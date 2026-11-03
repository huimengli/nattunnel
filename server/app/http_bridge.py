"""隧道桥: /tunnel/{tid} 的公网侧直连 —— 纯 HTTP 直转 + WebSocket 握手桥接。

(2026-11-03) 隧道协议 proto:
  http —— 浏览器直开短链: 纯 HTTP 逐请求直转发到内网服务(HTML `<base>` 注入);
          同一入口的 WS 握手也桥接到内网服务(页面里的 WebSocket 可用);
  tcp  —— 原生 TCP 字节流: 声明 WS 子协议 nattunnel-relay 的连接走 main.py 的帧协议裸流中继;
          未声明该子协议的 WS 握手由本模块桥接到内网 WS 服务;
  udp  —— 仅帧协议数据报中继(无 HTTP/WS 桥接语义)。

HTTP 直转语义:
  - 每个 HTTP 请求对应一条独立隧道流(多路并发各自独立);
  - 发往内网的请求强制 Connection: close, 响应结束即拆流;
  - 响应体结束由 Content-Length / chunked 终结符(0\r\n\r\n) / 目标关闭(FIN) 判定;
  - HTML 响应在 <head> 后注入 <base href="/tunnel/{tid}/">(Content-Length 同步 + 注入长),
    使相对引用(含 JS 运行时 fetch)落在隧道前缀内。

WS 握手桥接语义:
  - 握手请求(方法/路径/查询/头)原样转发到内网服务: 保留 Sec-WebSocket-Key/Version/Protocol
    (内网服务需要 key 计算 accept), 剥掉 Sec-WebSocket-Extensions —— 桥接层不做 permessage-deflate;
  - 内网服务返回 101 后, 本服务用**同一个 Sec-WebSocket-Key**对浏览器完成握手(uvicorn 计算的
    Sec-WebSocket-Accept 与内网服务一致), 并把内网协商到的 Sec-WebSocket-Protocol 原样回带;
  - 握手后是双向字节管道: 浏览器 WS 帧载荷 <-> 内网 socket 字节, 控制帧(ping/pong/close)在桥接层
    按 RFC 6455 客户端语义处理(桥接->内网帧带掩码, 内网->桥接帧不掩码);
  - 隧道不存在/停用 -> 404; LAN 离线 -> 502; 无握手响应/超时 -> 504; 非 101 -> 原状态码拒绝。
    (拒绝用 ASGI 扩展 websocket.http.response, 浏览器 WS 客户端得到非 101 的 HTTP 响应)
"""
import asyncio
import json
import logging
import re
import secrets
from urllib.parse import quote

from .database import SessionLocal
from .models import Tunnel
from .relay import (
    RELAY_SUBPROTOCOL,
    T_CLOSE,
    T_DATA,
    T_FIN,
    T_NEW,
    alloc_peer,
    hub,
    make_frame,
    split_frame,
)

log = logging.getLogger("nattunnel.bridge")

_TID_RE = re.compile(r"^/tunnel/([A-Za-z0-9]{8})(/.*)?$")
_CHUNK = 64 * 1024
_HEADER_TIMEOUT = 20.0

# hop-by-hop / WS 握手相关头: 纯 HTTP 直转时不转发给内网服务
_STRIP_REQ_HEADERS = {
    b"connection", b"keep-alive", b"proxy-connection", b"upgrade",
    b"sec-websocket-key", b"sec-websocket-version",
    b"sec-websocket-protocol", b"sec-websocket-extensions",
}

# WS 握手桥接: 保留 Sec-WebSocket-Key/Version/Protocol(内网需要), 只剥代理头与扩展
_WS_STRIP_HEADERS = {b"host", b"connection", b"upgrade", b"proxy-connection", b"sec-websocket-extensions"}


def _header(scope, name: str) -> str:
    """ASGI scope 头(键为原始大小写): 取第一个匹配值。"""
    want = name.lower().encode()
    for k, v in scope.get("headers", []):
        if k.lower() == want:
            return v.decode("latin-1")
    return ""


def _tunnel_info(tid: str) -> dict | None:
    """隧道配置的只读快照(会话关闭后仍可用, 避免 DetachedInstanceError)。"""
    with SessionLocal() as db:
        t = db.query(Tunnel).filter_by(tunnel_id=tid).first()
        if t is None:
            return None
        return {
            "tunnel_id": t.tunnel_id,
            "proto": t.proto,
            "local_port": int(t.local_port),
            "local_target_host": (t.local_target_host or "127.0.0.1").strip(),
            "enabled": bool(t.enabled),
        }


