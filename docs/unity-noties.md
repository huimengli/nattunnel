# Unity 原生 UDP 入口(`udp_port`)的已知限制

> 承接 `records/2026-11-18-13-03-15.md`(实测 20 轮 9/9 全 PASS + 清理复验 3 轮 → `tests/2026-11-18-13-03-15_cc/`)。
> 本次文档需求与定案见 `records/2026-11-18-19-23-43.md`。填法与部署步骤看 `docs/unity-ngo.md`;**本文件只讲哪里会咬人**。

## A. 架构与语义(结构性,改不掉)

1. **内网段是 WebSocket(TCP)隧道 → 队头阻塞(最本质限制)**
   - 数据报路径:`Unity →(真 UDP)→ 服务器 →(WS 帧 0x41, 走 TCP)→ 内网 exe → 游戏服`。
   - 内网段一个包丢失/重传会**拖住后续所有数据报**:Netcode 的低延迟在隧道段失效,表现为延迟尖峰与抖动,由内网段 TCP 拥塞控制主导。
   - Mitigation:内网侧保证带宽与低抖动(`bandwidth_kbps` 设足够值,勿设过小把 TCP 窗口压死);游戏服与 exe 同机(127.0.0.1 一跳);公网段丢包交给 Netcode 自己的 reliable channel。

2. **无握手、无鉴权**
   - 能到达端口的任何源都能建 peer 并把数据注入内网端口(与 WS 子协议裸流、纯 HTTP 直转同样)。
   - Mitigation:`udp_allow` 白名单(精确 IP / `1.2.3.` 前缀 / `1.2.3.*` / CIDR)+ 服务器防火墙 + 随机高位端口;自身无鉴权的内网服务不要暴露。

3. **无 FIN / 无显式断开(靠空闲回收)**
   - `T_FIN`/`T_CLOSE` 对 UDP 无意义;peer 只能靠空闲 `UDP_IDLE_TIMEOUT`(默认 60s)回收(实测阶段 G11)。
   - 影响:Unity 客户端换源端口(重连/端口随机化)后旧 peer 最多占位 60s;**长空闲(菜单/大厅 >60s)peer 回收 → 下一包重新建 peer(新 peer_id),内网游戏服的连接状态随之丢失(表现为掉线重连)**。
   - Mitigation:玩法侧保持周期性发包(Netcode 默认有 tick);大厅长的场景把 `UDP_IDLE_TIMEOUT` 调大(如 300)并同步加大 `UDP_MAX_PEERS`。

4. **内网离线不缓冲**
   - `room.lan is None` 时数据报直接丢弃(不缓存不排队)(`server/app/udp_relay.py:162-163`;实测 G3:丢弃且端点存活)。
   - Mitigation:Unity 侧超时/重连逻辑;管理页 `lan_online` + `udp_peers` 排查。

5. **限速只在客户端令牌桶**
   - `bandwidth_kbps`(双向共用)由内网 exe 的令牌桶执行,服务端不重复限流,**没有逐 peer 速率/PPS 限制**(`UDP_MAX_PEERS` 是数量限制,不是速率限制)。
   - Mitigation:高 PPS 玩法自己控 PPS;公网侧限速靠服务器出口 QoS。

6. **MTU 与分片**
   - 服务端接受到 `UDP_MAX_DATAGRAM`(默认 65536),但 >1472 会 IP 分片,NAT/移动网络丢分片概率高;实测 1536B 完整往返(G6)是回环环境,不代表公网。
   - Mitigation:Unity/Netcode 保持 ≤1400;不要在一条消息里塞超大 payload。

7. **IPv4 only**
   - `UDP_LISTEN_HOST` 是 IPv4(默认 `0.0.0.0`);Unity `Server Address` 官方语义 IPv4 → 填公网 IP(域名需先解析),换 IP 要改 Unity 配置;IPv6 不支持。

## B. 资源与攻击面

8. **`UDP_MAX_PEERS=64` 是源地址资源,且内网离线时 peer 仍累积**
   - 填满它不需要带宽,只要源地址够多 → 合法玩家被拒(`server/app/udp_relay.py:126-129`)。
   - `UdpEndpoint._add_peer` 先 `alloc_peer` 再入队,`room.lan` 离线时数据被丢但 peer 留在表里(`udp_relay.py:139-156`)→ **内网离线期被刷满后,内网恢复时新玩家进不来,要等 60s 回收**。
   - Mitigation:`udp_allow` 收紧;`UDP_MAX_PEERS` 按玩法设;监控 `GET /api/udp/status` 的 `peers`。

9. **可被用作 UDP 反射放大源**
   - 回包只回源地址(connected UDP 语义),若内网服务对小包回大包(欢迎/回执/错误页),该端点即成为反射源。
   - Mitigation:白名单;内网服务不要对未知小包产生大响应;必要时服务器出口限速。

