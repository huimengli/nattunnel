"""用户管理(仅管理员)。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import Tunnel, User
from ..relay import hub
from ..schemas import UserIn, UserOut
from ..security import hash_password

log = logging.getLogger("nattunnel.users")

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    users = db.query(User).order_by(User.id).all()
    return [UserOut(username=u.username, role=u.role, created_at=u.created_at) for u in users]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    if db.query(User).filter_by(username=body.username).first() is not None:
        raise HTTPException(status_code=409, detail="username already exists")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    log.info("admin created user '%s' (%s)", body.username, body.role)
    return UserOut(username=user.username, role=user.role, created_at=user.created_at)


@router.delete("/users/{username}", status_code=204)
def delete_user(
    username: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    if username == admin.username:
        raise HTTPException(status_code=400, detail="cannot delete yourself")
    user = db.query(User).filter_by(username=username).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    # 移除内存房间, 再级联删除其隧道
    tunnels = db.query(Tunnel).filter_by(owner_id=user.id).all()
    for t in tunnels:
        hub.drop(t.tunnel_id)
    db.query(Tunnel).filter_by(owner_id=user.id).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    log.info("admin deleted user '%s' and %d tunnel(s)", username, len(tunnels))
