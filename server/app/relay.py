"""隧道中继: 进程内 Room 注册表 + Redis 在线状态/登录限流。

帧格式(所有 WS 消息均为二进制, 统一 5 字节头):
    [type 1B][peer_id 4B big-endian][payload ...]

type 定义:
    0x01 NEW_STREAM   server -> LAN      公网对端已连接(peer_id)
    0x02 DATA         双向               TCP 字节流
    0x03 FIN          双向               TCP 半关/流结束
    0x04 CLOSE        双向               流拆除
    0x05 HELLO        server -> 公网侧   分配 peer_id
    0x21 CONFIG       server -> LAN      配置热更新 JSON{proto, local_port, local_target_host, bandwidth_kbps}
    0x41 DATA(UDP)    双向               UDP 数据报(peer_id 定位对端)

角色:
    LAN 侧  = 客户端 exe, 连接时携带有效 Bearer JWT 且是隧道属主/管理员;
    公网侧  = 其他任何连接(浏览器测试、ws 工具等), 每连接即一条流。
"""
import json
import logging

import redis as redis_lib

from .config import REDIS_URL

log = logging.getLogger("nattunnel.relay")

T_NEW = 0x01
T_DATA = 0x02
T_FIN = 0x03
T_CLOSE = 0x04
T_HELLO = 0x05
T_CONFIG = 0x21
U_DATA = 0x41

_redis = None


def get_redis() -> redis_lib.Redis:
    global _redis
    if _redis is None:
        _redis = redis_lib.from_url(REDIS_URL, decode_responses=True)
    return _redis


def make_frame(type_: int, peer_id: int, payload: bytes = b"") -> bytes:
    return bytes([type_]) + peer_id.to_bytes(4, "big") + payload


def split_frame(data: bytes):
    if len(data) < 5:
        raise ValueError("frame too short")
    return data[0], int.from_bytes(data[1:5], "big"), data[5:]


class Room:
    __slots__ = ("tunnel_id", "proto", "lan", "pubs", "next_peer")

    def __init__(self, tunnel_id: str, proto: str):
        self.tunnel_id = tunnel_id
        self.proto = proto
        self.lan = None          # LAN 侧 websocket(每隧道至多一个)
        self.pubs = {}           # peer_id -> 公网侧 websocket
        self.next_peer = 1


class Hub:
    """进程内房间表(单 worker 前提)。"""

    def __init__(self):
        self._rooms = {}

    def room(self, tunnel_id: str, proto: str) -> Room:
        r = self._rooms.get(tunnel_id)
        if r is None or r.proto != proto:
            r = Room(tunnel_id, proto)
            self._rooms[tunnel_id] = r
        return r

    def drop(self, tunnel_id: str) -> None:
        self._rooms.pop(tunnel_id, None)

    def get(self, tunnel_id: str) -> Room | None:
        """不创建房间的只读查询(用于配置推送)。"""
        return self._rooms.get(tunnel_id)

    def status(self, tunnel_id: str):
        r = self._rooms.get(tunnel_id)
        if r is None:
            return False, 0
        return r.lan is not None, len(r.pubs)


hub = Hub()


async def push_config_to_lan(
    room: Room | None, proto: str, local_port: int, bandwidth_kbps: int, target_host: str = "127.0.0.1"
) -> bool:
    """T_CONFIG 热更新: 把最新配置推给在线的 LAN 侧。无 LAN/发送失败返回 False。"""
    if room is None or room.lan is None:
        return False
    payload = json.dumps(
        {
            "proto": proto,
            "local_port": int(local_port),
            "local_target_host": target_host,
            "bandwidth_kbps": int(bandwidth_kbps),
        }
    ).encode("utf-8")
    try:
        await room.lan.send_bytes(make_frame(T_CONFIG, 0, payload))
        log.info(
            "tunnel %s: config pushed to lan side (%s:%s@%s %skbps)",
            room.tunnel_id, proto, local_port, target_host, bandwidth_kbps,
        )
        return True
    except Exception as exc:
        log.warning("config push failed for %s: %s", room.tunnel_id, exc)
        return False


# ------------------------------------------------------------ Redis 辅助

def lan_present_key(tunnel_id: str) -> str:
    return f"tunnel:lan:{tunnel_id}"


def lan_online(tunnel_id: str) -> bool:
    """内存优先, Redis 兜底(用于多 worker/重启观察)。"""
    r = hub._rooms.get(tunnel_id)
    if r is not None and r.lan is not None:
        return True
    try:
        return get_redis().get(lan_present_key(tunnel_id)) == "1"
    except Exception:
        return False


def set_lan_present(tunnel_id: str, present: bool) -> None:
    try:
        if present:
            get_redis().set(lan_present_key(tunnel_id), "1")
        else:
            get_redis().delete(lan_present_key(tunnel_id))
    except Exception as exc:
        log.warning("redis lan presence failed: %s", exc)


def login_fail_count(username: str, record: bool = False) -> int:
    """60 秒滑动窗口内的失败登录计数。"""
    key = f"auth:fail:{username}"
    try:
        r = get_redis()
        if record:
            n = r.incr(key)
            if n == 1:
                r.expire(key, 60)
            return int(n)
        return int(r.get(key) or 0)
    except Exception:
        return 0
