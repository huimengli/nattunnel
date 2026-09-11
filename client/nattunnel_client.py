#!/usr/bin/env python3
"""
nattunnel 客户端 — 将无公网IP电脑的本地端口经服务器隧道映射到公网。

启动(authtoken 必须提供, 两种途径):
    a) 命令行:  nattunnel-client.exe -a <token> [-s <server>]
    b) 控制台:  启动后按提示粘贴 token(token 从网页管理端 "认证令牌" 卡片获取)

流程:
  1) GET /api/me                  校验 authtoken(JWT)
  2) GET /api/tunnels/{id}        握手拉取端口/协议/带宽配置
  3) WS  /tunnel/{id} (Bearer)    建立隧道, 按帧协议转发 TCP/UDP
  4) T_CONFIG 热更新: 服务端改配置后实时下发(端口/带宽即生效, 协议变更自动重连)

帧格式(与后端一致): [type 1B][peer_id 4B BE][payload]
  0x01 NEW_STREAM(server->lan)  0x02 DATA(tcp)  0x03 FIN  0x04 CLOSE
  0x05 HELLO(server->pub)       0x21 CONFIG(server->lan, JSON)
  0x41 DATA(udp)

构建 exe:  build.bat (PyInstaller --onefile)
"""
import argparse
import asyncio
import json
import logging
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding

# ------------------------------------------------------------------ 帧协议

T_NEW = 0x01
T_DATA = 0x02
T_FIN = 0x03
T_CLOSE = 0x04
T_CONFIG = 0x21   # server->lan: JSON{proto, local_port, bandwidth_kbps} 热更新
U_DATA = 0x41


def build_frame(type_: int, peer_id: int, payload: bytes = b"") -> bytes:
    return bytes([type_]) + peer_id.to_bytes(4, "big") + payload


def split_frame(data: bytes):
    if len(data) < 5:
        raise ValueError("frame too short")
    return data[0], int.from_bytes(data[1:5], "big"), data[5:]


# ------------------------------------------------------------------ 配置

class Config:
    def __init__(self, path: Path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.server = str(raw["server"]).rstrip("/")
        self.tunnel_id = str(raw["tunnel_id"])
        self.local_target_host = str(raw.get("local_target_host", "127.0.0.1"))
        self.verify_ssl = bool(raw.get("verify_ssl", True))

    @property
    def ws_url(self) -> str:
        base = self.server
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        return f"{base}/tunnel/{self.tunnel_id}"


def default_config_path() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller exe
        return Path(sys.executable).with_name("config.json")
    return Path(__file__).resolve().with_name("config.json")


log = logging.getLogger("nattunnel.client")


# ------------------------------------------------------------------ HTTP(token 校验)

class HttpError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def http_json(cfg: Config, path: str, method: str = "GET", body=None, token: str = None) -> dict:
    url = cfg.server + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "nattunnel-client/0.1")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    ctx = None
    if url.startswith("https://"):
        ctx = ssl.create_default_context()
        if not cfg.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = str(e)
        raise HttpError(e.code, detail) from None


def verify_token(cfg: Config, token: str) -> dict:
    """用 Bearer token 调 /api/me 校验有效性; 返回 {username, role}。"""
    return http_json(cfg, "/api/me", token=token.strip())


def tunnel_config(cfg: Config, token: str) -> dict:
    out = http_json(cfg, f"/api/tunnels/{cfg.tunnel_id}", token=token)
    for field in ("proto", "local_port"):
        if field not in out:
            raise RuntimeError(f"tunnel config missing '{field}'")
    return out


# ------------------------------------------------------------------ 限流

