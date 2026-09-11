#!/usr/bin/env python3
"""公网侧 TCP 隧道测试工具(扮演互联网用户)。

连接 wss://www.xxx.com/tunnel/<ID>, 将 stdin/stdout 与该隧道双向转发:

    python tools/ws_tcp_test.py wss://www.xxx.com/tunnel/XgMacp2G
    # 例如测试远端 RDP/任意 TCP 服务时, 也可用 nc 经 ws 桥接
    # Ctrl+Z(Windows)/Ctrl+D(Linux) 发送 EOF

依赖: pip install websockets
"""
import argparse
import asyncio
import sys

import websockets

T_NEW = 0x01
T_DATA = 0x02
T_FIN = 0x03
T_CLOSE = 0x04
T_HELLO = 0x05


def build_frame(type_, peer_id, payload=b""):
    return bytes([type_]) + peer_id.to_bytes(4, "big") + payload


def split_frame(data):
    if len(data) < 5:
        raise ValueError("frame too short")
    return data[0], int.from_bytes(data[1:5], "big"), data[5:]


async def main() -> None:
    parser = argparse.ArgumentParser(description="nattunnel public-side TCP test")
    parser.add_argument("url", help="wss://www.xxx.com/tunnel/<ID>")
    args = parser.parse_args()

    async with websockets.connect(args.url, max_size=1 << 20) as ws:
        # 等待服务器分配 peer_id
        pid = None
        while pid is None:
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            type_, pid_candidate, _ = split_frame(msg)
            if type_ == T_HELLO:
                pid = pid_candidate
        print(f"[ok] connected as public peer_id={pid}", file=sys.stderr)

        out_q = asyncio.Queue()
        closed = asyncio.Event()

        async def pump_ws():
            while True:
                try:
                    msg = await ws.recv()
                except websockets.exceptions.ConnectionClosed:
                    closed.set()
                    return
                type_, pid2, payload = split_frame(msg)
                if pid2 != pid:
                    continue
                if type_ == T_DATA:
                    out_q.put_nowait(payload)
                elif type_ in (T_FIN, T_CLOSE):
                    closed.set()
                    return

        async def pump_stdin():
            loop = asyncio.get_running_loop()
            while not closed.is_set():
                data = await loop.run_in_executor(None, lambda: sys.stdin.buffer.read(65536))
                if not data:
                    closed.set()
                    return
                await ws.send(build_frame(T_DATA, pid, data))

        async def pump_stdout():
            while True:
                data = await out_q.get()
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
                if closed.is_set() and out_q.empty():
                    return

        tasks = [
            asyncio.create_task(pump_ws()),
            asyncio.create_task(pump_stdin()),
            asyncio.create_task(pump_stdout()),
        ]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        print("[ok] tunnel closed", file=sys.stderr)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
