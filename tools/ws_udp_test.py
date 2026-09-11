#!/usr/bin/env python3
"""公网侧 UDP 隧道测试工具(扮演互联网用户)。

交互模式(每行 stdin = 一个数据报, 回复打印到 stdout):
    python tools/ws_udp_test.py wss://www.xxx.com/tunnel/<ID>

一次性模式:
    python tools/ws_udp_test.py wss://www.xxx.com/tunnel/<ID> --send "ping" [--wait 2]

依赖: pip install websockets
"""
import argparse
import asyncio
import sys

import websockets

T_NEW = 0x01
T_CLOSE = 0x04
T_HELLO = 0x05
U_DATA = 0x41


def build_frame(type_, peer_id, payload=b""):
    return bytes([type_]) + peer_id.to_bytes(4, "big") + payload


def split_frame(data):
    if len(data) < 5:
        raise ValueError("frame too short")
    return data[0], int.from_bytes(data[1:5], "big"), data[5:]


async def wait_hello(ws):
    while True:
        msg = await asyncio.wait_for(ws.recv(), timeout=15)
        type_, pid, _ = split_frame(msg)
        if type_ == T_HELLO:
            return pid


def print_reply(payload: bytes) -> None:
    sys.stdout.buffer.write(b"[" + str(time_mono()).encode() + b"] " + payload + b"\n")
    sys.stdout.buffer.flush()


import time as _time


def time_mono() -> int:
    return int(_time.time()) % 100000


async def once(url: str, text: bytes, wait_s: float) -> None:
    async with websockets.connect(url, max_size=1 << 20) as ws:
        pid = await wait_hello(ws)
        print(f"[ok] peer_id={pid}, send: {text!r}", file=sys.stderr)
        await ws.send(build_frame(U_DATA, pid, text))
        deadline = asyncio.get_running_loop().time() + wait_s
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError:
                break
            type_, pid2, payload = split_frame(msg)
            if pid2 != pid or type_ != U_DATA:
                continue
            print_reply(payload)


async def interactive(url: str) -> None:
    async with websockets.connect(url, max_size=1 << 20) as ws:
        pid = await wait_hello(ws)
        print(f"[ok] peer_id={pid}; 每行输入发送一个 UDP 数据报 (Ctrl+Z/Ctrl+D 退出)", file=sys.stderr)
        closed = asyncio.Event()

        async def pump_ws():
            while True:
                try:
                    msg = await ws.recv()
                except websockets.exceptions.ConnectionClosed:
                    closed.set()
                    return
                type_, pid2, payload = split_frame(msg)
                if pid2 == pid and type_ == U_DATA:
                    print_reply(payload)

        async def pump_stdin():
            loop = asyncio.get_running_loop()
            while not closed.is_set():
                line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
                if not line or line == b"\n":
                    break
                data = line.rstrip(b"\r\n")
                if not data:
                    continue
                await ws.send(build_frame(U_DATA, pid, data))

        tasks = [asyncio.create_task(pump_ws()), asyncio.create_task(pump_stdin())]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description="nattunnel public-side UDP test")
    parser.add_argument("url", help="wss://www.xxx.com/tunnel/<ID>")
    parser.add_argument("--send", default=None, help="一次性模式: 发送该文本作为数据报")
    parser.add_argument("--wait", type=float, default=2.0, help="--send 模式下等待回复的秒数")
    args = parser.parse_args()

    if args.send is not None:
        await once(args.url, args.send.encode("utf-8"), args.wait)
    else:
        await interactive(args.url)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