class TokenBucket:
    """共享令牌桶, 限制隧道总吞吐(kbps; 0=不限)。"""

    def __init__(self, kbps: int):
        self.bps = (kbps * 1024.0 / 8.0) if kbps > 0 else 0.0
        self.capacity = max(self.bps, 65536)
        self.tokens = self.capacity
        self.last = time.monotonic()

    def set_bandwidth(self, kbps: int) -> None:
        """运行时改带宽(T_CONFIG)。"""
        self.bps = (kbps * 1024.0 / 8.0) if kbps > 0 else 0.0
        self.capacity = max(self.bps, 65536)
        self.tokens = min(self.tokens, self.capacity)

    async def consume(self, n: int) -> None:
        if self.bps <= 0 or n <= 0:
            return
        while True:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.bps)
            self.last = now
            if self.tokens >= n:
                self.tokens -= n
                return
            wait = (n - self.tokens) / self.bps
            await asyncio.sleep(min(wait, 0.25))


# ------------------------------------------------------------------ TCP 模式

async def run_tcp(ws, cfg: Config, tcfg: dict, bucket: TokenBucket):
    # live 可变配置: T_CONFIG 热更新 local_port/bandwidth; 协议变更则退出本会话重连
    live = {"host": cfg.local_target_host, "port": int(tcfg["local_port"])}
    streams = {}      # pid -> {"writer","queue","tasks"}
    open_tasks = {}   # pid -> 正在建连的 task(用于 T_DATA 早到的竞态)

    def apply_config(new: dict) -> bool:
        """应用服务端下发的配置; 返回 True 表示协议变更需重建会话。"""
        if "local_port" in new and int(new["local_port"]) != live["port"]:
            log.info("本地端口热更新: %s -> %s (新流生效)", live["port"], int(new["local_port"]))
            live["port"] = int(new["local_port"])
        if "bandwidth_kbps" in new:
            bucket.set_bandwidth(int(new["bandwidth_kbps"]))
            log.info("带宽热更新: %skbps", int(new["bandwidth_kbps"]))
        if new.get("proto") and new["proto"] != "tcp":
            return True
        return False

    async def close_stream(pid: int) -> None:
        open_tasks.pop(pid, None)
        st = streams.pop(pid, None)
        if st is None:
            return
        await st["queue"].put(None)
        for task in st["tasks"]:
            task.cancel()
        try:
            st["writer"].close()
            await st["writer"].wait_closed()
        except Exception:
            pass
        log.info("stream %s closed", pid)

    async def open_stream(pid: int) -> None:
        port = live["port"]
        try:
            reader, writer = await asyncio.open_connection(live["host"], port)
        except OSError as e:
            log.error("connect %s:%s failed: %s — 通知公网端拆除流", live["host"], port, e)
            try:
                await ws.send(build_frame(T_CLOSE, pid))
            except Exception:
                pass
            return
        queue = asyncio.Queue()
        st = {"writer": writer, "queue": queue, "tasks": []}
        streams[pid] = st
        log.info("stream %s opened -> %s:%s", pid, live["host"], port)

        async def pump_out():
            try:
                while True:
                    chunk = await reader.read(65536)
                    if not chunk:
                        break
                    await bucket.consume(len(chunk))
                    await ws.send(build_frame(T_DATA, pid, chunk))
                await ws.send(build_frame(T_FIN, pid))
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("stream %s pump_out: %s", pid, e)
            await close_stream(pid)

        async def pump_in():
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    writer.write(item)
                    await writer.drain()
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("stream %s pump_in: %s", pid, e)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

        st["tasks"].append(asyncio.create_task(pump_out()))
        st["tasks"].append(asyncio.create_task(pump_in()))

    try:
        while True:
            msg = await ws.recv()
            type_, pid, payload = split_frame(msg)
            if type_ == T_NEW:
                open_tasks[pid] = asyncio.create_task(open_stream(pid))
            elif type_ in (T_DATA, T_FIN):
                st = streams.get(pid)
                if st is None and pid in open_tasks:
                    # 数据帧早于建连完成到达(公网侧很快发数据): 先等建连结束
                    task = open_tasks[pid]
                    if not task.done():
                        await task
                    st = streams.get(pid)
                if st is not None:
                    await st["queue"].put(payload)
            elif type_ == T_CLOSE:
                task = open_tasks.pop(pid, None)
                if task is not None and not task.done():
                    task.cancel()
                await close_stream(pid)
            elif type_ == T_CONFIG:
                try:
                    new = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                if apply_config(new):
                    log.warning("协议已变更 — 重建隧道会话")
                    break
    finally:
        for pid in list(open_tasks):
            open_tasks[pid].cancel()
        for pid in list(streams):
            await close_stream(pid)


