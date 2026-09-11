#!/usr/bin/env python3
"""端到端自测: 对已运行的 nattunnel 服务器执行完整链路验证。

前置: 服务器已在运行(默认 http://127.0.0.1:8100), 例如:
    cd server && set DATABASE_URL=sqlite:///./selftest.db && ..\\..\\.venv\\Scripts\\python -m uvicorn app.main:app --port 8100

步骤:
  A. TCP:  本地起 echo 服务 -> 建隧道 -> 真实客户端代码(进程内)建立 lan 侧
           -> 公网侧 WS 直连 -> 数据往返断言
  B. UDP:  同上(UDP echo)

用法(凭据不入库, 从参数或环境变量提供):
    set NATTUNNEL_TEST_PASSWORD=...        (或 --password)
    python tools/selftest.py [--server http://127.0.0.1:8100] [--user admin]
"""
import argparse
import http.server
import os
import threading
import asyncio
import base64
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
import nattunnel_client as client  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding  # noqa: E402
import websockets  # noqa: E402

PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))


def build_frame(type_, peer_id, payload=b""):
    return bytes([type_]) + peer_id.to_bytes(4, "big") + payload


def split_frame(data):
    if len(data) < 5:
        raise ValueError("frame too short")
    return data[0], int.from_bytes(data[1:5], "big"), data[5:]


# ------------------------------------------------------------- echo 服务

async def start_tcp_echo() -> tuple:
    async def handler(reader, writer):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


class UdpEcho(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.transport.sendto(data, addr)


async def start_udp_echo() -> tuple:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(UdpEcho, local_addr=("127.0.0.1", 0))
    return transport, transport.get_extra_info("sockname")[1]


# ------------------------------------------------------------- HTTP helpers

def http_json(server: str, path: str, method="GET", body=None, token=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def http_raw(server: str, path: str, method="GET", body=None, headers=None) -> tuple:
    """普通 HTTP 请求, 返回 (status, resp_headers, body_bytes)。"""
    req = urllib.request.Request(server + path, data=body, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


async def delete_tunnel(server: str, tid: str, token: str) -> None:
    if not tid:
        return
    try:
        req = urllib.request.Request(server + f"/api/tunnels/{tid}", method="DELETE")
        req.add_header("Authorization", f"Bearer {token}")
        await asyncio.get_running_loop().run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=15))
    except Exception as e:
        print(f"  [WARN] 删除隧道 {tid} 失败: {e}")


def login(server: str, username: str, password: str) -> str:
    pub = http_json(server, "/api/auth/public-key")["public_key"]
    key = serialization.load_pem_public_key(pub.encode())
    payload = json.dumps({"username": username, "password": password}).encode()
    enc = key.encrypt(payload, rsa_padding.PKCS1v15())
    out = http_json(server, "/api/login", method="POST",
                    body={"secure_payload": base64.b64encode(enc).decode()})
    return out["access_token"]


# ------------------------------------------------------------- 客户端(进程内)

async def wait_lan_online(server: str, tunnel_id: str, token: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            out = http_json(server, f"/api/tunnels/{tunnel_id}", token=token)
            if out.get("lan_online"):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.3)
    return False


def start_client(server: str, tunnel_id: str, token: str, include_tid: bool = True) -> asyncio.Task:
    """在进程内启动真实客户端代码(每阶段先复位 STOP 标志)。

    include_tid=False: config 不写 tunnel_id — 客户端须完全靠令牌绑定的隧道 ID。
    """
    client.STOP = False
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        cfg_raw = {"server": server}
        if include_tid:
            cfg_raw["tunnel_id"] = tunnel_id
        json.dump(cfg_raw, f)
        cfg_path = f.name
    c = client.Config(Path(cfg_path))
    if not include_tid:
        # 模拟 main(): /api/me 学令牌绑定的隧道 ID, 覆盖空配置
        info = client.verify_token(c, token)
        client.resolve_tunnel_id(c, info)
    return asyncio.create_task(client.run(c, token))


async def stop_client(task: asyncio.Task) -> None:
    client.STOP = True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=10)
    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
        task.cancel()


# ------------------------------------------------------------- 各阶段

