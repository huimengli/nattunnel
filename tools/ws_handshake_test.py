#!/usr/bin/env python3
"""公网侧 WebSocket 握手桥接测试(验证"tcp/http 隧道支持 WebSocket 握手")。

像浏览器页面里的 JS 那样连接隧道入口(不声明 nattunnel-relay 子协议 => 握手桥接到内网 WS 服务):

    python tools/ws_handshake_test.py wss://www.xxx.com/tunnel/<ID>[/路径] [--send 文本] [--sub chat]

行为:
  -- 打印握手结果(状态/协商到的子协议)与内网 WS 服务的回显;
  -- 不声明子协议时等价于浏览器 new WebSocket(...) 的能力(浏览器不能自定义头)。

对比: tools/ws_tcp_test.py(帧协议裸流, 子协议 nattunnel-relay) / tools/ws_udp_test.py(UDP)。
依赖: pip install websockets
"""
import argparse
import asyncio
import json
import sys

import websockets


async def main() -> None:
    parser = argparse.ArgumentParser(description="nattunnel WS handshake bridge test")
    parser.add_argument("url", help="wss://www.xxx.com/tunnel/<ID>[/路径]")
    parser.add_argument("--send", default="hello-ws", help="发送的一条消息(文本帧)")
    parser.add_argument("--sub", default=None, help="请求的 WS 子协议(如 chat)")
    parser.add_argument("--wait", type=float, default=5.0, help="等待回显秒数")
    args = parser.parse_args()

    subs = [args.sub] if args.sub else []
    try:
        async with websockets.connect(args.url, max_size=1 << 20, subprotocols=subs) as ws:
            print(json.dumps({
                "ok": True,
                "url": args.url,
                "requested_subprotocols": subs,
                "negotiated_subprotocol": ws.subprotocol,
                "extensions": ws.extensions and list(ws.extensions.keys()),
            }, ensure_ascii=False))
            await ws.send(args.send)
            try:
                reply = await asyncio.wait_for(ws.recv(), timeout=args.wait)
                print("echo:", reply if isinstance(reply, str) else reply.decode("utf-8", "replace"))
            except asyncio.TimeoutError:
                print("[warn] 无回显(内网服务不回显该消息)", file=sys.stderr)
    except websockets.exceptions.InvalidStatus as e:
        resp = e.response
        print(json.dumps({
            "ok": False,
            "url": args.url,
            "status": getattr(resp, "status_code", None),
            "reason": getattr(resp, "reason_phrase", None) or str(e),
            "body": getattr(resp, "body", b"").decode("utf-8", "replace"),
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