# ------------------------------------------------------------------ UDP 模式

class DatagramProto(asyncio.DatagramProtocol):
    """UDP socket, 以 future 队列暴露收到的数据报。"""

    def __init__(self):
        self._fut = None
        self._pending = deque()
        self.sockname = None

    def connection_made(self, transport):
        self.sockname = transport.get_extra_info("sockname")

    def datagram_received(self, data, addr):
        if self._fut is not None and not self._fut.done():
            self._fut.set_result((data, addr))
        else:
            self._pending.append((data, addr))

    def next(self):
        loop = asyncio.get_running_loop()
        if self._pending:
            item = self._pending.popleft()
            f = loop.create_future()
            f.set_result(item)
            return f
        self._fut = loop.create_future()
        return self._fut


async def run_udp(ws, cfg: Config, tcfg: dict, bucket: TokenBucket):
    # live 可变配置: T_CONFIG 热更新 local_port/bandwidth; 协议变更则退出本会话重连
    live = {"host": cfg.local_target_host, "port": int(tcfg["local_port"])}
    peers = {}  # pid -> (transport, proto, pump_task)

    def apply_config(new: dict) -> bool:
        if "local_port" in new and int(new["local_port"]) != live["port"]:
            log.info("本地端口热更新: %s -> %s", live["port"], int(new["local_port"]))
            live["port"] = int(new["local_port"])
        if "bandwidth_kbps" in new:
            bucket.set_bandwidth(int(new["bandwidth_kbps"]))
            log.info("带宽热更新: %skbps", int(new["bandwidth_kbps"]))
        if new.get("proto") and new["proto"] != "udp":
            return True
        return False

    async def add_peer(pid: int) -> None:
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(
            DatagramProto, local_addr=("127.0.0.1", 0)
        )
        entry = {"transport": transport, "proto": proto, "task": None}
        peers[pid] = entry

        async def pump():
            try:
                while True:
                    data, _addr = await proto.next()
                    if not data:
                        continue
                    await bucket.consume(len(data))
                    await ws.send(build_frame(U_DATA, pid, data))
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("udp peer %s pump: %s", pid, e)

        entry["task"] = asyncio.create_task(pump())
        log.info("udp peer %s bound 127.0.0.1:%s -> %s:%s",
                 pid, proto.sockname[1] if proto.sockname else "?", live["host"], live["port"])

    async def drop_peer(pid: int) -> None:
        entry = peers.pop(pid, None)
        if entry is None:
            return
        if entry["task"] is not None:
            entry["task"].cancel()
        try:
            entry["transport"].close()
        except Exception:
            pass
        log.info("udp peer %s closed", pid)

    try:
        while True:
            msg = await ws.recv()
            type_, pid, payload = split_frame(msg)
            if type_ == T_NEW:
                asyncio.create_task(add_peer(pid))
            elif type_ == U_DATA:
                entry = peers.get(pid)
                if entry is None:
                    continue
                if not payload:
                    continue
                await bucket.consume(len(payload))
                entry["transport"].sendto(payload, (live["host"], live["port"]))  # sendto 是同步方法
            elif type_ == T_CLOSE:
                await drop_peer(pid)
            elif type_ == T_CONFIG:
                try:
                    new = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                if apply_config(new):
                    log.warning("协议已变更 — 重建隧道会话")
                    break
    finally:
        for pid in list(peers):
            await drop_peer(pid)


# ------------------------------------------------------------------ 主循环

STOP = False


def _on_signal(signum, _frame):
    global STOP
    STOP = True
    log.info("收到信号 %s, 准备退出...", signum)


