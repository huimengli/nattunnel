"""隧道配置管理(属主或管理员)。"""
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Tunnel, User
from ..relay import hub, lan_online, push_config_to_lan
from ..schemas import TunnelCreate, TunnelOut, TunnelUpdate

log = logging.getLogger("nattunnel.tunnels")

router = APIRouter(prefix="/api", tags=["tunnels"])

# 自动生成用字母表(去掉易混淆的 0 O 1 l I)
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"


def _gen_tunnel_id(db: Session) -> str:
    for _ in range(64):
        tid = "".join(secrets.choice(ALPHABET) for _ in range(8))
        if db.query(Tunnel).filter_by(tunnel_id=tid).first() is None:
            return tid
    raise HTTPException(status_code=409, detail="could not allocate tunnel id")


def _get_tunnel(db: Session, tid: str) -> Tunnel:
    tunnel = db.query(Tunnel).filter_by(tunnel_id=tid).first()
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    return tunnel


def _check_access(user: User, tunnel: Tunnel) -> None:
    if user.role != "admin" and user.id != tunnel.owner_id:
        raise HTTPException(status_code=403, detail="not your tunnel")


def _to_out(tunnel: Tunnel, owner_name: str) -> TunnelOut:
    online, pub_count = hub.status(tunnel.tunnel_id)
    if not online:
        online = lan_online(tunnel.tunnel_id)
    return TunnelOut(
        tunnel_id=tunnel.tunnel_id,
        owner=owner_name,
        name=tunnel.name or "",
        proto=tunnel.proto,
        local_port=tunnel.local_port,
        bandwidth_kbps=tunnel.bandwidth_kbps,
        enabled=bool(tunnel.enabled),
        lan_online=online,
        pub_count=pub_count,
        created_at=tunnel.created_at,
    )


@router.get("/tunnels", response_model=list[TunnelOut])
def list_tunnels(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    q = db.query(Tunnel)
    if user.role != "admin":
        q = q.filter(Tunnel.owner_id == user.id)
    items = []
    for t in q.order_by(Tunnel.id).all():
        owner = db.query(User).filter_by(id=t.owner_id).first()
        items.append(_to_out(t, owner.username if owner else "?"))
    return items


@router.post("/tunnels", response_model=TunnelOut, status_code=201)
def create_tunnel(
    body: TunnelCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    tid = body.tunnel_id or _gen_tunnel_id(db)
    if body.tunnel_id is not None and (
        db.query(Tunnel).filter_by(tunnel_id=body.tunnel_id).first() is not None
    ):
        raise HTTPException(status_code=409, detail="tunnel id already exists")
    tunnel = Tunnel(
        tunnel_id=tid,
        owner_id=user.id,
        name=body.name,
        proto=body.proto,
        local_port=body.local_port,
        bandwidth_kbps=body.bandwidth_kbps,
        enabled=True,
    )
    db.add(tunnel)
    db.commit()
    db.refresh(tunnel)
    log.info("user '%s' created tunnel %s (%s:%s, %skbps)", user.username, tid, body.proto, body.local_port, body.bandwidth_kbps)
    return _to_out(tunnel, user.username)


@router.get("/tunnels/{tid}", response_model=TunnelOut)
def get_tunnel(tid: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tunnel = _get_tunnel(db, tid)
    _check_access(user, tunnel)
    owner = db.query(User).filter_by(id=tunnel.owner_id).first()
    return _to_out(tunnel, owner.username if owner else "?")


@router.patch("/tunnels/{tid}", response_model=TunnelOut)
async def update_tunnel(
    tid: str, body: TunnelUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    tunnel = _get_tunnel(db, tid)
    _check_access(user, tunnel)
    data = body.model_dump(exclude_unset=True)
    proto_before = tunnel.proto
    for field, value in data.items():
        setattr(tunnel, field, value)
    db.commit()
    db.refresh(tunnel)
    # 配置热更新:
    #   协议变更 -> 所有现有流失效, 拆除房间(客户端会自动重连并拉新配置);
    #   端口/带宽变更 -> T_CONFIG 推给在线 LAN 侧, 现有连接保持。
    if "proto" in data and data["proto"] != proto_before:
        hub.drop(tid)
    elif {"local_port", "bandwidth_kbps"} & data.keys():
        await push_config_to_lan(
            hub.get(tid), tunnel.proto, tunnel.local_port, tunnel.bandwidth_kbps
        )
    owner = db.query(User).filter_by(id=tunnel.owner_id).first()
    log.info("user '%s' updated tunnel %s: %s", user.username, tid, data)
    return _to_out(tunnel, owner.username if owner else "?")


@router.delete("/tunnels/{tid}", status_code=204)
def delete_tunnel(tid: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tunnel = _get_tunnel(db, tid)
    _check_access(user, tunnel)
    hub.drop(tunnel.tunnel_id)
    db.delete(tunnel)
    db.commit()
    log.info("user '%s' deleted tunnel %s", user.username, tid)
