"""nattunnel 后端入口。

在 server/ 目录下运行:  python run.py
等价于: uvicorn app.main:app --host 0.0.0.0 --port 8000
注意: 中继状态保存在进程内存中, 必须单 worker 运行。
"""
import uvicorn

from app.config import HOST, PORT


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=HOST, port=PORT, workers=1, log_level="info")
