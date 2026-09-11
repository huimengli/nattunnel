"""ORM 模型: 用户 / 隧道 / 应用设置。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin | user
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Tunnel(Base):
    __tablename__ = "tunnels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 8 位短链, 公网入口: /tunnel/<tunnel_id>
    tunnel_id: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    proto: Mapped[str] = mapped_column(String(4), default="tcp")  # tcp | udp
    # “前端端口”: 本机被转发服务监听的端口
    local_port: Mapped[int] = mapped_column(Integer)
    # 带宽上限(kbps), 0 = 不限制; 由客户端执行令牌桶限流
    bandwidth_kbps: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AppSetting(Base):
    """键值设置: RSA 密钥对 / 自动生成的 JWT 密钥等。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
