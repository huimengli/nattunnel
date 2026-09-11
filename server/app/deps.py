"""FastAPI 鉴权依赖。"""
from fastapi import Depends, HTTPException, Request

from .database import get_db
from .models import User
from .security import decode_token


def _bearer_token(request: Request) -> str:
    authz = request.headers.get("authorization", "")
    if not authz.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authz[7:].strip()


def get_current_user(request: Request, db=Depends(get_db)) -> User:
    try:
        payload = decode_token(_bearer_token(request))
    except Exception:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    user = db.query(User).filter_by(username=payload.get("sub", "")).first()
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user


def get_current_payload(request: Request) -> dict:
    """当前请求的解码 JWT payload(含可选 tunnel_id 绑定声明)。"""
    try:
        return decode_token(_bearer_token(request))
    except Exception:
        raise HTTPException(status_code=401, detail="invalid or expired token")


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin required")
    return user
