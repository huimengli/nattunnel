"""密码哈希 / JWT / RSA 握手密钥管理。"""
import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from .config import JWT_ALGORITHM, JWT_EXPIRE_DAYS, JWT_SECRET
from .database import SessionLocal
from .models import AppSetting

PBKDF2_ITER = 300_000


# ---------------------------------------------------------------- 密码哈希

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)
    return f"pbkdf2_sha256${PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(calc, bytes.fromhex(digest_hex))
    except Exception:
        return False


# ---------------------------------------------------------------- JWT

_jwt_secret_cache = None


def resolve_jwt_secret() -> str:
    """优先取 .env; 否则从 app_settings 读取(不存在则生成并持久化)。"""
    global _jwt_secret_cache
    if _jwt_secret_cache:
        return _jwt_secret_cache
    if JWT_SECRET:
        _jwt_secret_cache = JWT_SECRET
        return _jwt_secret_cache
    db = SessionLocal()
    try:
        row = db.get(AppSetting, "jwt_secret")
        if row is None:
            row = AppSetting(key="jwt_secret", value=secrets.token_urlsafe(48))
            db.add(row)
            db.commit()
        _jwt_secret_cache = row.value
        return _jwt_secret_cache
    finally:
        db.close()


def create_access_token(username: str, role: str, tunnel_id: str = None):
    """签发 JWT; tunnel_id 非空时令牌绑定该隧道(客户端凭令牌确定隧道配置)。"""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=JWT_EXPIRE_DAYS)
    payload = {"sub": username, "role": role, "iat": int(now.timestamp()), "exp": int(expire.timestamp())}
    if tunnel_id:
        payload["tunnel_id"] = tunnel_id
    token = jwt.encode(payload, resolve_jwt_secret(), algorithm=JWT_ALGORITHM)
    return token, JWT_EXPIRE_DAYS * 86400


def decode_token(token: str) -> dict:
    return jwt.decode(token, resolve_jwt_secret(), algorithms=[JWT_ALGORITHM])


# ---------------------------------------------------------------- RSA 握手

def _set_setting(db, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def ensure_rsa_keys(db):
    """返回 (private_pem, public_pem); 不存在则生成 2048 位密钥并入库。"""
    priv = db.get(AppSetting, "rsa_private_key")
    pub = db.get(AppSetting, "rsa_public_key")
    if priv is not None and pub is not None:
        return priv.value, pub.value
    key = generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode("ascii")
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    _set_setting(db, "rsa_private_key", priv_pem)
    _set_setting(db, "rsa_public_key", pub_pem)
    db.commit()
    return priv_pem, pub_pem


def rsa_public_key(db) -> str:
    _, pub_pem = ensure_rsa_keys(db)
    return pub_pem


def decrypt_login_payload(db, secure_payload_b64: str):
    """解密登录报文, 返回 (username, password)。"""
    priv_pem, _ = ensure_rsa_keys(db)
    key = serialization.load_pem_private_key(priv_pem.encode("ascii"), password=None)
    raw = base64.b64decode(secure_payload_b64)
    data = key.decrypt(raw, rsa_padding.PKCS1v15())
    obj = json.loads(data.decode("utf-8"))
    return str(obj["username"]), str(obj["password"])
