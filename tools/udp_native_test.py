#!/usr/bin/env python3
"""公网侧 **UDP 原生入口** 测试工具(标准 UDP, 完全不走 WebSocket)。

对应隧道配置: proto=udp + udp_port(公网 UDP 端口)。本工具模拟 Unity Netcode(UnityTransport)那种
"只能讲标准 UDP"的客户端: 向 `<host>:<udp_port>` 发数据报, 收内网服务回包。

用法:
    # 一次性: 发一个数据报, 打印收到的回包(可多次发送, 每行 --send)
    python tools/udp_native_test.py 127.0.0.1:16000 --send "ping" --wait 2

    # 交互: 每行 stdin = 一个数据报; Ctrl+Z/Ctrl+D 退出
    python tools/udp_native_test.py 1.2.3.4:16000

    # 压测/echo 环路: 连发 N 条并统计回包(检验每个源地址=一个对端的转发正确性)
    python tools/udp_native_test.py 1.2.3.4:16000 --burst 20 --wait 3

依赖: 无(标准库 socket)。
"""
import argparse
import socket
import sys
import time


def parse_target(target: str) -> tuple:
    host, _, port = target.rpartition(":")
    if not host or not port.isdigit():
        parser_error(f"目标需写成 host:port, 收到 {target!r}")
    return host, int(port)


def parser_error(msg: str):
    raise SystemExit(f"参数错误: {msg}")


def main() -> None:
    ap = argparse.ArgumentParser(description="nattunnel UDP 原生入口(非 WS)客户端")
    ap.add_argument("target", help="服务器 UDP 入口, 形如 1.2.3.4:16000")
    ap.add_argument("--send", action="append", default=[], help="要发送的数据报(可重复)")
    ap.add_argument("--burst", type=int, default=0, help="连发 N 条 test-N(与 --send 叠加)")
    ap.add_argument("--wait", type=float, default=2.0, help="等待回包总时长(秒)")
    ap.add_argument("--bind", default=None, help="本机绑定地址(如 127.0.0.2, 用于测试源 IP 白名单)")
    ap.add_argument("--raw", default=None, help="发一段十六进制(测二进制/MTU)")
    args = ap.parse_args()

    host, port = parse_target(args.target)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(args.wait)
    if args.bind:
        s.bind((args.bind, 0))
    s.connect((host, port))   # connected UDP: 只收该对端的回包(与 UnityTransport 行为一致)
    print(f"[ok] 已连接 UDP {host}:{port} (本机 {s.getsockname()[0]}:{s.getsockname()[1]})", file=sys.stderr)

    payloads = [p.encode("utf-8") if isinstance(p, str) else p for p in args.send]
    for i in range(1, args.burst + 1):
        payloads.append(f"test-{i}".encode("utf-8"))
    if args.raw:
        payloads.append(bytes.fromhex(args.raw))

    if not payloads:
        # 交互模式
        print("[ok] 每行输入一个数据报 (Ctrl+Z/Ctrl+D 退出)", file=sys.stderr)
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                break
            data = line.rstrip(b"\r\n")
            if not data:
                continue
            s.send(data)
            try:
                reply = s.recv(65536)
                sys.stdout.buffer.write(b"[reply] " + reply + b"\n")
                sys.stdout.buffer.flush()
            except socket.timeout:
                print("[warn] 无回包(内网侧离线? 隧道未配 udp_port?)", file=sys.stderr)
        return

    got = 0
    for p in payloads:
        s.send(p)
        print(f"[sent] {p!r} ({len(p)}B)", file=sys.stderr)
    deadline = time.time() + args.wait
    while time.time() < deadline:
        try:
            reply = s.recv(65536)
        except socket.timeout:
            break
        got += 1
        sys.stdout.buffer.write(b"[reply] " + reply + b"\n")
        sys.stdout.buffer.flush()
    print(f"[done] 发送 {len(payloads)} 条, 收到 {got} 条回包", file=sys.stderr)
    if got == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
