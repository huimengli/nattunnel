"""运行配置: 从 server/.env 与环境变量读取。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # .../nattunnel/server
load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


# MySQL 连接串(默认与 docker-compose 保持一致)
DATABASE_URL = _get(
    "DATABASE_URL",
    "mysql+pymysql://nattunnel:nattunnel@127.0.0.1:3306/nattunnel?charset=utf8mb4",
)

# Redis 连接串
REDIS_URL = _get("REDIS_URL", "redis://127.0.0.1:6379/0")

# JWT: 留空 => 首次启动自动生成并持久化到 app_settings 表
JWT_SECRET = _get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = int(_get("JWT_EXPIRE_DAYS", "7") or 7)

# 初始管理员: 仅在 users 表没有管理员时, 于首次启动创建
# 用户名留空默认 "admin"; 密码留空则生成随机密码并在启动日志中一次性打印
INITIAL_ADMIN_USERNAME = _get("INITIAL_ADMIN_USERNAME", "admin")
INITIAL_ADMIN_PASSWORD = _get("INITIAL_ADMIN_PASSWORD", "")

HOST = _get("HOST", "0.0.0.0")
PORT = int(_get("PORT", "8000") or 8000)

# WebSocket 单帧最大载荷(1 MiB), 客户端分片远小于此值
WS_MAX_SIZE = 1 << 20