async def phase_tcp(server: str, token: str) -> None:
    print(f"\n== 阶段 A: TCP 隧道往返 + 配置热更新 ==")
    echo_server, port = await start_tcp_echo()
    tid = None
    client_task = None
    try:
        out = http_json(server, "/api/tunnels", method="POST", token=token, body={
            "name": "selftest-tcp", "proto": "tcp", "local_port": port,
        })
        tid = out["tunnel_id"]
        client_task = start_client(server, tid, token)

        online = await wait_lan_online(server, tid, token)
        check("A1 客户端 lan 侧上线", online)
        if not online:
            return

        url = server.replace("http://", "ws://") + f"/tunnel/{tid}"
        async with websockets.connect(url, max_size=1 << 20) as ws:
            pid = None
            while pid is None:
                t, p, _ = split_frame(await asyncio.wait_for(ws.recv(), timeout=10))
                if t == 0x05:
                    pid = p
            check("A2 公网侧收到 HELLO(peer_id)", True)

            await ws.send(build_frame(0x02, pid, b"hello-nattunnel"))
            reply = None
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    t, p, payload = split_frame(await asyncio.wait_for(ws.recv(), timeout=deadline - time.time()))
                except TimeoutError:
                    break
                if p == pid and t == 0x02:
                    reply = payload
                    break
            check("A3 TCP 数据往返 (echo)", reply == b"hello-nattunnel", repr(reply))

            # 公网侧关闭流 -> lan 客户端应拆除本地连接(不崩即可)
            await ws.send(build_frame(0x04, pid))
            await asyncio.sleep(0.5)

        # --- A4/A5: 热更新 — API 改 local_port(客户端不重启), 新公网连接应转发到新端口。
        #     先关掉旧 echo: 若客户端未应用 T_CONFIG, 连旧端口会失败 -> A5 必然失败。
        echo_server.close()
        echo2, port2 = await start_tcp_echo()
        try:
            http_json(server, f"/api/tunnels/{tid}", method="PATCH", token=token, body={"local_port": port2})
            await asyncio.sleep(0.5)  # 等客户端应用 T_CONFIG
            check("A4 API 已把 local_port 改为 %d" % port2, True)
            async with websockets.connect(url, max_size=1 << 20) as ws2:
                pid2 = None
                while pid2 is None:
                    t, p, _ = split_frame(await asyncio.wait_for(ws2.recv(), timeout=10))
                    if t == 0x05:
                        pid2 = p
                await ws2.send(build_frame(0x02, pid2, b"live-update-probe"))
                reply2 = None
                deadline = time.time() + 8
                while time.time() < deadline:
                    try:
                        t, p, payload = split_frame(await asyncio.wait_for(ws2.recv(), timeout=deadline - time.time()))
                    except TimeoutError:
                        break
                    if p == pid2 and t == 0x02:
                        reply2 = payload
                        break
                check("A5 热更新后转发到新端口 (echo)", reply2 == b"live-update-probe", repr(reply2))
        finally:
            echo2.close()
            await asyncio.sleep(0)
    finally:
        if client_task is not None:
            await stop_client(client_task)
        await delete_tunnel(server, tid, token)
        try:
            echo_server.close()
        except Exception:
            pass
        await echo_server.wait_closed()


async def phase_udp(server: str, token: str) -> None:
    print(f"\n== 阶段 B: UDP 隧道往返 ==")
    echo_transport, port = await start_udp_echo()
    tid = None
    client_task = None
    try:
        out = http_json(server, "/api/tunnels", method="POST", token=token, body={
            "name": "selftest-udp", "proto": "udp", "local_port": port,
        })
        tid = out["tunnel_id"]
        client_task = start_client(server, tid, token)

        online = await wait_lan_online(server, tid, token)
        check("B1 客户端 lan 侧上线", online)
        if not online:
            return

        url = server.replace("http://", "ws://") + f"/tunnel/{tid}"
        async with websockets.connect(url, max_size=1 << 20) as ws:
            pid = None
            while pid is None:
                t, p, _ = split_frame(await asyncio.wait_for(ws.recv(), timeout=10))
                if t == 0x05:
                    pid = p

            await ws.send(build_frame(0x41, pid, b"ping-udp"))
            reply = None
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    t, p, payload = split_frame(await asyncio.wait_for(ws.recv(), timeout=deadline - time.time()))
                except TimeoutError:
                    break
                if p == pid and t == 0x41:
                    reply = payload
                    break
            check("B2 UDP 数据报往返 (echo)", reply == b"ping-udp", repr(reply))
    finally:
        if client_task is not None:
            await stop_client(client_task)
        await delete_tunnel(server, tid, token)
        echo_transport.close()


