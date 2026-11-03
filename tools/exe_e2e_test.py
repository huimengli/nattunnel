#!/usr/bin/env python3
"""exe 端到端验收(真实构建产物 client/dist/nattunnel-client.exe)。

覆盖:
  X1-X3 新 exe 以 tcp(字节流)隧道上线 + 公网 WS 桥接 echo + 日志走字节流分支
  X4-X7 新 exe 以 http(混合)隧道上线 + 纯 HTTP 直转(浏览器语义) + 热更新端口后 WS 桥接 echo
  X8-X10(可选 --old-exe <路径>) 旧 exe 对 http 隧道: 控制面可上线, 但数据面走 udp 分支 -> WS 桥接 504

用法(服务器需已在跑, 凭据走参数/环境变量, 不入库):
  python tools/exe_e2e_test.py --server http://127.0.0.1:8100 --password <管理员密码> \
      [--exe client/dist/nattunnel-client.exe] [--old-exe <旧构建路径>]

说明: PyInstaller --onefile 需要可写的临时目录; 若系统 TEMP 不可写(沙箱/受限环境),
      子进程环境会把 TEMP/TMP 指到仓库内 client/_tmp_temp(用后即删)。
      日志 exe1.log/exe2.log/exe3.log 写在当前目录(gitignore)。
"""

import argparse
import base64
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as pad

import asyncio
import websockets

EXE = ""


def http(server, path, method="GET", body=None, token=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=15) as r:
        b = r.read()
        if raw:
            return r.status, b
        return json.loads(b.decode()) if b else None


def rsa_login(server, pub_pem, user, pwd):
    key = serialization.load_pem_public_key(pub_pem.encode())
    enc = key.encrypt(json.dumps({"username": user, "password": pwd}).encode(), pad.PKCS1v15())
    return http(server, "/api/login", "POST",
               {"secure_payload": base64.b64encode(enc).decode()})["access_token"]


def kill_exe(image="nattunnel-client.exe"):
    subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True)
    time.sleep(1.5)


def start_http():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True, "path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


async def ws_echo():
    async def echo(ws):
        async for m in ws:
            await ws.send(m)
    srv = await websockets.serve(echo, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


def child_env():
    """沙箱里 AppData 临时目录不可写(PyInstaller --onefile 解包失败), 指向仓库内临时目录。"""
    local = os.path.join(os.path.dirname(os.path.abspath(EXE)), "_tmp_temp")
    os.makedirs(local, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "SystemRoot": "C:\\Windows", "windir": "C:\\Windows",
        "USERPROFILE": "C:\\Users\\楼听", "HOMEDRIVE": "C:", "HOMEPATH": "\\Users\\楼听",
        "APPDATA": "C:\\Users\\楼听\\AppData\\Roaming",
        "LOCALAPPDATA": "C:\\Users\\楼听\\AppData\\Local",
        "PATH": "C:\\Windows\\System32;C:\\Windows",
        "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
        "TEMP": local, "TMP": local,
    })
    return env


def run_exe(log_path, token, server, exe=None):
    fh = open(log_path, "wb")
    p = subprocess.Popen([exe or EXE, "-s", server, "-a", token, "-v"],
                         stdout=fh, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=child_env())
    return p, fh


