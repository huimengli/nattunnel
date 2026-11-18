"""UDP 原生入口(标准 UDP 端口映射) —— 给 Unity Transport 等标准 UDP 客户端一个 `IP:端口` 直连入口。

**为什么需要它**: 隧道公网侧的三种入口(纯 HTTP 直转 / WS 握手桥接 / WS 子协议 `nattunnel-relay` 裸流)
都跑在 TCP 上的 WebSocket 里, 没有可供 Unity Netcode for GameObjects(UnityTransport = 纯 UDP)连接的
UDP 端点; UnityTransport 也发不出自定义 WS 子协议/头(WebGL 才有 WebSocket)。本模块让**有公网 IP 的服务器**
把隧道直接暴露成标准 UDP 端口: 公网客户端只需向 `<公网IP>:<udp_port>` 收发数据报, 无需 WS/子协议/自定义头,
Unity 侧**零代码改动**(UnityTransport 的 Server Address/Server Port 直接填它)。

模型(与 WS 子协议裸流共用房间与帧, LAN 客户端零改动):
  - `proto=udp` 隧道的 `udp_port` 在 `UDP_LISTEN_HOST` 上 bind 一个 UDP 套接字;
  - **每个源地址(对端 IP:端口)= 一个 peer_id**, 数据报走 U_DATA(0x41);
    LAN 侧为该 peer 起本机临时 socket, 转发到 `local_target_host:local_port`(客户端 `run_udp` 已实现);
  - 回包: LAN -> U_DATA(pid) -> `sendto`(该 peer 的源地址) —— UDP 无连接, 用源地址回程;
  - 对端空闲 `UDP_IDLE_TIMEOUT` 秒无数据报即回收(并通知 LAN 拆本地 socket); 内网侧离线时到达的数据报丢弃;
  - `udp_allow`(逗号分隔 IP/CIDR/前缀, 空=放行)是唯一的访问控制: **UDP 入口不鉴权**。
"""
import asyncio
import ipaddress
import logging
import time

from .config import UDP_IDLE_TIMEOUT, UDP_LISTEN_HOST, UDP_MAX_DATAGRAM, UDP_MAX_PEERS
from .database import SessionLocal
from .models import Tunnel
from .relay import T_CLOSE, T_NEW, U_DATA, alloc_peer, hub, make_frame, split_frame

log = logging.getLogger("nattunnel.udp")


def allow_ip(allow: str, ip: str) -> bool:
    """`udp_allow` 判定: 空 = 放行全部; 每项为 CIDR / 精确 IP / 前缀(如 '1.2.3.' 或 '1.2.3.*')。"""
    allow = (allow or "").strip()
    if not allow:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for raw in allow.split(","):
        item = raw.strip()
        if not item:
            continue
        if "/" in item:
            try:
                if addr in ipaddress.ip_network(item, strict=False):
                    return True
            except ValueError:
                continue
        elif item.endswith(".") or item.endswith("*"):
            if str(addr).startswith(item.rstrip(".*")):
                return True
        elif str(addr) == item:
            return True
    return False


def public_display(udp_port, public_host: str = "") -> str | None:
    """管理页展示的公网 UDP 入口(Unity 的 Server Address:Server Port)。"""
    port = int(udp_port or 0)
    if port <= 0:
        return None
    host = (public_host or "").strip()
    if not host:
        host = UDP_LISTEN_HOST
        if host in ("0.0.0.0", "::", ""):
            host = "<服务器公网IP>"
    return f"{host}:{port}"


class UdpPeer:
    """公网侧 UDP 对端(源地址); 在房间里作为伪 pub 入口(与 WS 裸流 / 桥接 `_QueuePub` 同一接口)。"""

    __slots__ = ("pid", "addr", "endpoint", "last_seen")

    def __init__(self, pid, addr, endpoint):
        self.pid = pid
        self.addr = addr
        self.endpoint = endpoint
        self.last_seen = time.monotonic()

    async def send_bytes(self, data: bytes) -> None:
        # main.py 的 LAN->pub 路径投递整帧 [type][pid][payload]
        try:
            type_, fpid, payload = split_frame(data)
        except ValueError:
            return
        if fpid != self.pid:
            return
        if type_ == U_DATA and payload:
            log.info("[dbg] 回包 peer %d %dB -> %s", self.pid, len(payload), self.addr)  # 临时诊断
            self.endpoint.send_to(self.addr, payload)
        # T_FIN/T_CLOSE 对 UDP 无意义: 忽略

    async def close(self, *args, **kwargs) -> None:
        self.endpoint.remove_peer(self.pid, notify_lan=False)


