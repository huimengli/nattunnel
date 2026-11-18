#!/usr/bin/env python3
"""管理员/用户 API 巡检(对已运行的服务器)。

覆盖: RSA 登录、me、改密码、建用户、非管理员越权检查、建隧道(http/tcp/udp 取值与改配置)、列表、清理;
      UDP 原生入口(udp_port/udp_allow 回显、端点表、端口唯一性 409、改绑、非 udp 清空、删除解绑)。
    python tools/api_check.py [--server http://127.0.0.1:8100]
    # 凭据从参数或环境变量 NATTUNNEL_TEST_USER / NATTUNNEL_TEST_PASSWORD 提供(不入库)
"""
import argparse
import os
import base64
import json
import sys
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding


def http(server, path, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw.decode()) if raw else None


def rsa_login(server, pub_pem: str, username: str, password: str) -> str:
    key = serialization.load_pem_public_key(pub_pem.encode())
    enc = key.encrypt(
        json.dumps({"username": username, "password": password}).encode(),
        rsa_padding.PKCS1v15(),
    )
    out = http(server, "/api/login", method="POST",
               body={"secure_payload": base64.b64encode(enc).decode()})
    return out["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8100")
    parser.add_argument(
        "--admin-user",
        default=os.environ.get("NATTUNNEL_TEST_USER", "admin"),
        help="留空时读取环境变量 NATTUNNEL_TEST_USER",
    )
    parser.add_argument(
        "--admin-password",
        default=os.environ.get("NATTUNNEL_TEST_PASSWORD", ""),
        help="留空时读取环境变量 NATTUNNEL_TEST_PASSWORD",
    )
    args = parser.parse_args()
    if not args.admin_password:
        parser.error("缺少管理员密码: 用 --admin-password 或设置 NATTUNNEL_TEST_PASSWORD")
    server = args.server.rstrip("/")

    pub = http(server, "/api/auth/public-key")["public_key"]
    tok = rsa_login(server, pub, args.admin_user, args.admin_password)
    me = http(server, "/api/me", token=tok)
    print("[PASS] 登录/me:", me)

    test_user = f"apicheck_{int(time.time() * 1000) % 1000000:06d}"
    created = http(server, "/api/users", method="POST", token=tok,
                   body={"username": test_user, "password": "apicheck123", "role": "user"})
    print("[PASS] 建用户:", created["username"])

    tok2 = rsa_login(server, pub, test_user, "apicheck123")
    try:
        http(server, "/api/users", token=tok2)
        print("[FAIL] 非管理员应被拒绝访问 /api/users")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code == 403, f"期望 403, 实际 {e.code}"
        print("[PASS] 非管理员访问 /api/users -> 403")

    # 改密码(用临时用户, 不动管理员凭据): 204 + 旧密码失效 + 新密码可登录
    http(server, "/api/password", method="POST", token=tok2,
         body={"old_password": "apicheck123", "new_password": "apicheck456"})
    print("[PASS] POST /api/password -> 204")
    try:
        rsa_login(server, pub, test_user, "apicheck123")
        print("[FAIL] 旧密码修改后仍应登录失败")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code == 401, f"期望 401, 实际 {e.code}"
        print("[PASS] 旧密码已失效 -> 401")
    rsa_login(server, pub, test_user, "apicheck456")
    print("[PASS] 新密码登录成功")

    tunnel = http(server, "/api/tunnels", method="POST", token=tok2,
                  body={"name": "api-check", "proto": "udp", "local_port": 9000, "bandwidth_kbps": 100})
    print("[PASS] 建隧道:", tunnel["tunnel_id"], tunnel["proto"], tunnel["local_port"], f"{tunnel['bandwidth_kbps']}kbps")

    # 协议取值: http/tcp/udp 可用; 其他(如 ws)拒绝 422; 协议可 PATCH 热改(房间被拆, 客户端重连)
    hp = http(server, "/api/tunnels", method="POST", token=tok2,
              body={"name": "api-check-proto", "proto": "http", "local_port": 9200})
    assert hp["proto"] == "http", hp
    print("[PASS] 建隧道 proto=http:", hp["tunnel_id"])
    try:
        http(server, "/api/tunnels", method="POST", token=tok2,
             body={"name": "api-check-badproto", "proto": "ws", "local_port": 9200})
        print("[FAIL] 非法协议 proto=ws 应被拒绝(422)")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code == 422, f"期望 422, 实际 {e.code}"
        print("[PASS] 非法协议 proto=ws -> 422")
    patched_proto = http(server, f"/api/tunnels/{hp['tunnel_id']}", method="PATCH", token=tok2,
                         body={"proto": "tcp"})
    assert patched_proto["proto"] == "tcp", patched_proto
    print("[PASS] PATCH proto http -> tcp")

    patched = http(server, f"/api/tunnels/{tunnel['tunnel_id']}", method="PATCH", token=tok2,
                   body={"bandwidth_kbps": 256, "enabled": False})
    assert patched["bandwidth_kbps"] == 256 and patched["enabled"] is False
    print("[PASS] PATCH 隧道配置: 256kbps, enabled=False")

    listing = http(server, "/api/tunnels", token=tok2)
    ids = [t["tunnel_id"] for t in listing]
    assert tunnel["tunnel_id"] in ids
    print("[PASS] 隧道列表:", ids)

    # 绑定令牌: 属主签发 -> /me 带回隧道 ID; 管理员也可签发
    btok = http(server, f"/api/tunnels/{tunnel['tunnel_id']}/token", method="POST", token=tok2)["access_token"]
    bme = http(server, "/api/me", token=btok)
    assert bme.get("tunnel_id") == tunnel["tunnel_id"], f"绑定隧道不符: {bme}"
    print("[PASS] 属主签发绑定令牌, /me 返回 tunnel_id:", bme.get("tunnel_id"))
    http(server, f"/api/tunnels/{tunnel['tunnel_id']}/token", method="POST", token=tok)
    print("[PASS] 管理员亦可为任意隧道签发绑定令牌")

    # 纯 HTTP 直转: 无 LAN 在线的 http 隧道 -> 503(直转语义归入 http 协议)
    ht = http(server, "/api/tunnels", method="POST", token=tok2,
              body={"name": "api-check-http", "proto": "http", "local_port": 9100})
    try:
        urllib.request.urlopen(server + f"/tunnel/{ht['tunnel_id']}/", timeout=10)
        print("[FAIL] HTTP 直转在 LAN 离线时应返回 503")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code == 503, f"期望 503, 实际 {e.code}"
        print("[PASS] 纯 HTTP 直转(LAN 离线) -> 503")

    # UDP 原生入口(Unity/标准 UDP 客户端用): 字段回显 + 端点表 + 端口唯一性 + 改绑 + 非 udp 清空 + 解绑
    u1 = http(server, "/api/tunnels", method="POST", token=tok2,
              body={"name": "api-check-udp-entry", "proto": "udp", "local_port": 9300,
                    "udp_port": 26100, "udp_allow": "203.0.113.0/24"})
    assert u1["udp_port"] == 26100 and u1["udp_allow"] == "203.0.113.0/24", u1
    assert (u1.get("udp_public") or "").endswith(":26100"), u1.get("udp_public")
    print("[PASS] udp 隧道 udp_port/udp_allow/udp_public 回显:", u1["udp_public"])
    st = http(server, "/api/udp/status", token=tok2)
    eps = [e for e in st["endpoints"] if e["tunnel_id"] == u1["tunnel_id"]]
    assert eps and eps[0]["port"] == 26100, st
    print("[PASS] UDP 端点已绑定(端点表):", eps[0]["public"], "peers=", eps[0]["peers"])
    try:
        http(server, "/api/udp/status")   # 未登录
        print("[FAIL] /api/udp/status 未登录应 401")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code == 401, f"期望 401, 实际 {e.code}"
        print("[PASS] /api/udp/status 未登录 -> 401")
    try:
        http(server, "/api/tunnels", method="POST", token=tok2,
              body={"name": "api-check-udp-conflict", "proto": "udp", "local_port": 9301, "udp_port": 26100})
        print("[FAIL] 同 UDP 端口给第二条隧道应被拒(409)")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code == 409, f"期望 409, 实际 {e.code}"
        print("[PASS] UDP 端口占用 -> 409")
    u2 = http(server, "/api/tunnels", method="POST", token=tok2,
              body={"name": "api-check-http-udpport", "proto": "http", "local_port": 9302, "udp_port": 26101})
    assert u2["udp_port"] is None, u2
    print("[PASS] 非 udp 隧道的 udp_port 被清空(不占用端口)")
    u3 = http(server, f"/api/tunnels/{u1['tunnel_id']}", method="PATCH", token=tok2,
              body={"udp_allow": "198.51.100.7", "udp_port": 26102})
    assert u3["udp_port"] == 26102 and u3["udp_allow"] == "198.51.100.7", u3
    st3 = http(server, "/api/udp/status", token=tok2)
    eps3 = [e for e in st3["endpoints"] if e["tunnel_id"] == u1["tunnel_id"]]
    assert eps3 and eps3[0]["port"] == 26102, st3
    print("[PASS] PATCH 改 UDP 端口/白名单 -> 重新绑定:", eps3[0]["public"])
    http(server, f"/api/tunnels/{u1['tunnel_id']}", method="DELETE", token=tok2)
    st4 = http(server, "/api/udp/status", token=tok2)
    assert all(e["tunnel_id"] != u1["tunnel_id"] for e in st4["endpoints"]), st4
    print("[PASS] 删除隧道 -> UDP 端点解绑")
    u4 = http(server, "/api/tunnels", method="POST", token=tok2,
              body={"name": "api-check-udp-free", "proto": "udp", "local_port": 9303, "udp_port": 26101})
    assert u4["udp_port"] == 26101, u4   # 26101 未被占用(26100 才是占用者)
    print("[PASS] 未被占用的 UDP 端口可用(26101)")
    http(server, f"/api/tunnels/{u4['tunnel_id']}", method="DELETE", token=tok2)
    # 客户端下载(登录态): 200 + Content-Length > 1MB (不读全量体)
    req = urllib.request.Request(server + "/api/client/download")
    req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        assert resp.status == 200 and int(resp.headers.get("Content-Length") or -1) > 1_000_000
    print("[PASS] 客户端下载接口 -> 200 (exe)")

    # 客户端下载(静态直链, 免登录): 200 + Content-Length 一致
    with urllib.request.urlopen(server + "/nattunnel-client.exe", timeout=60) as resp:
        assert resp.status == 200 and int(resp.headers.get("Content-Length") or -1) > 1_000_000
    print("[PASS] 客户端静态直链 /nattunnel-client.exe -> 200 (exe)")

    # 清理: 删隧道 + 删临时用户(管理员)
    req = urllib.request.Request(server + f"/api/tunnels/{tunnel['tunnel_id']}", method="DELETE")
    req.add_header("Authorization", f"Bearer {tok}")
    urllib.request.urlopen(req, timeout=15)
    req = urllib.request.Request(server + f"/api/tunnels/{hp['tunnel_id']}", method="DELETE")
    req.add_header("Authorization", f"Bearer {tok}")
    urllib.request.urlopen(req, timeout=15)
    req = urllib.request.Request(server + f"/api/tunnels/{ht['tunnel_id']}", method="DELETE")
    req.add_header("Authorization", f"Bearer {tok}")
    urllib.request.urlopen(req, timeout=15)
    req = urllib.request.Request(server + f"/api/users/{test_user}", method="DELETE")
    req.add_header("Authorization", f"Bearer {tok}")
    urllib.request.urlopen(req, timeout=15)
    print("[PASS] 清理完成(删隧道/用户)")
    print("API CHECK ALL PASS")


if __name__ == "__main__":
    main()