def wait_online(server, tok, tid, tries=40):
    for _ in range(tries):
        time.sleep(0.5)
        try:
            if http(server, f"/api/tunnels/{tid}", token=tok).get("lan_online"):
                return True
        except Exception:
            pass
    return False


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8100")
    ap.add_argument("--password", required=True)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--exe", default=os.path.join(root, "client", "dist",
                                                  "nattunnel-client.exe"))
    ap.add_argument("--old-exe", default="")
    a = ap.parse_args()
    global EXE
    EXE = a.exe
    server = a.server.rstrip("/")
    fails = []

    def check(name, ok, detail=""):
        detail = detail.encode("gbk", "ignore").decode("gbk")
        print(("  [PASS] " if ok else "  [FAIL] ") + name + (" " + detail if detail else ""))
        if not ok:
            fails.append(name)

    print("exe:", EXE, os.path.getsize(EXE), "bytes")
    pub = http(server, "/api/auth/public-key")["public_key"]
    tok = rsa_login(server, pub, "admin", a.password)
    ws_srv, ws_port = await ws_echo()
    http_port = start_http()

    # --- X1-X3: exe 以 tcp(字节流)隧道上线 + WS 桥接 echo ---
    t1 = http(server, "/api/tunnels", "POST",
            {"name": "exe-tcp", "proto": "tcp", "local_port": ws_port}, tok)
    tid1 = t1["tunnel_id"]
    token1 = http(server, f"/api/tunnels/{tid1}/token", "POST", {}, tok)["access_token"]
    p1, fh1 = run_exe("exe1.log", token1, server)
    online1 = wait_online(server, tok, tid1)
    check("X1 exe(tcp 隧道) lan_online", online1)
    if online1:
        url1 = server.replace("http://", "ws://") + f"/tunnel/{tid1}"
        try:
            async with websockets.connect(url1, max_size=1 << 20) as ws:
                await ws.send("exe-e2e-1")
                out = await asyncio.wait_for(ws.recv(), timeout=10)
                check("X2 exe tcp 隧道 WS 桥接 echo", out == "exe-e2e-1", repr(out))
        except Exception as e:
            check("X2 exe tcp 隧道 WS 桥接 echo", False, repr(e))
    log1 = open("exe1.log", "rb").read().decode("gbk", "replace")
    check("X3 exe 日志走字节流(无 udp 分支)",
          "隧道配置: tcp" in log1 and "隧道已建立" in log1,
          log1[-110:].replace("\n", " / "))
    p1.terminate()
    kill_exe()

    # --- X4-X6: exe 以 http(混合)隧道上线 + 纯 HTTP 直转 + WS 桥接 ---
    t2 = http(server, "/api/tunnels", "POST",
            {"name": "exe-http", "proto": "http", "local_port": http_port}, tok)
    tid2 = t2["tunnel_id"]
    token2 = http(server, f"/api/tunnels/{tid2}/token", "POST", {}, tok)["access_token"]
    p2, fh2 = run_exe("exe2.log", token2, server)
    online2 = wait_online(server, tok, tid2)
    check("X4 exe(http 隧道) lan_online", online2)
    if online2:
        try:
            st, body = http(server, f"/tunnel/{tid2}/hello?a=1", raw=True)
            j = json.loads(body.decode())
            check("X5 exe http 隧道纯 HTTP 直转(浏览器语义)",
                  st == 200 and j.get("path") == "/hello?a=1", f"{st} {j}")
        except urllib.error.HTTPError as e:
            check("X5 exe http 隧道纯 HTTP 直转(浏览器语义)", False,
                  f"{e.code} {e.read()[:80]}")
        # http 隧道是混合入口: 热更新 local_port 到 WS echo 服务后走 WS 桥接
        http(server, f"/api/tunnels/{tid2}", "PATCH", {"local_port": ws_port}, tok)
        time.sleep(1.0)
        url2 = server.replace("http://", "ws://") + f"/tunnel/{tid2}/ws"
        try:
            async with websockets.connect(url2, max_size=1 << 20) as ws:
                await ws.send("exe-http-ws")
                out = await asyncio.wait_for(ws.recv(), timeout=10)
                check("X6 exe http 隧道(混合) WS 桥接 echo", out == "exe-http-ws", repr(out))
        except Exception as e:
            check("X6 exe http 隧道(混合) WS 桥接 echo", False, repr(e))
    log2 = open("exe2.log", "rb").read().decode("gbk", "replace")
    check("X7 exe 日志走字节流(http 也进 run_tcp)",
          "隧道配置: http" in log2 and "隧道已建立" in log2,
          log2[-110:].replace("\n", " / "))
    p2.terminate()
    kill_exe()

    # --- X8-X10: 旧 exe(2026-11-03 13:03 构建, proto==tcp 判定) 对 http 隧道不兼容 ---
    if a.old_exe:
        t3 = http(server, "/api/tunnels", "POST",
                  {"name": "oldexe-http", "proto": "http", "local_port": ws_port}, tok)
        tid3 = t3["tunnel_id"]
        token3 = http(server, f"/api/tunnels/{tid3}/token", "POST", {}, tok)["access_token"]
        p3, fh3 = run_exe("exe3.log", token3, server, a.old_exe)
        online3 = wait_online(server, tok, tid3)
        check("X8 旧 exe 控制面可上线(WS 连接/热更新不变)", online3)
        url3 = server.replace("http://", "ws://") + f"/tunnel/{tid3}/ws"
        echo_ok = None
        try:
            async with websockets.connect(url3, max_size=1 << 20) as ws:
                await ws.send("old-exe-check")
                out = await asyncio.wait_for(ws.recv(), timeout=8)
                echo_ok = out == "old-exe-check"
        except Exception as e:
            echo_ok = False
        check("X9 旧 exe 对 http 隧道无法做字节流桥接(数据面不兼容)", not echo_ok,
              f"echo_ok={echo_ok}")
        log3 = open("exe3.log", "rb").read().decode("gbk", "replace")
        check("X10 旧 exe 对 http 走 udp 分支(无 stream 打开)",
              "隧道配置: http" in log3 and "stream" not in log3,
              log3[-100:].replace("\n", " / "))
        p3.terminate()
        kill_exe(os.path.basename(a.old_exe))
        http(server, f"/api/tunnels/{tid3}", "DELETE", token=tok)

    http(server, f"/api/tunnels/{tid1}", "DELETE", token=tok)
    http(server, f"/api/tunnels/{tid2}", "DELETE", token=tok)
    ws_srv.close()
    print("结果:", len(fails), "failed;", "ALL PASS" if not fails else "有失败项")
    if fails:
        raise SystemExit(1)


asyncio.run(main())
