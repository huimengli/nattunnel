#!/usr/bin/env python3
"""Unity UDP 原生入口(公网 UDP 端口映射) 20 轮回归台架。

每轮(服务器全新启动 + 中途重启一次)跑:
  1) tools/selftest.py 全阶段 A-G(阶段 G = UDP 原生入口 21 项: 数据报往返/多源地址对端隔离/源 IP 白名单/超上限丢弃/改绑/解绑/空闲回收/与 WS 子协议裸流共存)
  2) tools/api_check.py API 巡检(含 udp_port/udp_allow 回显、端点表、端口唯一性 409、改绑、非 udp 清空、删除解绑)
  3) P1 建 udp 隧道配公网 UDP 端口 -> 端点绑定与公网展示串(Unity 要填的 Server Address:Port)
  4) P3 LAN 侧离线时对入口发数据报: 丢弃(无回包)且端点存活(UDP 入口不能把服务端弄崩)
  5) P2 服务器重启: 启动时按库自动绑定 UDP 入口(单 worker 语义; 重启后入口仍在)
  6) P4 删除隧道 -> 端点解绑(端点表清空)
  7) P5 管理页静态检查: UDP 端口/白名单字段 + Unity 指引 + 表列 + 状态接口

每轮写 tests/<批次>/record_i.md(时间、场景、期望、实际、结果、备注)。

用法(凭据走环境变量, 不入库; 受限沙箱里 AppData 临时目录不可写, 台架把 TEMP/TMP 指到仓库内 _tmp_rounds):
    set NATTUNNEL_TEST_PASSWORD=...
    python tools/unity_udp_test.py --rounds 20 --start 1 --outdir tests/2026-11-18-13-03-15_cc
"""
import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as pad

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
PY = sys.executable
PORT = 8100
SERVER = "http://127.0.0.1:%d" % PORT
TMP_DIR = ROOT / "_tmp_rounds"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def http(path: str, method: str = "GET", body=None, token: str = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SERVER + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw.decode()) if raw else None


def login(pw: str) -> str:
    pub = http("/api/auth/public-key")["public_key"]
    key = serialization.load_pem_public_key(pub.encode())
    enc = key.encrypt(json.dumps({"username": "admin", "password": pw}).encode(), pad.PKCS1v15())
    return http("/api/login", "POST", {"secure_payload": base64.b64encode(enc).decode()})["access_token"]


def child_env(pw: str) -> dict:
    """子进程环境: 受限沙箱 AppData 不可写 -> TEMP/TMP 指仓库内目录; 自测专用配置。

    单 worker(UDP 中继态在进程内存, 多 worker 会丢包) + 回环监听(自测里的"公网 UDP 端口"由回环端口代表)。
    """
    TMP_DIR.mkdir(exist_ok=True)
    local = str(TMP_DIR)
    env = dict(os.environ)
    env.update({
        "SystemRoot": "C:\\Windows", "windir": "C:\\Windows",
        "PATH": "C:\\Windows\\System32;C:\\Windows",
        "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
        "TEMP": local, "TMP": local, "TEMPDIR": local, "TMPDIR": local,
        "DATABASE_URL": "sqlite:///./selftest.db",
        "INITIAL_ADMIN_USERNAME": "admin",
        "INITIAL_ADMIN_PASSWORD": pw,
        "HOST": "127.0.0.1", "PORT": str(PORT),
        "UDP_LISTEN_HOST": "127.0.0.1",
        "UDP_PUBLIC_HOST": "127.0.0.1",
        "UDP_MAX_DATAGRAM": "1600",   # 设小以便验证超上限丢弃
        "UDP_IDLE_TIMEOUT": "6",      # 设短以便验证空闲回收
        "UDP_MAX_PEERS": "8",
        "PYTHONIOENCODING": "utf-8",   # 服务器日志/自测输出统一 UTF-8(Windows 默认 cp936 会乱码)
        "NATTUNNEL_TEST_PASSWORD": pw,
        "NATTUNNEL_TEST_USER": "admin",
    })
    return env


def reset_db(retries: float = 20.0):
    """每轮全新库: 删掉自测 sqlite 库(含 -wal/-shm), 保证管理员密码与隧道状态干净。

    服务器重启那次不能删库(启动时自动绑定要读上一轮留下的隧道)。
    Windows 上进程退出后文件释放有延迟(SQLite 占用), 故重试。
    """
    for name in ("selftest.db", "selftest.db-wal", "selftest.db-shm"):
        old = SERVER_DIR / name
        deadline = time.time() + retries
        while old.exists():
            try:
                old.unlink()
                break
            except PermissionError:
                time.sleep(0.5)
                if time.time() > deadline:
                    raise


