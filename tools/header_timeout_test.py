#!/usr/bin/env python3
"""阶段 F: 隧道配置项 header_timeout_s(响应头等待窗口)端到端验证。

对应需求: records/2026-11-04-19-06-38.md(默认 30 秒, 最大 600 秒, 服务端逐请求读取)。

前置: 服务器已在运行(默认 http://127.0.0.1:8100), 例如:
    cd server && set DATABASE_URL=sqlite:///./selftest.db && python -m uvicorn app.main:app --port 8100

用法(凭据不入库):
    python tools/header_timeout_test.py --server http://127.0.0.1:8100 --user admin
    set NATTUNNEL_TEST_PASSWORD=...   (或 --password)

场景: 内网"慢服务"在吐响应头前 sleep(SLOW.delay); 隧道的 header_timeout_s
      大于 delay -> 200; 小于 delay -> 504 "本地服务响应超时"(http_bridge 的 504 分支)。
"""
import argparse
import asyncio
import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
import selftest as st  # noqa: E402  复用登录/隧道/进程内客户端helper

SLOW = {"delay": 40.0}


class SlowHandler(http.server.BaseHTTPRequestHandler):
    """响应头故意延迟 SLOW.delay 秒后才吐(模拟 llm 生成耗时)。"""

    def do_GET(self):
        delay = SLOW["delay"]
        time.sleep(delay)
        body = b"slow-ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def raw_get(server: str, path: str, timeout: float = 90.0) -> tuple:
    req = urllib.request.Request(server + path)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


async def main() -> None:
    ap = argparse.ArgumentParser(description="header_timeout_s 端到端验证")
    ap.add_argument("--server", default="http://127.0.0.1:8100")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="")
    args = ap.parse_args()
    password = args.password or os.environ.get("NATTUNNEL_TEST_PASSWORD", "")
    token = st.login(args.server, args.user, password)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tid_default = None
    tid = None
    task = None
    loop = asyncio.get_running_loop()
    try:
        # F1: 不填该字段 -> 默认 30
        out = st.http_json(args.server, "/api/tunnels", method="POST", token=token,
                           body={"name": "ht-default", "proto": "http", "local_port": port})
        tid_default = out["tunnel_id"]
        st.check("F1 新建隧道默认 header_timeout_s=30",
                 out.get("header_timeout_s") == 30, str(out.get("header_timeout_s")))

        # F2: 显式 45 -> 落库 + 回显
        out2 = st.http_json(args.server, "/api/tunnels", method="POST", token=token,
                            body={"name": "ht-45", "proto": "http", "local_port": port,
                                  "header_timeout_s": 45})
        tid = out2["tunnel_id"]
        st.check("F2 指定 header_timeout_s=45 落库并回显",
                 out2.get("header_timeout_s") == 45, str(out2.get("header_timeout_s")))

        # F3: 上限 600 接受
        out3 = st.http_json(args.server, f"/api/tunnels/{tid}", method="PATCH", token=token,
                            body={"header_timeout_s": 600})
        st.check("F3 PATCH header_timeout_s=600 接受",
                 out3.get("header_timeout_s") == 600, str(out3.get("header_timeout_s")))

        # F4: 601 越界被拒(422)
        code = None
        try:
            st.http_json(args.server, f"/api/tunnels/{tid}", method="PATCH", token=token,
                         body={"header_timeout_s": 601})
        except urllib.error.HTTPError as exc:
            code = exc.code
        st.check("F4 header_timeout_s=601 越界被拒(422)", code == 422, str(code))

        # F5: 窗口 45s > 慢服务 40s -> 200(隧道 + 进程内客户端全链路)
        st.http_json(args.server, f"/api/tunnels/{tid}", method="PATCH", token=token,
                     body={"header_timeout_s": 45})
        task = st.start_client(args.server, tid, token)
        online = await st.wait_lan_online(args.server, tid, token)
        st.check("F5 lan 侧上线(隧道配置含响应超时)", online)
        if not online:
            return
        t0 = time.time()
        status, body = await loop.run_in_executor(None, lambda: raw_get(args.server, f"/tunnel/{tid}/hello"))
        st.check("F6 窗口45s: 40s 慢响应得到 200",
                 status == 200 and body == b"slow-ok",
                 f"{status} {body[:40]} {round(time.time() - t0, 1)}s")

        # F7: 收紧到 10s -> 504 文案为"本地服务响应超时"
        st.http_json(args.server, f"/api/tunnels/{tid}", method="PATCH", token=token,
                     body={"header_timeout_s": 10})
        t0 = time.time()
        status, body = await loop.run_in_executor(None, lambda: raw_get(args.server, f"/tunnel/{tid}/hello"))
        text = body.decode("utf-8", "replace")
        st.check("F7 窗口10s: 40s 慢响应 504 且 detail 为本地服务响应超时",
                 status == 504 and "本地服务响应超时" in text,
                 f"{status} {text[:60]} {round(time.time() - t0, 1)}s")

        # F8: 改回 45, 无重启, 下一个请求立即生效(逐请求读库)
        st.http_json(args.server, f"/api/tunnels/{tid}", method="PATCH", token=token,
                     body={"header_timeout_s": 45})
        status, body = await loop.run_in_executor(None, lambda: raw_get(args.server, f"/tunnel/{tid}/hello"))
        st.check("F8 改回45后同一隧道立即恢复 200(逐请求读配置, 无需重启)",
                 status == 200 and body == b"slow-ok", f"{status} {body[:40]}")

        # F9: 管理页字段随 API 回显(表头渲染依赖)
        out4 = st.http_json(args.server, f"/api/tunnels/{tid}", token=token)
        st.check("F9 GET 单隧道回显 header_timeout_s=45", out4.get("header_timeout_s") == 45,
                 str(out4.get("header_timeout_s")))
    finally:
        if task is not None:
            await st.stop_client(task)
        await st.delete_tunnel(args.server, tid, token)
        await st.delete_tunnel(args.server, tid_default, token)
        srv.shutdown()

    print(f"\n阶段 F: PASS {len(st.PASS)} / FAIL {len(st.FAIL)}")
    if st.FAIL:
        print("FAILED:", st.FAIL)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
