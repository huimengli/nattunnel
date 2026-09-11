"""认证: RSA 公钥下发 / 登录(JWT) / 当前用户。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..relay import login_fail_count
from ..schemas import LoginIn, PasswordChangeIn, TokenOut
from ..security import create_access_token, decrypt_login_payload, hash_password, rsa_public_key, verify_password

log = logging.getLogger("nattunnel.auth")

router = APIRouter(prefix="/api", tags=["auth"])

LOGIN_MAX_FAILS = 8  # 60s 窗口内


@router.get("/auth/public-key")
def public_key(db: Session = Depends(get_db)):
    """客户端登录前获取 RSA 公钥(PKCS#8 / SPKI PEM)。"""
    return {"public_key": rsa_public_key(db)}


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    if body.secure_payload:
        # RSA 握手: 用户名+密码经服务器公钥加密后传输(exe 客户端)
        try:
            username, password = decrypt_login_payload(db, body.secure_payload)
        except Exception as exc:
            log.warning("login payload decrypt failed: %s", exc)
            raise HTTPException(status_code=400, detail="bad secure payload")
    elif body.username and body.password:
        # 网页管理端: JSON 凭据(依赖 TLS 保护)
        username, password = body.username.strip(), body.password
    else:
        raise HTTPException(status_code=400, detail="provide secure_payload or username+password")

    if login_fail_count(username) >= LOGIN_MAX_FAILS:
        raise HTTPException(status_code=429, detail="too many failed attempts, try later")

    user = db.query(User).filter_by(username=username).first()
    ok = user is not None and verify_password(password, user.password_hash)
    if not ok:
        n = login_fail_count(username, record=True)
        log.info("login failed for '%s' (%d in window)", username, n)
        raise HTTPException(status_code=401, detail="invalid credentials")

    token, expires_in = create_access_token(user.username, user.role)
    return TokenOut(access_token=token, token_type="bearer", expires_in=expires_in)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "role": user.role, "created_at": user.created_at}


@router.post("/auth/token", response_model=TokenOut)
def issue_token(user: User = Depends(get_current_user)):
    """为当前登录用户签发新的 authtoken(网页管理端给 exe 客户端用)。"""
    token, expires_in = create_access_token(user.username, user.role)
    log.info("issued new token for '%s'", user.username)
    return TokenOut(access_token=token, token_type="bearer", expires_in=expires_in)


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChangeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改本人密码(首次启动的临时密码请立即修改)。"""
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=401, detail="old password incorrect")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    log.info("user '%s' changed password", user.username)