class UdpEndpoint:
    """一条隧道的公网 UDP 监听端点(asyncio DatagramProtocol)。"""

    def __init__(self, tunnel_id, port):
        self.tunnel_id = tunnel_id
        self.port = port
        self.peers = {}            # (ip, port) -> UdpPeer
        self.transport = None
        self.info = None           # {proto, local_port, local_target_host, udp_allow, enabled}
        self._queue = asyncio.Queue()
        self._pump_task = None   # 注意: 不能叫 _pump(与协程方法同名)

    # ---------------- protocol 回调 ----------------
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        info = self.info
        if info is None or not info.get("enabled"):
            return
        if not data or len(data) > UDP_MAX_DATAGRAM:
            return
        ip = addr[0] if isinstance(addr, tuple) else str(addr)
        if not allow_ip(info.get("udp_allow", ""), ip):
            log.debug("udp %s: 源 %s 不在 udp_allow 内, 丢弃 %dB", self.tunnel_id, addr, len(data))
            return
        peer = self.peers.get(addr)
        if peer is None:
            if len(self.peers) >= UDP_MAX_PEERS:
                log.warning("udp %s: 对端数已达上限 %d, 丢弃来自 %s 的数据报",
                            self.tunnel_id, UDP_MAX_PEERS, addr)
                return
            asyncio.create_task(self._add_peer(addr, data))
            return
        peer.last_seen = time.monotonic()
        self._queue.put_nowait((peer.pid, data))

    def connection_lost(self, exc):
        self.peers.clear()

    # ---------------- 内部 ----------------
    async def _add_peer(self, addr, first: bytes) -> None:
        info = self.info
        if info is None or not info.get("enabled"):
            return
        room = hub.room(self.tunnel_id, "udp")
        pid = alloc_peer(room)
        peer = UdpPeer(pid, addr, self)
        self.peers[addr] = peer
        room.pubs[pid] = peer
        log.info("udp %s: 新对端 %s -> peer_id=%d (内网目标 %s:%s)",
                 self.tunnel_id, addr, pid, info["local_target_host"], info["local_port"])
        lan = room.lan
        if lan is not None:
            try:
                await lan.send_bytes(make_frame(T_NEW, pid))
            except Exception as exc:
                log.warning("udp %s: 通知 LAN 建对端失败(peer %d): %s", self.tunnel_id, pid, exc)
        self._queue.put_nowait((pid, first))

    async def _pump(self) -> None:
        while True:
            pid, data = await self._queue.get()
            room = hub.get(self.tunnel_id)
            if room is None or room.lan is None:
                continue  # 内网侧离线: UDP 无连接语义, 直接丢(客户端会重发/超时)
            try:
                await room.lan.send_bytes(make_frame(U_DATA, pid, data))
            except Exception as exc:
                log.debug("udp %s: 入隧失败(peer %d): %s", self.tunnel_id, pid, exc)

    def send_to(self, addr, payload: bytes) -> None:
        if self.transport is None:
            return
        try:
            self.transport.sendto(payload, addr)   # sendto 是同步方法(见踩坑备忘 3)
        except Exception as exc:
            log.debug("udp %s: 回包失败(%s): %s", self.tunnel_id, addr, exc)

    def remove_peer(self, pid, notify_lan=True) -> None:
        for addr, peer in list(self.peers.items()):
            if peer.pid == pid:
                self.peers.pop(addr, None)
                break
        room = hub.get(self.tunnel_id)
        if room is not None:
            room.pubs.pop(pid, None)
            if room.lan is not None and notify_lan:
                try:
                    asyncio.ensure_future(room.lan.send_bytes(make_frame(T_CLOSE, pid)))
                except Exception:
                    pass
            if room.lan is None and not room.pubs:
                hub.drop(self.tunnel_id)

    def start_pump(self):
        """入隧泵: 队列里的数据报依次送进隧道(内网侧离线则丢弃)。"""
        self._pump_task = asyncio.create_task(self._pump())

    def stop_pump(self):
        if self._pump_task is not None:
            self._pump_task.cancel()
            self._pump_task = None