def start_server(env: dict, log_path: str):
    fh = open(log_path, "wb")
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                          "--port", str(PORT), "--workers", "1"],
                         # stdin=PIPE: Windows 上 stdin=nul 会被判定为 tty, 服务器会走
                         # 交互式首启向导(读 stdin) -> 非交互模式必须用 PIPE 才能走 .env/INITIAL_ADMIN_PASSWORD。
                         cwd=str(SERVER_DIR), env=env, stdin=subprocess.PIPE,
                         stdout=fh, stderr=subprocess.STDOUT)
    return p, fh


def stop_server(p, fh):
    if p is None:
        return
    p.terminate()
    deadline = time.time() + 15
    while p.poll() is None and time.time() < deadline:
        time.sleep(0.3)
    if p.poll() is None:
        p.kill()
        time.sleep(1.0)
    if fh:
        fh.close()


def wait_health(timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(SERVER + "/api/health", timeout=3) as r:
                if json.loads(r.read().decode()).get("ok") is True:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def run_tool(script: str, env: dict) -> tuple:
    cp = subprocess.run([PY, str(ROOT / "tools" / script), "--server", SERVER],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        env=env, cwd=str(ROOT))
    out = (cp.stdout or "") + (cp.stderr or "")
    return cp.returncode, out


def parse_tool(out: str) -> tuple:
    items = [l.strip() for l in out.splitlines()]
    return sum(1 for l in items if l.startswith("[PASS]")), [l for l in items if l.startswith("[FAIL")]


def tail(path: str, n: int = 6) -> str:
    """服务器日志尾部(诊断用): 健康检查失败时带上最后几行。"""
    try:
        txt = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return " | ".join(txt[-n:])
    except Exception as exc:
        return f"(读日志失败: {exc})"


def endpoints(token: str) -> list:
    return http("/api/udp/status", token=token).get("endpoints", [])


def free_udp_port(base: int) -> int:
    for p in range(base, base + 60):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            probe.close()
    return None


def udp_probe(port: int, payload: bytes, wait: float = 2.0, bind: str = None) -> bytes:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(wait)
    if bind:
        s.bind((bind, 0))
    s.connect(("127.0.0.1", port))   # connected UDP: 与 UnityTransport 一致(只收该对端回包)
    try:
        s.send(payload)
    except OSError:
        return None
    try:
        return s.recv(65536)
    except socket.timeout:
        return None
    finally:
        s.close()


def run_round(i: int, pw: str, outdir: Path) -> tuple:
    env = child_env(pw)
    rows = []
    log_a = str(TMP_DIR / f"r{i}_server_a.log")
    log_b = str(TMP_DIR / f"r{i}_server_b.log")
    reset_db()
    p, fh = start_server(env, log_a)
    p2, fh2 = None, None
    ok = wait_health()
    rows.append(("R0 服务器全新启动 + /api/health", ok, log_a + ("" if ok else " " + tail(log_a))))
    try:
        if ok:
            token = login(pw)
            rc, out = run_tool("selftest.py", env)
            n1, f1 = parse_tool(out)
            rows.append((f"S1 selftest 全阶段 A-G: {n1} 项通过", rc == 0, f"rc={rc} FAIL={f1[:4]}"))
            rc2, out2 = run_tool("api_check.py", env)
            n2, f2 = parse_tool(out2)
            rows.append((f"S2 api_check API 巡检: {n2} 项通过", rc2 == 0, f"rc={rc2} FAIL={f2[:4]}"))

            port = free_udp_port(26200 + i)
            created = http("/api/tunnels", "POST", {"name": "unity-udp-persist", "proto": "udp",
                            "local_port": 9400, "udp_port": port, "udp_allow": "127.0.0.1"}, token)
            tid = created["tunnel_id"]
            e1 = [e for e in endpoints(token) if e["tunnel_id"] == tid]
            rows.append(("P1 建 udp 隧道并绑定公网 UDP 端口",
                         created.get("udp_port") == port and len(e1) == 1,
                         f"tid={tid} port={port} eps={e1}"))

            rep = udp_probe(port, b"offline-probe", 2.0)
            e2 = [e for e in endpoints(token) if e["tunnel_id"] == tid]
            rows.append(("P3 LAN 离线时对入口发数据报: 丢弃且端点存活", rep is None and len(e2) == 1, f"reply={rep!r}"))

            stop_server(p, fh)
            p, fh = None, None
            p2, fh2 = start_server(env, log_b)
            ok2 = wait_health()
            tok2 = login(pw)
            e3 = [e for e in endpoints(tok2) if e["tunnel_id"] == tid]
            rows.append(("P2 重启后启动时自动绑定 UDP 入口", ok2 and len(e3) == 1, f"health={ok2} eps={e3} {log_b}"))
            rep2 = udp_probe(port, b"still-dead-lan", 2.0)
            rows.append(("P2b 重启后入口仍在线(内网离线 -> 无回包, 端点存活)", rep2 is None and len(endpoints(tok2)) >= 1, f"reply={rep2!r}"))

            http(f"/api/tunnels/{tid}", "DELETE", None, tok2)
            e4 = [e for e in endpoints(tok2) if e["tunnel_id"] == tid]
            rows.append(("P4 删除隧道后 UDP 端点解绑", not e4, str(e4)))

            html = (SERVER_DIR / "app" / "static" / "index.html").read_text(encoding="utf-8")
            need = ("tm-udpport", "tm-udpallow", "UDP 端口(公网)", "UnityTransport",
                    "Server Address", "udp-hint", "api/udp/status", "udp_public")
            miss = [k for k in need if k not in html]
            rows.append(("P5 管理页含 UDP 字段与 Unity 指引", not miss, f"缺失={miss}"))
    except Exception as exc:
        # 台架异常(接口 500/网络/绑定失败)也要落进记录, 不能中断本轮
        rows.append(("P9 台架异常(接口/服务端报错)", False,
                       repr(exc) + " " + tail(log_b if p2 else log_a)))
    finally:
        stop_server(p, fh)
        stop_server(p2, fh2)

    n_ok = sum(1 for r in rows if r[1])
    body = [f"# 第 {i} 轮 测试记录(Unity UDP 原生入口 20 轮回归)", "",
            f"- 时间: {now()}",
            "- 场景: 服务器全新启动 -> selftest 全阶段 A-G + api_check 巡检 -> 建 udp 隧道配公网 UDP 端口 -> LAN 离线数据报丢弃 -> 服务器重启(启动时自动绑定) -> 删除隧道解绑 -> 管理页静态检查",
            "- 期望: 台架 9 项全过, 且 selftest / api_check 退出码 0(全 PASS)",
            f"- 实际: 台架 {n_ok}/{len(rows)} 项通过",
            "- 结果: " + ("通过" if n_ok == len(rows) else "未通过"),
            "- 备注: 服务器日志在 _tmp_rounds/(gitignore); 自测库 server/selftest.db(gitignore)",
            "", "## 逐项", "", "| 检查 | 结果 | 备注 |", "| --- | --- | --- |"]
    for name, ok, detail in rows:
        mark = "PASS" if ok else "FAIL"
        body.append("| " + name + " | " + mark + " | " + str(detail).replace("|", "/") + " |")
    (outdir / f"record_{i}.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    return rows, n_ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Unity UDP 原生入口 20 轮回归台架")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--outdir", default="tests/2026-11-18-13-03-15_cc")
    ap.add_argument("--password", default=os.environ.get("NATTUNNEL_TEST_PASSWORD", ""))
    if hasattr(sys.stdout, "reconfigure"):   # 控制台(Windows cp936)输出 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = ap.parse_args()
    if not args.password:
        ap.error("缺少管理员密码: --password 或环境变量 NATTUNNEL_TEST_PASSWORD")
    outdir = ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    total = []
    for i in range(args.start, args.start + args.rounds):
        t0 = time.time()
        rows, n_ok = run_round(i, args.password, outdir)
        used = int(time.time() - t0)
        total.append((i, n_ok, len(rows), used))
        print(f"[round {i}] {n_ok}/{len(rows)} 通过, 用时 {used}s")
        for name, ok, detail in rows:
            mark = "ok" if ok else "FAIL"
            print(f"  [{mark}] {name}" + (f"  {detail}" if not ok else ""))
    print("SUMMARY " + " ".join(f"r{i}:{n}/{t}" for i, n, t, _ in total))


if __name__ == "__main__":
    main()
