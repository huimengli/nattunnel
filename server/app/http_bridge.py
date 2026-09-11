"""Pure-HTTP 直转桥: /tunnel/{tid} 接受普通 HTTP 请求(无 WebSocket Upgrade)。

浏览器可以直接打开 http(s)://服务器/tunnel/<短链>[子路径]: 请求被转换成一条隧道流
转发到本地服务, 响应按流回传。WebSocket 握手请求不受本模块影响。

语义:
  - 仅 proto=tcp 的隧道支持直转(udp 无连接语义);
  - 每个 HTTP 请求对应一条独立隧道流(多路并发各自独立);
  - 发往本地服务的请求强制加 Connection: close, 响应结束即拆流;
  - 响应体结束由 Content-Length / chunked 终结符(0\\r\\n\\r\\n) / 目标关闭(FIN) 判定;
  - HTML 响应在 <head> 后注入 <base href="/tunnel/{tid}/">(Content-Length 同步+注入长),
    使相对引用(含 JS 运行时 fetch)落在隧道前缀下, 子路径部署的 Web 应用可直接浏览。
"""
import asyncio
import json
import logging
import re
from urllib.parse import quote

from .database import SessionLocal
from .models import Tunnel
from .relay import T_CLOSE, T_DATA, T_FIN, T_NEW, hub, make_frame, split_frame

log = logging.getLogger("nattunnel.httpbridge")

_TID_RE = re.compile(r"^/tunnel/([A-Za-z0-9]{8})(/.*)?$")
_CHUNK = 64 * 1024
_HEADER_TIMEOUT = 20.0

# hop-by-hop / WS 握手相关头: 不转发给目标服务
_STRIP_REQ_HEADERS = {
    b"connection", b"keep-alive", b"proxy-connection", b"upgrade",
    b"sec-websocket-key", b"sec-websocket-version",
    b"sec-websocket-protocol", b"sec-websocket-extensions",
}


class _QueuePub:
    """HTTP 流的伪公网侧入口。

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


class HttpBridgeMiddleware:
    """拦截 /tunnel/{id} 的纯 HTTP 请求; WebSocket 握手放行给原中继。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            upgrade = b""
            for k, v in scope.get("headers", []):
                if k.lower() == b"upgrade":
                    upgrade = v.lower()
            if upgrade != b"websocket":
                m = _TID_RE.match(scope["path"])
                if m is not None:
                    await _bridge(scope, receive, send, m.group(1), m.group(2) or "/")
                    return
        await self.app(scope, receive, send)


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
            if tunnel.proto != "tcp":
                await _send_json(send, 400, "HTTP 直转仅支持 tcp 隧道")
                return
            local_port = tunnel.local_port

        room = hub.get(tid)
        if room is None or room.lan is None:
            await _send_json(send, 503, "LAN client offline")
            return

        pid = room.next_peer
        room.next_peer += 1
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