# ------------------------------------------------------------- 阶段 C: 令牌绑定隧道

async def phase_bound(server: str, token: str) -> None:
    print(f"\n== 阶段 C: 令牌绑定隧道 ==")
    echo, port = await start_tcp_echo()
    tid = None
    client_task = None
    try:
        out = http_json(server, "/api/tunnels", method="POST", token=token, body={
            "name": "selftest-bound", "proto": "tcp", "local_port": port,
        })
        tid = out["tunnel_id"]

        tok = http_json(server, f"/api/tunnels/{tid}/token", method="POST", token=token)
        bound = tok.get("access_token", "")
        check("C1 签发隧道绑定令牌", bool(bound))

        me = http_json(server, "/api/me", token=bound)
        check("C2 /me 返回绑定隧道的 ID", me.get("tunnel_id") == tid, repr(me.get("tunnel_id")))

        # config 不写 tunnel_id — 客户端须完全靠令牌确定隧道
        client_task = start_client(server, tid, bound, include_tid=False)
        online = await wait_lan_online(server, tid, token)
        check("C3 客户端凭令牌定位正确隧道 (config 无 tunnel_id)", online)
        if not online:
            return

        url = server.replace("http://", "ws://") + f"/tunnel/{tid}"
        async with websockets.connect(url, max_size=1 << 20) as ws:
            pid = None
            while pid is None:
                t, p, _ = split_frame(await asyncio.wait_for(ws.recv(), timeout=10))
                if t == 0x05:
                    pid = p
            await ws.send(build_frame(0x02, pid, b"bound-token-probe"))
            reply = None
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    t, p, payload = split_frame(await asyncio.wait_for(ws.recv(), timeout=deadline - time.time()))
                except TimeoutError:
                    break
                if p == pid and t == 0x02:
                    reply = payload
                    break
            check("C4 TCP 往返 (绑定令牌会话)", reply == b"bound-token-probe", repr(reply))
    finally:
        if client_task is not None:
            await stop_client(client_task)
        await delete_tunnel(server, tid, token)
        echo.close()


# ------------------------------------------------------------- 阶段 D: 纯 HTTP 直转

