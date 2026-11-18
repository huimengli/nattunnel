# Unity(NGO / UnityTransport) 接标准 UDP 入口

> 需求记录: `records/2026-11-18-13-03-15.md`。适用: Unity Netcode for GameObjects 的 `UnityTransport`
> 等只能讲标准 UDP(不能写 WebSocket 子协议)的客户端: UDP 游戏服、语音、KCP 类自建 UDP 服务。
> 已知限制清单(哪里会咬人 / mitigations)见 `docs/unity-noties.md`。

## 原理(一句话)

`udp` 隧道可额外配一个 **公网 UDP 端口**(`udp_port`): 服务器在该端口上就是一个**标准 UDP 端点**
(FastAPI 进程直接收发数据报, nginx 不参与)。每个源 `ip:port` = 一个 `peer_id`, 与 WebSocket 子协议裸流
共用同一间房间与同一套帧(`0x41`), 内网侧客户端(exe)零改动。

```text
Unity 客户端            服务器(公网)                内网机器(exe + 游戏服)
UnityTransport  ─标准UDP─> <公网IP>:udp_port  ─帧 0x41─> NAT 隧道 LAN 侧 ─> 127.0.0.1:local_port
```

## 服务器侧配置(网页管理端)

1. 新建/编辑隧道: **协议 = `udp`(游戏 UDP 数据报)**;
2. **内网端口 `local_port`** = 内网机器上游戏服务器(Unity 以 Server 监听)的端口;
3. **UDP 端口(公网) `udp_port`** = Unity 要连的公网端口(1-65535, 全服务器唯一; 冲突 → 409 并提示占用的隧道);
4. **源 IP 白名单 `udp_allow`**: 空 = 放行全部; 支持精确 IP、`1.2.3.` 前缀、`1.2.3.*`、CIDR(`1.2.3.0/24`),
   多条用逗号/空格/换行分隔;
5. 保存后管理页显示 `Server Address:Port`(公网入口)与当前对端数(表列 `UDP 端口(公网)`)。

## Unity 侧设置

- `NetworkManager` / `UnityTransport`: **Server Address = 服务器公网 IP**(官方语义 IPv4; 域名需先解析成 IP),
  **Server Port = `udp_port`**;
- 多人主机: 内网机器上 `NetworkManager.StartServer()`(监听 `local_port`), 每个客户端的 Server Address
  都填 `<公网IP>:udp_port`;
- 数据报建议 ≤1400 字节(MTU 余量); 超过 `UDP_MAX_DATAGRAM`(默认 65536)直接丢弃;
- Unity 的 connected UDP 语义(只收该对端回包)与本项目一致: 回包只发给该对端的源 `ip:port`。

## 内网机器

- exe 客户端保持在线(隧道在线 UDP 入口才生效), 游戏服务器在 `local_port` 监听;
- 内网侧离线时数据报直接丢弃(UDP 无连接语义, 不缓存不排队), 客户端重发/超时即可;
- 空闲 `UDP_IDLE_TIMEOUT`(默认 60s)无数据报的对端自动回收(释放 peer_id)。

## 部署要点

- **单 uvicorn worker**(UDP 房间是进程内存态, 与中继一致);
- **防火墙/安全组放行入站 UDP** `udp_port`; nginx 不反代 UDP, 公网入口必须是 `IP:udp_port` 直连进程;
- `UDP_LISTEN_HOST`(监听地址, 默认 `0.0.0.0`)与 `UDP_PUBLIC_HOST`(管理页显示的公网 IP)在 `server/.env` 配置;
- 带宽限速(`bandwidth_kbps`)由**客户端令牌桶**执行, 服务端不重复限流。

## 安全

- UDP 入口**不鉴权**(与 WS 子协议裸流、纯 HTTP 直转一致): 用 `udp_allow` + 随机高位端口 + 防火墙;
- `udp_peers` 计数可在管理页与 `GET /api/udp/status`(仅登录可见)观察。

## 自测与工具

- `tools/selftest.py` 阶段 G(21 项): 建隧道/绑定/回显/多对端/白名单/超限丢弃/改端口/停用解绑/空闲回收/与 WS 子协议共存;
- `python tools/udp_native_test.py <公网IP>:<udp_port> --send ping --wait 2` — 纯 UDP 往返(完全不经过 WebSocket);
- 20 轮实测: `tests/2026-11-18-13-03-15_cc/`(台架 `tools/unity_udp_test.py`)。

## 踩坑备忘(实测发现, 已修)

- **首包竞态**: 服务端 `T_NEW` 与首包背靠背送达时, 客户端 `run_udp` 的建端 `create_task` 尚未注册 peers[pid]
  → 首包被丢弃(与 2026-11-03 TCP 路径修过的同款竞态)。修法: 客户端在 `U_DATA` 遇到未注册的 peer 时
  先 `await` 建端任务完成再投数据(`client/nattunnel_client.py`)。
- **测试台架**: 自测的回显服务与 `udp_native` 在同一 asyncio 进程里, 阻塞 `recv` 会饿死事件循环使回显永不触发;
  `tools/selftest.py` 的 `udp_native` 已改为非阻塞轮询。
