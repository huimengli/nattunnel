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

# 监听地址: 默认只绑本机回环 — nginx 反代转发到 127.0.0.1:8000;
# 混部服务器上保持默认, 避免 FastAPI(含管理页/隧道 WS) 不经 TLS 直接暴露公网。
HOST = _get("HOST", "127.0.0.1")
PORT = int(_get("PORT", "8000") or 8000)

# WebSocket 单帧最大载荷(1 MiB), 客户端分片远小于此值
WS_MAX_SIZE = 1 << 20

# ---------------- UDP 原生入口(标准 UDP 端口映射, 供 Unity Transport 等标准 UDP 客户端) ----------------
# 监听地址: 公网 UDP 必须直接到达本进程(不经 nginx), 默认全网卡; 本机自测/混部建议 127.0.0.1
UDP_LISTEN_HOST = _get("UDP_LISTEN_HOST", "0.0.0.0")
# 管理页展示的公网地址(填服务器公网 IP; Unity 的 Server Address 官方语义是 IPv4)。留空则显示监听地址
UDP_PUBLIC_HOST = _get("UDP_PUBLIC_HOST", "")
# 对端(源地址)空闲回收时间(秒): 无数据报超过该时间即拆该对端并通知 LAN 侧
UDP_IDLE_TIMEOUT = int(_get("UDP_IDLE_TIMEOUT", "60") or 60)
# 单隧道最大并发对端数(源地址数)
UDP_MAX_PEERS = int(_get("UDP_MAX_PEERS", "64") or 64)
# 单个数据报最大字节(超过丢弃; Unity Netcode 的 MTU 一般 1400, 64KiB 足够)
UDP_MAX_DATAGRAM = int(_get("UDP_MAX_DATAGRAM", "65536") or 65536)
