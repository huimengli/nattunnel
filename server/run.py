"""nattunnel 后端入口。

在 server/ 目录下运行:
    python run.py                 # host/port 取自 .env(HOST/PORT, 默认 127.0.0.1:8000)
    python run.py --port 9000     # CLI 覆盖端口(如混部服务器上 8000 被占用); --host 同理
等价于: uvicorn app.main:app --host ... --port ... --workers 1
注意: 中继状态保存在进程内存中, 必须单 worker 运行。
"""
import argparse

import uvicorn

from app.config import HOST, PORT


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="nattunnel server")
    parser.add_argument("--host", default=HOST, help=f"监听地址(默认 {HOST}, 来自 .env)")
    parser.add_argument("--port", type=int, default=PORT, help=f"监听端口(默认 {PORT}, 来自 .env)")
    args = parser.parse_args()

    uvicorn.run("app.main:app", host=args.host, port=args.port, workers=1, log_level="info")
