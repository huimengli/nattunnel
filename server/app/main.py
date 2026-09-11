"""nattunnel FastAPI 应用 + /tunnel/{tid} WebSocket 中继。

角色判定:
  - LAN 侧(客户端 exe): 握手携带有效 Bearer JWT, 且为隧道属主或管理员;
  - 公网侧: 其余连接(无需鉴权), 每个连接即一条独立流。
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .config import WS_MAX_SIZE
from .database import SessionLocal
from .models import Tunnel, User
from .relay import (
    T_CLOSE,
    T_DATA,
    T_FIN,
    T_HELLO,
    T_NEW,
    U_DATA,
    get_redis,
    hub,
    lan_present_key,
    make_frame,
    split_frame,
)
from .routers import auth, tunnels, users
from .security import decode_token
from .seed import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nattunnel.main")


@asynccontextmanager
async def lifespan(_app):
    init_db()
    try:
        get_redis().ping()
        log.info("redis ok")
    except Exception as exc:
        log.warning("redis unavailable (%s); 在线状态/登录限流降级", exc)
    log.info("nattunnel ready (single worker required)")
    yield


app = FastAPI(title="nattunnel", version="0.1.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tunnels.router)


@app.get("/api/health")
def health():
    redis_ok = True
    try:
        get_redis().ping()
    except Exception:
        redis_ok = False
    return {"ok": True, "redis": redis_ok}


# ---------------------------------------------------------------- helpers

async def _send_safe(ws, type_: int, peer_id: int, payload: bytes = b"") -> None:
    try:
        await ws.send_bytes(make_frame(type_, peer_id, payload))
    except Exception:
        pass  # 对端已断开, 由接收循环感知


async def _register_lan(room, ws) -> None:
    """注册 LAN 侧; 顶替旧连接, 并通报已有公网流。"""
    if room.lan is not None:
        old = room.lan
        room.lan = None
        try:
            await old.close(code=4410, reason="replaced by new lan link")
        except Exception:
            pass
    room.lan = ws
    for pid in list(room.pubs):
        await _send_safe(ws, T_NEW, pid)
    try:
        from .relay import set_lan_present

        set_lan_present(room.tunnel_id, True)
    except Exception:
        pass


async def _register_pub(room, ws) -> int:
    """注册公网侧: 分配 peer_id, 通知双方。"""
    pid = room.next_peer
    room.next_peer += 1
    room.pubs[pid] = ws
    await _send_safe(ws, T_HELLO, pid)
    if room.lan is not None:
        await _send_safe(room.lan, T_NEW, pid)
    return pid


async def _cleanup_pub(room, pid) -> None:
    """移除公网侧并通知 LAN 侧拆除流。"""
    ws = room.pubs.pop(pid, None)
    if ws is not None and room.lan is not None:
        await _send_safe(room.lan, T_CLOSE, pid)


# ---------------------------------------------------------------- endpoint

@app.websocket("/tunnel/{tid}")
async def tunnel_ws(tid: str, websocket: WebSocket):
    db = SessionLocal()
    room = None
    role = None
    pid = None
    try:
        tunnel = db.query(Tunnel).filter_by(tunnel_id=tid).first()

        # ---- 角色判定(不泄露隧道存在性) ----
        is_lan = False
        authz = websocket.headers.get("authorization", "")
        if tunnel is not None and tunnel.enabled and authz.lower().startswith("bearer "):
            try:
                payload_jwt = decode_token(authz[7:].strip())
                owner_or_admin = db.query(User).filter_by(username=payload_jwt.get("sub", "")).first()
                if owner_or_admin is not None and (
                    owner_or_admin.role == "admin" or owner_or_admin.id == tunnel.owner_id
                ):
                    is_lan = True
            except Exception:
                pass

        await websocket.accept()
        if tunnel is None or not tunnel.enabled:
            log.info("tunnel %s: 未找到或已停用 -> close 4004", tid)
            await websocket.close(code=4004)
            return

        room = hub.room(tid, tunnel.proto)
        data_types = {T_DATA} if tunnel.proto == "tcp" else {U_DATA}
        role = "lan" if is_lan else "pub"
        log.info("tunnel %s: %s connected (role=%s)", tid, websocket.client, role)

        if is_lan:
            await _register_lan(room, websocket)
        else:
            pid = await _register_pub(room, websocket)

        # ---- 转发主循环 ----
        while True:
            msg = await websocket.receive_bytes()
            type_, fpid, payload = split_frame(msg)
            if role == "lan":
                if type_ in data_types:
                    peer_ws = room.pubs.get(fpid)
                    if peer_ws is not None:
                        await _send_safe(peer_ws, type_, fpid, payload)
                elif type_ == T_CLOSE:
                    peer_ws = room.pubs.pop(fpid, None)
                    if peer_ws is not None:
                        try:
                            await peer_ws.close(code=1000)
                        except Exception:
                            pass
            else:
                if fpid != pid:
                    continue  # 只信任自己的 peer_id
                if type_ in data_types or type_ == T_FIN:
                    lan = room.lan
                    if lan is not None:
                        await _send_safe(lan, type_, pid, payload)
                elif type_ == T_CLOSE:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("tunnel %s relay error: %s", tid, exc)
    finally:
        if room is not None:
            if role == "lan" and room.lan is websocket:
                room.lan = None
                for p in list(room.pubs):
                    try:
                        await room.pubs[p].close(code=4410, reason="relay offline")
                    except Exception:
                        pass
                room.pubs.clear()
            elif role == "pub" and room.pubs.get(pid) is websocket:
                await _cleanup_pub(room, pid)
            if room.lan is None and not room.pubs:
                hub.drop(tid)
        if role == "lan":
            try:
                get_redis().delete(lan_present_key(tid))
            except Exception:
                pass


if __name__ == "__main__":
    import uvicorn

    from .config import HOST, PORT

    uvicorn.run(app, host=HOST, port=PORT)