class UdpHub:
    """公网 UDP 端点表(与中继同进程, 单 worker 前提)。"""

    def __init__(self):
        self.endpoints = {}     # port -> UdpEndpoint
        self.by_tunnel = {}     # tunnel_id -> port
        self._gc = None

    def snapshot(self, tunnel) -> dict:
        return {
            "proto": tunnel.proto,
            "local_port": int(tunnel.local_port),
            "local_target_host": (tunnel.local_target_host or "127.0.0.1").strip(),
            "udp_allow": (tunnel.udp_allow or ""),
            "enabled": bool(tunnel.enabled),
        }

    def port_of(self, tid) -> int | None:
        return self.by_tunnel.get(tid)

    def peer_count(self, tid) -> int:
        port = self.by_tunnel.get(tid)
        ep = self.endpoints.get(port) if port is not None else None
        return len(ep.peers) if ep else 0

    def endpoint_list(self) -> list:
        out = []
        for port, ep in self.endpoints.items():
            out.append({
                "tunnel_id": ep.tunnel_id,
                "port": port,
                "peers": len(ep.peers),
                "target": f"{ep.info['local_target_host']}:{ep.info['local_port']}",
                "allow": ep.info["udp_allow"] or "放行全部",
                "public": public_display(port),
            })
        return out

    async def bind(self, tunnel) -> bool:
        """用当前 ORM 行绑定(隧道配置变更时)。"""
        return await self._bind(tunnel.tunnel_id, int(tunnel.udp_port or 0), self.snapshot(tunnel))

    async def _bind(self, tid: str, port: int, snap: dict) -> bool:
        """实际绑定; 快照是**普通 dict**(会话关闭后 ORM 属性不可读, 见踩坑备忘)。"""
        if port <= 0:
            return False
        ep = UdpEndpoint(tid, port)
        ep.info = snap
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: ep, local_addr=(UDP_LISTEN_HOST, port)
            )
        except OSError as exc:
            log.warning("UDP 端口 %d 绑定失败(隧道 %s): %s", port, tid, exc)
            return False
        ep.transport = transport
        ep.start_pump()
        self.endpoints[port] = ep
        self.by_tunnel[tid] = port
        log.info("UDP 入口已绑定: %s:%d (隧道 %s) -> 内网 %s:%s, 白名单=%s",
                 UDP_LISTEN_HOST, port, tid,
                 snap["local_target_host"], snap["local_port"], snap["udp_allow"] or "放行全部")
        return True

    async def unbind(self, tid, reason: str = "") -> bool:
        port = self.by_tunnel.pop(tid, None)
        if port is None:
            return False
        ep = self.endpoints.pop(port, None)
        if ep is None:
            return True
        room = hub.get(tid)
        for peer in list(ep.peers.values()):
            ep.peers.pop(peer.addr, None)
            if room is not None and room.lan is not None:
                try:
                    await room.lan.send_bytes(make_frame(T_CLOSE, peer.pid))
                except Exception:
                    pass
            if room is not None:
                room.pubs.pop(peer.pid, None)
        ep.stop_pump()
        if ep.transport is not None:
            try:
                ep.transport.close()
            except Exception:
                pass
        if room is not None and room.lan is None and not room.pubs:
            hub.drop(tid)
        log.info("UDP 入口已解绑: 端口 %d (隧道 %s, %s)", port, tid, reason)
        return True

    async def sync(self, tunnel) -> bool:
        """按隧道当前配置绑定/解绑; 端口不变时只热刷新快照(白名单/启用/目标)。"""
        tid = tunnel.tunnel_id
        port = int(tunnel.udp_port or 0)
        want = tunnel.proto == "udp" and bool(tunnel.enabled) and port > 0
        current = self.by_tunnel.get(tid)
        if not want:
            if current is not None:
                await self.unbind(tid, "配置变更(停用/协议/端口清空)")
            return False
        if current == port:
            ep = self.endpoints.get(current)
            if ep is not None:
                ep.info = self.snapshot(tunnel)
            return True
        if current is not None:
            await self.unbind(tid, "公网端口变更")
        return await self.bind(tunnel)

    async def start(self):
        """服务启动: 绑定库里全部已配置的 UDP 入口 + 启动空闲回收。

        注意: 快照必须在会话内取成 dict —— 会话关闭后 ORM 实例已 expire,
        再读属性会 DetachedInstanceError(启动即 500)。
        """
        rows = []
        with SessionLocal() as db:
            for t in db.query(Tunnel).filter(Tunnel.proto == "udp").all():
                if t.enabled and int(t.udp_port or 0) > 0:
                    rows.append((t.tunnel_id, int(t.udp_port), self.snapshot(t)))
        bound = 0
        for tid, port, snap in rows:
            if await self._bind(tid, port, snap):
                bound += 1
        self._gc = asyncio.create_task(self._gc_loop())
        log.info("UDP hub 就绪: %d 个入口, 监听 %s, 空闲回收 %ds, 每隧道对端上限 %d",
                 bound, UDP_LISTEN_HOST, UDP_IDLE_TIMEOUT, UDP_MAX_PEERS)

    async def stop(self):
        if self._gc is not None:
            self._gc.cancel()
            self._gc = None
        for ep in list(self.endpoints.values()):
            await self.unbind(ep.tunnel_id, "服务停止")

    async def _gc_loop(self):
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            for ep in list(self.endpoints.values()):
                for addr, peer in list(ep.peers.items()):
                    if now - peer.last_seen > UDP_IDLE_TIMEOUT:
                        log.info("UDP %s: 对端 %s 空闲超过 %ds -> 回收 peer %d",
                                 ep.tunnel_id, addr, UDP_IDLE_TIMEOUT, peer.pid)
                        ep.remove_peer(peer.pid)


udp_hub = UdpHub()