class _EchoHandler(http.server.BaseHTTPRequestHandler):
    """最小 HTTP 回显服务: 把 method/path/query/bodylen 作为响应体返回。"""

    def _echo(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        path, _, query = self.path.partition("?")
        if path == "/page.html":
            payload = (b"<!doctype html><html><head><title>echo</title></head>"
                      b"<body>HTML-ECHO</body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = (f"HTTP-ECHO method={self.command} path={path} "
                   f"query={query} bodylen={len(body)}").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _echo
    do_POST = _echo
    do_PUT = _echo

    def log_message(self, *args):  # 静默
        pass


async def phase_http(server: str, token: str) -> None:
    print(f"\n== 阶段 D: 纯 HTTP 直转(浏览器开短链) ==")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tid = None
    tid_offline = None
    client_task = None
    loop = asyncio.get_running_loop()
    try:
        out = http_json(server, "/api/tunnels", method="POST", token=token, body={
            "name": "selftest-http", "proto": "tcp", "local_port": port,
        })
        tid = out["tunnel_id"]
        client_task = start_client(server, tid, token)
        online = await wait_lan_online(server, tid, token)
        check("D1 客户端 lan 侧上线 (HTTP 隧道)", online)

        if online:
            status, _, body = await loop.run_in_executor(
                None, lambda: http_raw(server, f"/tunnel/{tid}/hello?x=1"))
            text = body.decode("utf-8", "replace")
            check("D2 GET 直转: method/path/query 透传",
                  status == 200 and "method=GET path=/hello query=x=1" in text, text[:120])

            status, _, body = await loop.run_in_executor(
                None, lambda: http_raw(server, f"/tunnel/{tid}/api", method="POST",
                                       body=b"http-bridge-body",
                                       headers={"Content-Type": "text/plain"}))
            text = body.decode("utf-8", "replace")
            check("D3 POST 直转: 请求体透传 (bodylen=16)",
                  status == 200 and "method=POST path=/api" in text and "bodylen=16" in text, text[:120])

            # nginx 对 /tunnel/ 的普通请求会带 Connection: upgrade(无 Upgrade) — 仍须直转成功
            status, _, body = await loop.run_in_executor(
                None, lambda: http_raw(server, f"/tunnel/{tid}/",
                                       headers={"Connection": "upgrade"}))
            check("D4 带 Connection: upgrade 头的 GET 仍正常直转 (nginx 兼容)",
                  status == 200 and "HTTP-ECHO" in body.decode("utf-8", "replace"),
                  body[:80].decode("utf-8", "replace"))

            # D6: HTML 响应注入 <base>(相对引用钉在隧道前缀内) + Content-Length 同步
            status, hdrs, body = await loop.run_in_executor(
                None, lambda: http_raw(server, f"/tunnel/{tid}/page.html"))
            expect_base = f'<base href="/tunnel/{tid}/">'.encode()
            cl_val = next((v for k, v in hdrs.items() if k.lower() == "content-length"), None)
            cl_ok = cl_val is not None and int(cl_val) == len(body)
            check("D6 HTML 直转注入 <base> 且 Content-Length 同步",
                  status == 200 and expect_base in body and b"<title>echo</title>" in body
                  and body.rstrip().endswith(b"</html>") and cl_ok,
                  f"{status} base={'yes' if expect_base in body else 'no'} "
                  f"CL={cl_val} len={len(body)}")

        out = http_json(server, "/api/tunnels", method="POST", token=token, body={
            "name": "selftest-http-offline", "proto": "tcp", "local_port": port + 1000,
        })
        tid_offline = out["tunnel_id"]
        status, _, _ = await loop.run_in_executor(
            None, lambda: http_raw(server, f"/tunnel/{tid_offline}/"))
        check("D5 LAN 离线时返回 503", status == 503, str(status))

        # D7: 客户端下载接口(登录态; 只校验状态与大小, 不读全量体)
        def _dl():
            req = urllib.request.Request(server + "/api/client/download",
                                         headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, int(resp.headers.get("Content-Length") or -1)

        dl_status, dl_cl = await loop.run_in_executor(None, _dl)
        check("D7 客户端下载接口 (200 + exe 大小)",
              dl_status == 200 and dl_cl > 1_000_000, f"{dl_status} CL={dl_cl}")
    finally:
        if client_task is not None:
            await stop_client(client_task)
        await delete_tunnel(server, tid, token)
        await delete_tunnel(server, tid_offline, token)
        srv.shutdown()


# ------------------------------------------------------------- main

async def main() -> None:
    parser = argparse.ArgumentParser(description="nattunnel end-to-end selftest")
    parser.add_argument("--server", default="http://127.0.0.1:8100")
    parser.add_argument("--user", default=os.environ.get("NATTUNNEL_TEST_USER", "admin"))
    parser.add_argument(
        "--password",
        default=os.environ.get("NATTUNNEL_TEST_PASSWORD", ""),
        help="留空时读取环境变量 NATTUNNEL_TEST_PASSWORD",
    )
    args = parser.parse_args()
    if not args.password:
        parser.error("缺少管理员密码: 用 --password 或设置 NATTUNNEL_TEST_PASSWORD")
    server = args.server.rstrip("/")

    print(f"selftest vs {server}")
    health = http_json(server, "/api/health")
    check("00 服务器健康", health.get("ok") is True)

    token = login(server, args.user, args.password)
    check("01 RSA 登录获取 JWT", bool(token))

    await phase_tcp(server, token)
    await phase_udp(server, token)
    await phase_bound(server, token)
    await phase_http(server, token)

    print(f"\n结果: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("失败项:", ", ".join(FAIL))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