class _QueuePub:
    """HTTP / WS 桥接流的伪公网侧入口。

    main.py 的 LAN 中继循环把响应帧(含 T_FIN/T_CLOSE)经 send_bytes() 放入本队列;
    close() 由 main.py 掉线清理调用, 这里投放 None 哨兵唤醒桥接。
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    async def send_bytes(self, data: bytes) -> None:
        await self.queue.put(data)

    async def close(self, *args, **kwargs) -> None:
        await self.queue.put(None)


class _RespParser:
    """HTTP/1 响应增量解析: 提取状态行+头, 并跟踪响应体结束(CL / chunked)。"""

    def __init__(self) -> None:
        self.buf = b""
        self.headers_done = False
        self.complete = False
        self.status = 502
        self.headers: list = []
        self._remaining: int | None = None
        self._chunked = False
        self._tail = b""
        # HTML <base> 注入(仅 text/html 响应): 让子路径代理下的相对引用落在隧道前缀内
        self.base_href: str | None = None
        self._base_injected = False
        self._inj_gave_up = False

    def _injection_tag(self) -> bytes:
        return b'<base href="' + self.base_href.encode() + b'">'

    def scan_injection(self) -> None:
        """字节缓冲有更新后尝试确定注入点(已决定或非 HTML 时为 no-op)。"""
        if self.base_href is None or self._base_injected or self._inj_gave_up:
            return
        buf = self.buf
        m = re.search(rb"<head[^>]*>", buf, re.IGNORECASE)
        if m is None:
            if len(buf) > 65536:
                self._inj_gave_up = True  # 找不到 <head>: 原样放行
            return
        if re.search(rb"<base[\s>/]", buf[: m.end()], re.IGNORECASE):
            self._inj_gave_up = True  # 文档自带 <base>, 不覆盖
            return
        self.buf = buf[: m.end()] + self._injection_tag() + buf[m.end():]
        self._base_injected = True

    def feed(self, data: bytes) -> None:
        self.buf += data
        self.scan_injection()

    def injection_decided(self) -> bool:
        return self.base_href is None or self._base_injected or self._inj_gave_up

    def injected_len(self) -> int:
        return len(self._injection_tag()) if self._base_injected else 0

    def note_injected(self, n: int) -> None:
        """注入已完成: CL 模式下同步可发送体长度(注入字节也要计入)。"""
        if n and not self._chunked and self._remaining is not None:
            self._remaining += n

    def force_decide_injection(self) -> None:
        if not self.injection_decided():
            self._inj_gave_up = True

    async def await_headers(self) -> bool:
        """头缓冲完整后解析状态行与头; 返回是否完成。"""
        idx = self.buf.find(b"\r\n\r\n")
        if idx < 0:
            return False
        head, self.buf = self.buf[:idx], self.buf[idx + 4:]
        lines = head.decode("latin-1").split("\r\n")
        m = re.match(r"HTTP/\d\.\d\s+(\d{3})", lines[0])
        if not m:
            raise ValueError(f"非法响应起始行: {lines[0]!r}")
        self.status = int(m.group(1))
        for line in lines[1:]:
            k, sep, v = line.partition(":")
            if not sep or not k.strip():
                continue
            lk = k.strip().lower().encode()
            bv = v.strip().encode()
            if lk in (b"connection", b"keep-alive"):
                continue
            self.headers.append((lk, bv))
        for lk, bv in self.headers:
            if lk == b"content-length":
                try:
                    self._remaining = int(bv)
                except ValueError:
                    self._remaining = None
            elif lk == b"transfer-encoding" and b"chunked" in bv:
                self._chunked = True
        self.headers_done = True
        return True

    def is_complete(self) -> bool:
        if self.complete:
            return True
        if not self.headers_done:
            return False
        if self._chunked:
            return self._tail.endswith(b"0\r\n\r\n")
        if self._remaining is not None:
            return self._remaining <= 0
        return False

    def pop_body(self) -> bytes:
        """返回当前可发送的响应体字节(可能为空); complete 标志表示结束。"""
        out = b""
        if not self._chunked and self._remaining is not None:
            n = min(len(self.buf), self._remaining)
            out, self.buf = self.buf[:n], self.buf[n:]
            self._remaining -= n
            if self._remaining == 0:
                self.complete = True
        else:
            out = self.buf
            self.buf = b""
            self._tail = (self._tail + out)[-8:]
            if self._chunked and self._tail.endswith(b"0\r\n\r\n"):
                self.complete = True
        return out


# ------------------------------------------------------------ WS 桥接编解码

_OP_CONT, _OP_TEXT, _OP_BIN, _OP_CLOSE, _OP_PING, _OP_PONG = 0, 1, 2, 8, 9, 10


class WSCodec:
    """桥接层对内网服务一侧的最小 WebSocket 客户端编解码(RFC 6455, 无扩展)。

    桥接层是 WS 客户端: 发出**带掩码**帧; 内网服务端发来的帧**不掩码**。
    feed() 返回事件列表: ("bytes", bytes) / ("text", str) / ("ping", bytes) / ("close", code, reason)
    """

    def __init__(self) -> None:
        self.buf = b""
        self._frag = bytearray()
        self._frag_op: int | None = None
        self.closed = False

    def frame(self, opcode: int, payload: bytes = b"") -> bytes:
        n = len(payload)
        b0 = 0x80 | opcode  # FIN=1
        if n < 126:
            head = bytes([b0, 0x80 | n])
        elif n < 65536:
            head = bytes([b0, 0x80 | 126]) + n.to_bytes(2, "big")
        else:
            head = bytes([b0, 0x80 | 127]) + n.to_bytes(8, "big")
        mask = secrets.token_bytes(4)  # token_bytes 兼容无 randbytes 的解释器
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return head + mask + masked

    def text(self, data: str) -> bytes:
        return self.frame(_OP_TEXT, data.encode("utf-8"))

    def binary(self, data: bytes) -> bytes:
        return self.frame(_OP_BIN, data)

    def pong(self, payload: bytes) -> bytes:
        return self.frame(_OP_PONG, payload)

    def close(self, code: int = 1000, reason: str = "") -> bytes:
        self.closed = True
        payload = code.to_bytes(2, "big") + reason.encode("utf-8")[:120]
        return self.frame(_OP_CLOSE, payload)

    def feed(self, data: bytes) -> list:
        """喂入内网服务方向的字节, 返回完整事件(分片/控制帧处理, 不完整时留在缓冲)。"""
        self.buf += data
        events = []
        while True:
            b = self.buf
            if len(b) < 2:
                break
            fin = b[0] & 0x80
            opcode = b[0] & 0x0F
            if b[0] & 0x70:
                raise ValueError("WS 帧保留位(RSV)非 0 — 桥接层不支持扩展")
            if b[1] & 0x80:
                raise ValueError("内网服务发来的 WS 帧带掩码(协议错误)")
            ln = b[1] & 0x7F
            need = 2
            if ln == 126:
                if len(b) < 4:
                    break
                ln = int.from_bytes(b[2:4], "big")
                need = 4
            elif ln == 127:
                if len(b) < 10:
                    break
                ln = int.from_bytes(b[2:10], "big")
                need = 10
            if opcode >= 8 and ln > 125:
                raise ValueError("WS 控制帧载荷过长")
            if len(b) < need + ln:
                break
            payload = b[need:need + ln]
            self.buf = b[need + ln:]
            if opcode in (_OP_TEXT, _OP_BIN, _OP_CONT):
                if opcode == _OP_CONT:
                    if self._frag_op is None:
                        raise ValueError("WS 分片序异常(无起始数据帧)")
                elif self._frag_op is not None:
                    raise ValueError("WS 分片未完成时收到新数据帧")
                else:
                    self._frag_op = opcode
                self._frag += payload
                if fin:
                    op = self._frag_op
                    out = bytes(self._frag)
                    self._frag = bytearray()
                    self._frag_op = None
                    events.append(("text", out.decode("utf-8", "replace")) if op == _OP_TEXT else ("bytes", out))
            elif opcode == _OP_PING:
                events.append(("ping", bytes(payload)))
            elif opcode == _OP_CLOSE:
                code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else 1000
                reason = payload[2:].decode("utf-8", "replace")
                self.closed = True
                events.append(("close", code, reason))
                break
            # PONG: 内网服务对桥接层 ping 的回应, 忽略
        return events


def _build_ws_handshake(scope, target_path: str, local_port: int) -> bytes:
    """把浏览器的 WS 握手改写为发往内网服务的 HTTP/1.1 升级请求。"""
    tpath = quote(target_path, safe="/%")
    qs = scope.get("query_string") or b""
    if qs:
        tpath += "?" + qs.decode("latin-1")
    lines = [f"GET {tpath} HTTP/1.1"]
    have_key = False
    have_ver = False
    for k, v in scope.get("headers", []):
        lk = k.lower()
        if lk in _WS_STRIP_HEADERS:
            continue
        ks = k.decode("latin-1")
        vs = v.decode("latin-1")
        if lk == b"sec-websocket-key":
            have_key = True
        elif lk == b"sec-websocket-version":
            have_ver = True
        lines.append(f"{ks}: {vs}")
    if not have_key:
        raise ValueError("WS 握手缺少 Sec-WebSocket-Key")
    lines.append(f"Host: 127.0.0.1:{local_port}")
    lines.append("Connection: Upgrade")
    lines.append("Upgrade: websocket")
    if not have_ver:
        lines.append("Sec-WebSocket-Version: 13")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def _parse_ws_response(buf: bytes):
    """解析内网服务的 WS 握手响应; 未完整返回 None, 否则 (status, headers, 剩余字节)。"""
    idx = buf.find(b"\r\n\r\n")
    if idx < 0:
        return None
    head = buf[:idx].decode("latin-1")
    rest = buf[idx + 4:]
    lines = head.split("\r\n")
    m = re.match(r"HTTP/\d\.\d\s+(\d{3})", lines[0])
    if not m:
        raise ValueError(f"非法 WS 握手响应起始行: {lines[0]!r}")
    status = int(m.group(1))
    hdrs = {}
    for line in lines[1:]:
        k, sep, v = line.partition(":")
        if sep:
            hdrs[k.strip().lower()] = v.strip()
    return status, hdrs, rest


async def _send_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _ws_reject(send, status: int, detail: str) -> None:
    """握手完成前拒绝连接: 用 ASGI 扩展 websocket.http.response 给出 HTTP 响应。"""
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "websocket.http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "websocket.http.response.body", "body": body})


async def _send_lan(lan, type_: int, pid: int, payload: bytes) -> None:
    if not payload:
        await lan.send_bytes(make_frame(type_, pid, b""))
        return
    for i in range(0, len(payload), _CHUNK):
        await lan.send_bytes(make_frame(type_, pid, payload[i:i + _CHUNK]))


async def _next_frame(qpub, receive, timeout=None):
    """等待三种事件之一: 响应帧 / 客户端断开 / 超时。返回 (kind, item)。"""
    loop = asyncio.get_running_loop()
    get_q = asyncio.ensure_future(qpub.queue.get())
    get_d = asyncio.ensure_future(receive())
    waiter = {get_q, get_d}
    timer_fut = None
    timer_handle = None
    if timeout is not None:
        timer_fut = loop.create_future()
        waiter.add(timer_fut)
        timer_handle = loop.call_later(timeout, timer_fut.set_result, True)
    try:
        done, pending = await asyncio.wait(waiter, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if timer_handle is not None and timer_fut is not None and not timer_fut.done():
            timer_handle.cancel()
        for t in pending:
            t.cancel()
    if get_q in done:
        return "frame", get_q.result()
    if timer_fut is not None and timer_fut in done:
        return "timeout", None
    return "client-gone", None


async def _emit_ws_event(ev, send, codec, lan, pid) -> None:
    """把解码出的事件投递给浏览器(必要时对内网回一帧)。"""
    kind = ev[0]
    if kind == "bytes":
        await send({"type": "websocket.send", "bytes": ev[1]})
    elif kind == "text":
        await send({"type": "websocket.send", "text": ev[1]})
    elif kind == "ping":
        await _send_lan(lan, T_DATA, pid, codec.pong(ev[1]))
    elif kind == "close":
        code = ev[1] if ev[1] in (1000, 1001) else 1011
        await send({"type": "websocket.close", "code": code, "reason": ev[2][:120]})


async def _ws_pump_client(lan, pid, codec, receive) -> None:
    """浏览器 -> 内网: WS 帧载荷作为字节流入隧。"""
    while True:
        msg = await receive()
        if msg["type"] == "websocket.disconnect":
            try:
                await _send_lan(lan, T_DATA, pid, codec.close(1000))
            except Exception:
                pass
            return
        data = msg.get("bytes")
        if data is None:
            txt = msg.get("text")
            if txt is None:
                continue
            out = codec.text(txt)
        else:
            out = codec.binary(data)
        await _send_lan(lan, T_DATA, pid, out)


async def _ws_pump_server(qpub, lan, pid, codec, send) -> None:
    """内网 -> 浏览器: 字节流解码为 WS 帧投递给浏览器。"""
    while True:
        item = await qpub.queue.get()
        if item is None:  # LAN 离线哨兵
            await send({"type": "websocket.close", "code": 1011, "reason": "隧道链路中断"})
            return
        type_, fpid, payload = split_frame(item)
        if fpid != pid:
            continue
        if type_ == T_DATA:
            for ev in codec.feed(payload):
                await _emit_ws_event(ev, send, codec, lan, pid)
                if ev[0] == "close":
                    return
        elif type_ in (T_FIN, T_CLOSE):
            await send({"type": "websocket.close", "code": 1011, "reason": "内网连接结束"})
            return


async def _ws_bridge(scope, receive, send, tid: str, target_path: str, info: dict) -> None:
    """公网侧 WS 握手桥接到内网 WS 服务(握手 101 + 数据帧双向透明)。"""
    room = hub.room(tid, info["proto"])
    lan = room.lan
    if lan is None:
        await _ws_reject(send, 502, "LAN client offline")
        return
    pid = alloc_peer(room)
    qpub = _QueuePub()
    room.pubs[pid] = qpub
    codec = WSCodec()
    accepted = False
    try:
        first = await receive()  # websocket.connect
        if first["type"] == "websocket.disconnect":
            return

        # 1) 内网侧建流 + 转发握手请求(客户端建连竞态由客户端 open_tasks 处理)
        await _send_lan(lan, T_NEW, pid, b"")
        req_bytes = _build_ws_handshake(scope, target_path, info["local_port"])
        log.debug("ws bridge %s%s 转发握手(%d B): %s", tid, target_path, len(req_bytes),
                 req_bytes.decode("latin-1").replace("\r\n", " | "))
        await _send_lan(lan, T_DATA, pid, req_bytes)

        # 2) 等内网服务的握手响应(限时); 浏览器此时不会发数据
        buf = b""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _HEADER_TIMEOUT
        parsed = None
        while parsed is None:
            timeout = max(0.0, deadline - loop.time())
            kind, item = await _next_frame(qpub, receive, timeout=timeout)
            if kind == "timeout":
                break
            if kind == "client-gone":
                return  # 浏览器在握手前离开
            if item is None:  # LAN 离线哨兵
                break
            type_, fpid, payload = split_frame(item)
            if fpid != pid:
                continue
            if type_ == T_DATA:
                buf += payload
                parsed = _parse_ws_response(buf)
            elif type_ in (T_FIN, T_CLOSE):
                break

        if parsed is None:
            await _ws_reject(send, 504, "内网服务无 WS 握手响应(超时/链路中断)")
            return
        log.debug("ws bridge %s%s 内网响应头(%d B): %r", tid, target_path, len(buf), buf[:240])
        status, hdrs, rest = parsed
        if status != 101:
            await _ws_reject(send, status, f"内网服务未切换 WebSocket (HTTP {status})")
            return

        # 3) 用内网协商结果对浏览器完成握手(key 同一个 => Accept 值一致)
        subprotocol = hdrs.get("sec-websocket-protocol") or None
        await send({"type": "websocket.accept", "subprotocol": subprotocol})
        accepted = True
        log.info("ws bridge %s%s: 101 (peer_id=%s, subprotocol=%s, target=%s:%s)",
                 tid, target_path, pid, subprotocol, info["local_target_host"], info["local_port"])

        # 4) 握手响应之后已到达的字节先解码投递
        for ev in codec.feed(rest):
            await _emit_ws_event(ev, send, codec, lan, pid)
            if ev[0] == "close":
                return

        # 5) 双向管道
        t_client = asyncio.create_task(_ws_pump_client(lan, pid, codec, receive))
        t_server = asyncio.create_task(_ws_pump_server(qpub, lan, pid, codec, send))
        _, pending = await asyncio.wait({t_client, t_server}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except Exception as exc:
        log.warning("ws bridge %s%s 失败: %s", tid, target_path, exc)
        if not accepted:
            try:
                await _ws_reject(send, 502, "WS 桥接失败")
            except Exception:
                pass
    finally:
        if room.pubs.get(pid) is qpub:
            room.pubs.pop(pid, None)
            lan = room.lan
            if lan is not None:
                try:
                    await lan.send_bytes(make_frame(T_CLOSE, pid))
                except Exception:
                    pass
        if room.lan is None and not room.pubs:
            hub.drop(tid)


async def _bridge(scope, receive, send, tid: str, target_path: str) -> None:
    started = False
    room = None
    pid = None
    qpub = None
    try:
        with SessionLocal() as db:
            tunnel = db.query(Tunnel).filter_by(tunnel_id=tid).first()
            if tunnel is None or not tunnel.enabled:
                await _send_json(send, 404, "Not Found")
                return
            if tunnel.proto != "http":
                await _send_json(send, 400, "HTTP 直转仅支持 http 隧道(该隧道是 %s)" % tunnel.proto)
                return
            local_port = tunnel.local_port

        room = hub.get(tid)
        if room is None or room.lan is None:
            await _send_json(send, 503, "LAN client offline")
            return

        pid = alloc_peer(room)
        qpub = _QueuePub()
        room.pubs[pid] = qpub

        # 1) LAN 侧打开本地流(客户端内部等待连接完成后再消费帧, 无竞态)
        if room.lan is not None:
            await _send_lan(room.lan, T_NEW, pid, b"")

        # 2) 请求行 + 头(Host 用 127.0.0.1:<端口>: 客户端就连接在本地服务上)
        head = _build_request_head(scope, target_path, local_port)
        await _send_lan(room.lan, T_DATA, pid, head)

        # 3) 请求体流式入隧(客户端中途断开则中止)
        aborted = False
        while True:
            msg = await receive()
            if msg["type"] == "http.request":
                body = msg.get("body") or b""
                if body:
                    await _send_lan(room.lan, T_DATA, pid, body)
                if not msg.get("more_body", False):
                    break
            elif msg["type"] == "http.disconnect":
                aborted = True
                break

        # 4) 等响应头(限时); LAN 离线/客户端断开即中止
        parser = _RespParser()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _HEADER_TIMEOUT
        while not parser.headers_done and not aborted:
            timeout = max(0.0, deadline - loop.time())
            kind, item = await _next_frame(qpub, receive, timeout=timeout)
            if kind == "timeout":
                break
            if kind == "client-gone":
                aborted = True
                break
            if item is None:  # LAN 离线哨兵
                break
            type_, fpid, payload = split_frame(item)
            if fpid != pid:
                continue
            if type_ == T_DATA:
                parser.feed(payload)
                if await parser.await_headers():
                    break
            elif type_ in (T_FIN, T_CLOSE):
                parser.complete = True
                break

        if not parser.headers_done:
            if not aborted:
                await _send_json(
                    send, 502 if parser.complete else 504,
                    "隧道链路中断" if parser.complete else "本地服务响应超时",
                )
            return

        # 4b) HTML 响应: 先确定 <base> 注入(Content-Length 需计入注入字节), 再发响应头。
        # 仅在非编码且非 chunked 时注入(chunked 有显式块大小, 插字节会破坏帧)。
        _encoded = any(k == b"content-encoding" for k, v in parser.headers)
        if (not _encoded and not parser._chunked
                and any(k == b"content-type" and b"text/html" in v for k, v in parser.headers)):
            parser.base_href = f"/tunnel/{tid}/"
            parser.scan_injection()
            while not parser.injection_decided() and not aborted and not parser.complete:
                kind, item = await _next_frame(qpub, receive)
                if kind == "client-gone":
                    aborted = True
                    break
                if item is None:  # LAN 离线
                    break
                type_, fpid, payload = split_frame(item)
                if fpid != pid:
                    continue
                if type_ == T_DATA:
                    parser.feed(payload)
                elif type_ in (T_FIN, T_CLOSE):
                    parser.complete = True
                    break
            parser.force_decide_injection()
            if parser.injected_len() > 0:
                parser.note_injected(parser.injected_len())
                for i, (k, v) in enumerate(parser.headers):
                    if k == b"content-length":
                        try:
                            parser.headers[i] = (k, str(int(v) + parser.injected_len()).encode())
                        except ValueError:
                            pass

        started = True
        await send({
            "type": "http.response.start",
            "status": parser.status,
            "headers": parser.headers,
        })

        # 5) 流式回传响应体直到结束
        while True:
            chunk = parser.pop_body()
            if chunk or parser.complete:
                await send({"type": "http.response.body", "body": chunk,
                           "more_body": not parser.complete})
            if parser.complete or aborted:
                break
            kind, item = await _next_frame(qpub, receive)
            if kind == "client-gone":
                aborted = True
                break
            if item is None:  # LAN 离线
                break
            type_, fpid, payload = split_frame(item)
            if fpid == pid and type_ == T_DATA:
                parser.feed(payload)
            elif fpid == pid and type_ in (T_FIN, T_CLOSE):
                parser.complete = True
    except Exception as exc:
        log.warning("http bridge %s%s 失败: %s", tid, target_path, exc)
        if not started:
            try:
                await _send_json(send, 502, "转发失败")
            except Exception:
                pass
    finally:
        if room is not None and pid is not None and qpub is not None:
            if room.pubs.get(pid) is qpub:
                room.pubs.pop(pid, None)
                lan = room.lan
                if lan is not None:
                    try:
                        await lan.send_bytes(make_frame(T_CLOSE, pid))
                    except Exception:
                        pass
            if room.lan is None and not room.pubs:
                hub.drop(tid)


def _build_request_head(scope, target_path: str, local_port: int) -> bytes:
    tpath = quote(target_path, safe="/%")
    qs = scope.get("query_string") or b""
    if qs:
        tpath += "?" + qs.decode("latin-1")
    lines = [f"{scope['method']} {tpath} HTTP/1.1"]
    for k, v in scope.get("headers", []):
        lk = k.lower()
        if lk in _STRIP_REQ_HEADERS or lk == b"host":
            continue
        lines.append(f"{k.decode('latin-1')}: {v.decode('latin-1')}")
    lines.append(f"Host: 127.0.0.1:{local_port}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class HttpBridgeMiddleware:
    """拦截 /tunnel/{id}: 纯 HTTP 请求直转、WS 握手桥接; 其余交给原应用。

    公网侧 WS 握手的三种归属:
      - 携带 Bearer(属主/管理员) => LAN 侧, 交给 main.py 的 WS 中继;
      - 子协议 nattunnel-relay => 帧协议裸流, 交给 main.py 的 WS 中继;
      - 其余 => 桥接到内网 WS 服务(http/tcp 隧道), udp 隧道拒绝。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        m = _TID_RE.match(scope.get("path", ""))
        if m is None or stype not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        tid = m.group(1)
        target = m.group(2) or "/"

        if stype == "websocket":
            if _header(scope, "authorization").lower().startswith("bearer "):
                await self.app(scope, receive, send)   # LAN 侧 -> 中继
                return
            info = _tunnel_info(tid)
            if info is None or not info["enabled"]:
                await _ws_reject(send, 404, "隧道不存在或已停用")
                return
            if RELAY_SUBPROTOCOL in (scope.get("subprotocols") or []):
                await self.app(scope, receive, send)   # 帧协议裸流 -> 中继
                return
            if info["proto"] == "udp":
                await _ws_reject(
                    send, 400,
                    f"udp 隧道不支持 WebSocket 桥接(裸流请用子协议 {RELAY_SUBPROTOCOL})",
                )
                return
            await _ws_bridge(scope, receive, send, tid, target, info)
            return

        # http: 带 Upgrade: websocket 的普通请求(非真握手) 交给应用; 其余直转
        if _header(scope, "upgrade").lower() == "websocket":
            await self.app(scope, receive, send)
            return
        await _bridge(scope, receive, send, tid, target)


# 保留旧导出名(测试/自检脚本按此名引用中间件)
HTTP_BRIDGE_WS_SUBPROTOCOL = RELAY_SUBPROTOCOL