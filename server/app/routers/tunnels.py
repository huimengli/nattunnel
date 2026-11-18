"""隧道配置管理(属主或管理员)。"""
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import UDP_LISTEN_HOST, UDP_PUBLIC_HOST
from ..database import get_db
from ..deps import get_current_user
from ..models import Tunnel, User
from ..relay import hub, lan_online, push_config_to_lan
from ..udp_relay import public_display, udp_hub
from ..schemas import TokenOut, TunnelCreate, TunnelOut, TunnelUpdate
from ..security import create_access_token

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


def _check_udp_port(db: Session, port: int, tid: str | None = None) -> None:
    """公网 UDP 端口唯一性: 同一端口只能属于一条隧道(否则绑定会撞车)。"""
    q = db.query(Tunnel).filter(Tunnel.udp_port == port)
    if tid:
        q = q.filter(Tunnel.tunnel_id != tid)
    hit = q.first()
    if hit is not None:
        raise HTTPException(status_code=409,
                            detail=f"UDP 端口 {port} 已被隧道 {hit.tunnel_id} 占用")


@router.post("/tunnels/{tid}/token", response_model=TokenOut)
def issue_tunnel_token(tid: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """为指定隧道签发绑定令牌(属主或管理员)。

    客户端凭此 token 经 GET /api/me 学到 tunnel_id, 无需在 config.json 里再写隧道 ID。
    """
    tunnel = _get_tunnel(db, tid)
    _check_access(user, tunnel)
    token, expires_in = create_access_token(user.username, user.role, tunnel_id=tid)
    log.info("issued tunnel-bound token for '%s' on %s", user.username, tid)
    return TokenOut(access_token=token, token_type="bearer", expires_in=expires_in)


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
        local_target_host=tunnel.local_target_host or "127.0.0.1",
        bandwidth_kbps=tunnel.bandwidth_kbps,
        header_timeout_s=int(tunnel.header_timeout_s or 30),
        udp_port=int(tunnel.udp_port or 0) or None,
        udp_allow=tunnel.udp_allow or "",
        udp_public=public_display(int(tunnel.udp_port or 0), UDP_PUBLIC_HOST),
        udp_peers=udp_hub.peer_count(tunnel.tunnel_id),
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
async def create_tunnel(
    body: TunnelCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    tid = body.tunnel_id or _gen_tunnel_id(db)
    if body.tunnel_id is not None and (
        db.query(Tunnel).filter_by(tunnel_id=body.tunnel_id).first() is not None
    ):
        raise HTTPException(status_code=409, detail="tunnel id already exists")
    # UDP 原生入口只对 udp 隧道生效(非 udp 隧道的公网入口是 HTTP/WS)
    is_udp = body.proto == "udp"
    udp_port = int(body.udp_port) if (is_udp and body.udp_port) else None
    udp_allow = (body.udp_allow or "").strip() if is_udp else ""
    if udp_port:
        _check_udp_port(db, udp_port)
    tunnel = Tunnel(
        tunnel_id=tid,
        owner_id=user.id,
        name=body.name,
        proto=body.proto,
        local_port=body.local_port,
        local_target_host=(body.local_target_host or "127.0.0.1").strip(),
        bandwidth_kbps=body.bandwidth_kbps,
        header_timeout_s=body.header_timeout_s,
        udp_port=udp_port,
        udp_allow=udp_allow,
        enabled=True,
    )
    db.add(tunnel)
    db.commit()
    db.refresh(tunnel)
    if udp_port:
        await udp_hub.sync(tunnel)   # 绑定公网 UDP 端口(失败已在 udp_relay 记日志)
    log.info(
        "user '%s' created tunnel %s (%s:%s, %skbps, 响应头窗口 %ss, UDP 入口 %s)",
        user.username, tid, body.proto, body.local_port, body.bandwidth_kbps, body.header_timeout_s,
        public_display(udp_port, UDP_PUBLIC_HOST) or "无",
    )
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
    # udp_port / udp_allow 只对 udp 隧道有意义(非 udp 隧道清空, 以免管理页留下无意义的配置)
    if "proto" in data and data["proto"] != "udp":
        data["udp_port"] = None
        data["udp_allow"] = ""
    if data.get("udp_port"):
        _check_udp_port(db, int(data["udp_port"]), tid)
    for field, value in data.items():
        setattr(tunnel, field, value)
    db.commit()
    db.refresh(tunnel)
    # UDP 原生入口按新配置绑定/解绑(端口不变时只热刷新白名单与启用状态)
    await udp_hub.sync(tunnel)
    # 配置热更新:
    #   协议变更 -> 所有现有流失效, 拆除房间(客户端会自动重连并拉新配置);
    #   端口/带宽/目标主机变更 -> T_CONFIG 推给在线 LAN 侧, 现有连接保持;
    #   header_timeout_s / udp_port / udp_allow 是服务端参数, 不推 T_CONFIG。
    if "proto" in data and data["proto"] != proto_before:
        hub.drop(tid)
    elif {"local_port", "bandwidth_kbps", "local_target_host"} & data.keys():
        await push_config_to_lan(
            hub.get(tid), tunnel.proto, tunnel.local_port, tunnel.bandwidth_kbps,
            tunnel.local_target_host,
        )
    owner = db.query(User).filter_by(id=tunnel.owner_id).first()
    log.info("user '%s' updated tunnel %s: %s", user.username, tid, data)
    return _to_out(tunnel, owner.username if owner else "?")


@router.delete("/tunnels/{tid}", status_code=204)
async def delete_tunnel(tid: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tunnel = _get_tunnel(db, tid)
    _check_access(user, tunnel)
    await udp_hub.unbind(tunnel.tunnel_id, "隧道删除")
    hub.drop(tunnel.tunnel_id)
    db.delete(tunnel)
    db.commit()
    log.info("user '%s' deleted tunnel %s", user.username, tid)
