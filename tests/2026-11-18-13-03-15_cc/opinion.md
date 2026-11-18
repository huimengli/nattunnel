# 2026-11-18-13-03-15 批次结论(Unity 标准 UDP 入口, 20 轮)

- 台架: `tools/unity_udp_test.py`(每轮: 删库 → 全新 uvicorn 进程 → selftest 全阶段 → api_check → 台架自有场景 → 重启后启动绑定 → 删除解绑 → 管理页静态检查)。
- 环境: sqlite(`UDP_LISTEN_HOST=127.0.0.1`, `UDP_MAX_DATAGRAM=1600`, `UDP_IDLE_TIMEOUT=6`, `UDP_MAX_PEERS=8`), 单 worker, 内网回显与真实客户端在测试进程内。
- 结果: **20/20 轮全 PASS(每轮 9 项)**; 每轮 selftest **53 项全 PASS**(阶段 A/B/C/D/E/G) + api_check **26 项全 PASS**; 明细 `record_1..20.md`。

## 关键结论

1. `udp` 隧道配 `udp_port` 后, 服务器在该端口上就是**标准 UDP 端点**: 纯 UDP 数据报(完全不经过 WebSocket)可与内网 echo 完整往返(单包/1536B 二进制/多包), 内网 exe 零改动。
2. 每个源 `ip:port` = 一个独立 peer_id(两源地址并存 peers=2, 互不干扰); 与 WS 子协议裸流通道可共存(同一隧道、同一房间、同一 `0x41` 帧)。
3. 控制项有效: `udp_allow`(精确 IP 放行/拒绝、`127.0.0.0/8` CIDR 放行、空=放行全部)、`UDP_MAX_DATAGRAM` 超限直接丢弃且端点存活、`UDP_IDLE_TIMEOUT` 空闲回收(peers 归 0)、`UDP_MAX_PEERS` 上限。
4. 生命周期: 建隧道即绑定; 改 `udp_port` → 新端口生效且旧端口静音; 停用/删除 → 解绑(端点表清空); 服务器重启后启动时自动绑定库里的 `udp_port`(不依赖网页操作)。
5. API 侧: 公网 UDP 端口全服务器唯一(冲突 409 并提示占用者); 非 `udp` 隧道自动清空 `udp_port`/`udp_allow`; `udp_public` 展示串与 `/api/udp/status`(仅登录)一致。
6. LAN 离线时对入口发数据报: 直接丢弃(UDP 无连接语义), 端点与隧道不受影响。

## 期间发现并修复的真问题(见 records/2026-11-18-13-03-15.md)

- `main.py` 的 `from .relay import (...)` 整块被本次 import 重排删除 → WS 建隧 `name 'RELAY_SUBPROTOCOL' is not defined`, 自测 A/B/C/D/E/G 全崩; 已恢复。
- **客户端首包竞态**(真 bug, 影响所有 UDP 隧道首包): `T_NEW` 与首包背靠背送达时 `run_udp` 丢弃首包; 与 2026-11-03 TCP 路径同款竞态, 已用同法在 `client/nattunnel_client.py` 修复(等建端任务完成再投数据)。
- 自测 `udp_native` 的阻塞 `recv` 饿死同进程的回显服务(台架 bug), 改为非阻塞轮询。
- 台架 Windows 坑: `stdin=DEVNULL` 使 `isatty()` 为真触发首启向导; GBK 控制台编码; 进程退出后 sqlite 文件释放延迟(重试点)。

## 遗留(未在本批覆盖)

- 真实公网 + 防火墙入站 UDP 放行(台架是 127.0.0.1 回环, 不经 nginx/防火墙)。Unity 真机(UnityTransport)未实测, 需按 `docs/unity-ngo.md` 上线后验一次。
- **exe 需重编译**: 客户端源码改了首包竞态, 现网 exe 仍是旧行为(`client/build.bat -s <服务器地址>`, 用 `tools/exe_e2e_test.py` 验收)。
- UDP 丢包/乱序/重传(Netcode 自身语义)、跨 NAT 源端口变化、`bandwidth_kbps` 对 UDP 的客户端限速未在本批专项覆盖。