async def run(cfg: Config, token: str) -> None:
    backoff = 1.0
    while not STOP:
        try:
            tcfg = await asyncio.to_thread(tunnel_config, cfg, token)
            bucket = TokenBucket(int(tcfg.get("bandwidth_kbps") or 0))
            proto = tcfg["proto"]
            port = int(tcfg["local_port"])
            bw = int(tcfg.get("bandwidth_kbps") or 0)
            log.info("隧道配置: %s:%s 带宽=%skbps", proto, port, "不限" if bw == 0 else str(bw))

            headers = {"Authorization": f"Bearer {token}"}
            async with websockets.connect(
                cfg.ws_url,
                additional_headers=headers,
                max_size=1 << 20,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                log.info("隧道已建立: %s", cfg.ws_url)
                backoff = 1.0
                if proto == "tcp":
                    await run_tcp(ws, cfg, tcfg, bucket)
                else:
                    await run_udp(ws, cfg, tcfg, bucket)
        except HttpError as e:
            log.error("HTTP 错误 %s", e)
            if e.status in (401, 403):
                log.error("令牌失效或无该隧道权限 — 到网页管理端重新获取令牌并重启客户端")
        except websockets.exceptions.InvalidStatus as e:
            status = getattr(e, "status_code", None) or getattr(e, "status", "?")
            log.error("WS 被拒绝: status=%s (4004=隧道不存在/已停用)", status)
        except Exception:
            log.exception("链路异常(重连前记录完整堆栈)")

        if STOP:
            break
        delay = backoff
        log.info("%d 秒后重连...", int(delay))
        await asyncio.sleep(delay)
        backoff = min(backoff * 2, 30.0)
    log.info("客户端已停止")


def acquire_token(cfg: Config, cli_token: str = None) -> str:
    """获取有效 authtoken: 优先 --auth 参数, 否则控制台输入(可反复粘贴)。"""
    def validate(token: str):
        info = verify_token(cfg, token)  # HttpError on failure
        log.info("令牌有效 (user=%s role=%s)", info.get("username"), info.get("role"))
        return True

    if cli_token and cli_token.strip():
        try:
            validate(cli_token.strip())
            return cli_token.strip()
        except HttpError as e:
            print(f"--auth 提供的令牌无效: {e} — 请改为控制台输入", file=sys.stderr)

    while True:
        try:
            raw = input("请输入 authtoken (网页管理端 → “认证令牌” 卡片获取): ").strip()
        except EOFError:
            print("没有可用输入源且未提供有效令牌。请用 --auth <token> 启动, 或在交互式控制台输入。", file=sys.stderr)
            sys.exit(2)
        if not raw:
            continue
        try:
            validate(raw)
            return raw
        except HttpError as e:
            print(f"令牌无效或已过期: {e} — 请重新输入 (Ctrl+C 退出)", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="nattunnel client")
    parser.add_argument("--config", default=None, help="config.json 路径")
    parser.add_argument("-s", "--server", default=None,
                        help="公网服务器地址(http/https), 覆盖 config.json 中的 server")
    parser.add_argument("-a", "--auth", default=None,
                        help="authtoken(网页管理端获取); 省略则启动后在控制台输入")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)

    path = Path(args.config) if args.config else default_config_path()
    if not path.exists():
        print(f"配置文件不存在: {path}", file=sys.stderr)
        print("参考 config.example.json 创建, 或直接运行 --config <路径>", file=sys.stderr)
        sys.exit(1)
    cfg = Config(path)

    if args.server:
        base = args.server.strip()
        if not (base.startswith("http://") or base.startswith("https://")):
            print("-s 需要 http(s) 地址, 例如 https://www.xxx.com", file=sys.stderr)
            sys.exit(1)
        cfg.server = base.rstrip("/")

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _on_signal)
        except (ValueError, OSError):
            pass

    print("=" * 62)
    print("nattunnel client")
    print(f"  服务器 : {cfg.server}")
    print(f"  隧道   : /tunnel/{cfg.tunnel_id}")
    print("=" * 62)

    token = acquire_token(cfg, args.auth)
    try:
        asyncio.run(run(cfg, token))
    except KeyboardInterrupt:
        log.info("已中断")


if __name__ == "__main__":
    main()
