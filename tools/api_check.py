#!/usr/bin/env python3
"""管理员/用户 API 巡检(对已运行的服务器)。

覆盖: RSA 登录、me、改密码、建用户、非管理员越权检查、建隧道、改配置、列表、清理。
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

    patched = http(server, f"/api/tunnels/{tunnel['tunnel_id']}", method="PATCH", token=tok2,
                   body={"bandwidth_kbps": 256, "enabled": False})
    assert patched["bandwidth_kbps"] == 256 and patched["enabled"] is False
    print("[PASS] PATCH 隧道配置: 256kbps, enabled=False")

    listing = http(server, "/api/tunnels", token=tok2)
    ids = [t["tunnel_id"] for t in listing]
    assert tunnel["tunnel_id"] in ids
    print("[PASS] 隧道列表:", ids)

    # 清理: 删隧道 + 删临时用户(管理员)
    req = urllib.request.Request(server + f"/api/tunnels/{tunnel['tunnel_id']}", method="DELETE")
    req.add_header("Authorization", f"Bearer {tok}")
    urllib.request.urlopen(req, timeout=15)
    req = urllib.request.Request(server + f"/api/users/{test_user}", method="DELETE")
    req.add_header("Authorization", f"Bearer {tok}")
    urllib.request.urlopen(req, timeout=15)
    print("[PASS] 清理完成(删隧道/用户)")
    print("API CHECK ALL PASS")


if __name__ == "__main__":
    main()
