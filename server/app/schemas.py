"""Pydantic 请求/响应模型。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    """两种登录形式(二选一):
    1) secure_payload: base64(RSA 公钥加密(JSON{username,password})) — exe 客户端;
    2) username + password: 直接传输 — 网页管理端(必须经 TLS)。
    """
    secure_payload: Optional[str] = Field(default=None, min_length=1, max_length=2048)
    username: Optional[str] = Field(default=None, min_length=1, max_length=32)
    password: Optional[str] = Field(default=None, min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class PasswordChangeIn(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class UserIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field(default="user", pattern=r"^(admin|user)$")


class UserOut(BaseModel):
    username: str
    role: str
    created_at: Optional[datetime] = None


class TunnelCreate(BaseModel):
    tunnel_id: Optional[str] = Field(
        default=None, pattern=r"^[A-Za-z0-9]{8}$",
        description="8 位短链; 不填则自动生成",
    )
    name: str = Field(default="", max_length=64)
    proto: str = Field(default="tcp", pattern=r"^(tcp|udp)$")
    local_port: int = Field(..., ge=1, le=65535)
    # 本机目标主机(客户端转发流量的对端), 默认回环
    local_target_host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    bandwidth_kbps: int = Field(default=0, ge=0)


class TunnelUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    proto: Optional[str] = Field(default=None, pattern=r"^(tcp|udp)$")
    local_port: Optional[int] = Field(default=None, ge=1, le=65535)
    local_target_host: Optional[str] = Field(default=None, min_length=1, max_length=255)
    bandwidth_kbps: Optional[int] = Field(default=None, ge=0)
    enabled: Optional[bool] = None


class TunnelOut(BaseModel):
    tunnel_id: str
    owner: str
    name: str
    proto: str
    local_port: int
    local_target_host: str
    bandwidth_kbps: int
    enabled: bool
    lan_online: bool = False
    pub_count: int = 0
    created_at: Optional[datetime] = None