10. **扫描面:端口号就是唯一口令**
    - UDP 面没有 token/短链(8 位短链只在 WS/HTTP 面),`udp_allow` 为空时任何源可试。
    - Mitigation:随机高位端口 + 白名单 + 防火墙。

11. **端点数无全局上限;绑定失败仍显示入口(易误判)**
    - 每隧道一个 UDP 端口;`create_datagram_endpoint` 绑定失败(端口被外部进程占用)只 `log.warning` 并返回 False(`udp_relay.py:245-266`),`POST/PATCH` **保存仍成功且 `udp_public` 仍显示** → 管理页看起来有入口,只有 `GET /api/udp/status` 里没有它。
    - Mitigation:保存后核对 `/api/udp/status` 端点表,或服务器日志 `UDP 入口已绑定: 0.0.0.0:<port>`;隧道之间的端口冲突 API 已回 409(外部占用需人工)。

12. **无历史指标/告警**
    - `udp_peers` 是即时计数(表列与 `/api/udp/status`);peer 建立/回收只在日志(关键字 `新对端` / `对端数已达上限`),无曲线无告警。

## C. 运维与部署

13. **单 uvicorn worker 硬要求**:UDP 房间是进程内存态(与中继一致);多 worker → 端点/peer 不共享,行为不可预测。
14. **nginx 反代不了 UDP**(多项目混部同样):UDP 必须直连 uvicorn 进程;UDP 端口是**全服务器唯一命名空间**(隧道间冲突 409 已保证),与 80/443 不冲突;安全组/防火墙须放行**入站 UDP**(与 TCP 443 无关)。
15. **跨机转发必须保源地址**:socat 的 NAT 转发会把多玩家捏成同一 peer(源被改成转发机)→ 用 iptables DNAT / 主机网络保源。
16. **改 `udp_port` 需 Unity 侧同步**:无重定向,`Server Port` 是手工配置(每台客户端都要改)。
17. **重启期入口消失**:服务重启期间 UDP 入口不存在(启动时按库自动绑定,实测 G10);Unity 表现为超时/断连,需重连。
18. **明文**:UDP 无 TLS(与子协议裸流一致);敏感业务靠白名单 + 防火墙,不要指望加密。
19. **无固定公网 IP(家用宽带)不可行**:UDP 必须入站可达;NAT 后控制不了映射则无解。

## D. 测试覆盖缺口(诚实声明)

- 台架是 **127.0.0.1 回环**:未验真实 NAT/防火墙/MTU 分片/高 PPS/丢包乱序/多人同 NAT 的源端口变化/**Unity 真机(UnityTransport 未实机跑过)**。
- 未验:端口被外部进程占用的绑定失败路径(第 11 项)、公网压力下 `UDP_MAX_PEERS` 的实际表现、跨机转发、Windows Proactor 高 PPS 性能。
- **现网 exe 未重编译**:首包竞态修复在客户端源码(见 E2),真机首包仍可能丢 → 上线前必须 `client/build.bat -s www.keliit.top/tunnel/` 重编译并用 `tools/exe_e2e_test.py` 验收。

## E. 本会话已修(可复核)

1. `main.py` 的 `from .relay import (...)` 整块被 import 重排删掉 → `RELAY_SUBPROTOCOL` 未定义,WS 建隧全崩(自测 A–E/G 的 lan 上线全 FAIL)。已恢复(教训:改 import 逐行追加,不要整块替换)。
2. **客户端 `run_udp` 首包竞态**:`T_NEW` 建端是 `create_task`,首包 `U_DATA` 早于其完成 → `peers.get(pid)` 为 None 丢首包;服务端加睡眠无效(帧已在客户端缓冲)。已按 2026-11-03 TCP 路径同法修(`peer_tasks` 先 `await` 建端任务再投数据)。
3. 自测 `udp_native` 阻塞 `recv` 饿死同进程回显(阶段 G5–G8 必失败)→ 改非阻塞轮询;顺带用独立探针证明 `DatagramTransport.sendto` 跨进程投递正常(不是服务端发送原语问题)。
4. `[dbg]` 临时诊断日志(客户端 2 处 + 服务端 1 处)随 feat commit 进仓库,已删除并复验 3 轮 9/9。

## 快速自检(上线 30 秒)

```bash
python tools/udp_native_test.py <公网IP>:<udp_port> --send ping --wait 2
python tools/udp_native_test.py <公网IP>:<udp_port> --burst 3 --send hello --wait 2
```

- 收到回包 = UDP 入口通(Unity 一定能连);收不到 = 链路问题(入站 UDP 未放行 / 公网不可达),与代码无关。
- Unity 连入后管理页该隧道的 `UDP 端口(公网)` 列应出现 `· 对端N`,`GET /api/udp/status` 的 `peers` 同步 +1。

