# nattunnel — 跨会话进度台账

<!-- 已归档于 ai_buckup/progress.md_2026-11-03-19-18-00.md (107 行全量; 更早全量见 ai_buckup/progress.md_2026-11-03-18-58-00.md) -->
<!-- 2026-11-03 19:18 归档动作: 移除 2026-09-12 两条会话摘要(全量在备份), 状态总览合并; 本次会话条目与踩坑/风险/Git 记录保留 -->
<!-- 已归档于 ai_buckup/progress.md_2026-11-03-18-58-00.md (203 行全量; 历史会话条目/踩坑/部署排障细节均在备份) -->
## 项目目标
无公网 IP 电脑 ⇄ 有公网 IP 服务器(nginx 反代 www.xxx.com):
客户端 exe 将本机端口转发到 `www.xxx.com/tunnel/<8位短链>`。
技术栈: Python / FastAPI / MySQL / Redis / JWT / RSA 握手 / WebSocket 中继;
协议 **http | tcp | udp**(2026-11-03 改), 前端端口与带宽配置; 用户系统(管理员首启初始化, 代码无硬编码)。
## 关键约定
- 8 位短链: 字母表去混淆字符, 例 `XgMacp2G`; 自动生成或手工指定。
- 角色判定: `/tunnel/{id}` WS 携带有效 Bearer JWT 且属主/admin → LAN 侧; 否则公网侧(每连接一条流)。
- 帧协议: `[type 1B][peer_id 4B BE][payload]`; 0x01 NEW / 0x02 DATA / 0x03 FIN / 0x04 CLOSE / 0x05 HELLO / 0x21 CONFIG(热更新) / 0x41 UDP-DATA。
- 带宽由客户端令牌桶执行(双向共用, kbps, 0=不限)。
- 中继状态在进程内存(Redis 记在线状态与登录失败计数) → **必须单 worker**。
- **协议语义(2026-11-03)**: `http` = 混合(浏览器直开逐请求直转 + WS 握手桥接 + 子协议裸流);
  `tcp` = 原生字节流(WS 握手桥接 + 子协议裸流, 纯 HTTP → 400); `udp` = 仅子协议数据报(WS 握手 → 400)。
- 公网侧 WS 归属: Bearer→LAN; 子协议 `nattunnel-relay`→帧协议裸流中继; 其余(浏览器式)→ http_bridge 桥接到内网 WS 服务。
- 存量 `proto=tcp`(旧浏览器直开语义)首启一次性迁移为 `http`(AppSetting `proto_tcp_to_http_v1` 标记)。
## 凭据 / 环境
| 项 | 值 |
|---|---|
| 管理员 | 首启创建: 交互终端 `python run.py` 控制台引导(隐藏+二次确认); 非交互回退 `.env INITIAL_ADMIN_PASSWORD` 或随机 16 位+日志一次性打印; 登录后 POST /api/password 改密; 无硬编码凭据 |
| 示例隧道 | XgMacp2G (tcp=字节流, 3389, 不限速) |
| 后端默认地址 | http://127.0.0.1:8000 (server/ 目录 `python run.py`) |
| MySQL/Redis | server/docker-compose.yml 一键起(无 Redis 时在线状态/限流降级内存) |
| 本机自测 | `.venv` + `DATABASE_URL=sqlite:///./selftest.db` + `INITIAL_ADMIN_PASSWORD` 环境变量; `.env` 缺失会触发首启交互向导 |
## 状态总览
- [x] 骨架 + 台账(progress.md / tree.md / records/ / todo.md / need_user.md / tests/)
- [x] 后端: 用户/JWT/RSA 登录/隧道 CRUD/WS 中继/种子数据/绑定令牌
- [x] 客户端: 无本地配置 — 服务器地址 build 时写死, 隧道 ID 由绑定令牌决定, 端口/协议/带宽/目标主机运行时读取; T_CONFIG 热更新; PyInstaller --onefile + favicon 图标
- [x] 网页管理端: 单文件(登录/隧道 CRUD+在线状态/用户/改密/绑定令牌签发/exe 下载按钮); 协议下拉三项 + 语义说明
- [x] **纯 HTTP 直转(http 隧道)**: 浏览器直开短链, 逐请求直转发 + HTML `<base>` 注入 + Content-Length 同步; nginx map 头兼容
- [x] **WebSocket 握手桥接(http/tcp 隧道)**(2026-11-03): 握手 101 + 数据帧/文本帧双向透明, `Sec-WebSocket-Key` 原样转发,
  子协议协商结果回带, 剥 `Sec-WebSocket-Extensions`; 拒绝码 404(隧道缺失)/502(LAN 离线)/504(无握手响应)/内网非 101 原码透传
- [x] 混部子路径部署(nginx embed + 管理页相对路径 + API_BASES 回退) + docs/deploy-keliit-top.md
- [x] 端到端自测: selftest 阶段 A(字节流+热更新)/B(UDP)/C(绑定令牌)/D(纯 HTTP 直转+tcp 拒绝)/E(WS 握手桥接) 32 项 + api_check 18 项, 20 轮全 PASS
- [x] exe 双下载通道(登录态 API + 静态直链, 单副本在 server/app/static/)
- [x] **隧道配置项 `header_timeout_s`**(2026-11-04): 响应头等待窗口, 默认 30 秒 / 上限 600 秒; 服务端逐请求读库(管理页改后下一个请求生效, 不走 T_CONFIG); WS 握手窗口固定 20 秒; 3 轮验证全 PASS
- [ ] 部署到 www.keliit.top: 子路径 `/tunnel/` + `/nattunnel-admin/`; 部署后改管理员密码(步骤见 docs/deploy-keliit-top.md)
- [ ] `tcp+udp` 同端口双栈混合(用户 2026-11-03 选方案A, 本项列为后续需求)
## 会话摘要(最近)
### 2026-11-03 13:21 — records/2026-11-03-13-21-03.md (协议改造 + WS 握手桥接)
- 需求原文: "tcp 隧道实测只能转发 HTTP 但 WebSocket 握手到不了; 隧道选项 tcp,udp → http,tcp,udp; 原有 tcp 改为 http, tcp 需支持 WebSocket 握手"。
- `relay.py`: `PROTOS=(http,tcp,udp)` + `RELAY_SUBPROTOCOL="nattunnel-relay"` + `data_type(proto)` + `alloc_peer(room)`(HTTP 桥接与 WS 桥接共用房间分配); 恢复被误删的 `get_redis()`。
- `http_bridge.py`(重写): 新增 `WSCodec`(最小 RFC6455 客户端编解码: 掩码帧发/不掩码帧收, 分片/控制帧, RSV 非 0 与掩码服务端帧拒绝),
  `_build_ws_handshake`(剥 host/connection/upgrade/proxy-connection/sec-websocket-extensions, 补 Host/Connection/Upgrade/Version),
  `_parse_ws_response`, `_ws_bridge`(握手透传→101 用同一 key 完成对浏览器 accept 并回带子协议→双向字节管道),
  `_ws_reject`(ASGI `websocket.http.response` 预拒绝), 纯 HTTP 直转限定 `proto == "http"`(否则 400)。
- `main.py`: 接受时回填 `nattunnel-relay` 子协议; `data_types = {data_type(tunnel.proto)}`; 角色/日志带 proto。
- `schemas.py`/`models.py`: proto 取值 `^(http|tcp|udp)$`, 默认 `http`; `seed.py`: 一次性迁移 tcp→http(AppSetting 标记) + 演示隧道注释。
- `client/nattunnel_client.py`: `proto in ("tcp","http")` 均走字节流管道; 仅切到 `udp` 需重连。
- 管理页 `index.html`: 协议下拉 http/tcp/udp + 弹窗协议提示(protoHint) + 隧道卡片协议语义说明(混合表述)。
- 工具: `ws_tcp_test.py`/`ws_udp_test.py` 声明子协议; 新增 `tools/ws_handshake_test.py`(浏览器式 WS 握手/echo/子协议打印);
  selftest 新增阶段 E(E1-E10)与 D8; api_check 新增协议取值(422)/PATCH 改协议。
- 方案A(用户拍板): 不新增 mixed 协议, `http` 即混合; `tcp+udp` 双栈混合列为后续需求(need_user.md Q1 已答, 已归档)。
- 实测: 20 轮全 PASS(selftest 32 项 + api_check 18 项) → tests/2026-11-03-13-21-03_cc/(record_1..20 + opinion.md), 累积 tests/opinion.md。

### 2026-11-04 19:06 — records/2026-11-04-19-06-38.md (断线诊断 + 隧道配置项 header_timeout_s)
- 现象: 带 46 张截图的 `POST /v1/chat/completions` 连续 5 次 504 `"本地服务响应超时"`(14:34 / 18:46 同型); 14:34:38 客户端 `sent 1011 keepalive ping timeout`。
- 只读诊断(server.log + nginx 访问日志, 经 ssh 公钥只读取证): 504 起算点在**请求体入隧后 20 秒**(`_HEADER_TIMEOUT`), 46 图 prefill 远超 20 秒 → 必然 504; 超时后 finally 发 T_CLOSE 掐本地生成 → 重试 = 重传 46 图 + 再被掐;
  1011 是这条链下游(20 秒无响应 → 无 pong), **不是真断线**(nginx WS 会话 17.6MB/67s ≈ 2Mbps, 关闭时间戳逐一对应); 拥塞排除(websockets 17 的 32KB 反压 → 在途 ≈96KB, 2Mbps 下 ping 排队 ~0.12 秒)。
- 实现: `header_timeout_s`(默认 30, 1..600) 走与 `bandwidth_kbps` 同一管线: models 列 + seed 幂等 ALTER + schemas + routers create/_to_out + http_bridge 逐请求取值 + 管理页"响应超时(s)"列与输入; 服务端参数**不推 T_CONFIG**。
- `http_bridge` 常量拆分: `_WS_HANDSHAKE_TIMEOUT = 20.0`(WS 握手等 101, 不随配置放大) / `_DEFAULT_HEADER_TIMEOUT = 30.0`(兜底)。
- 新增 `tools/header_timeout_test.py`(阶段 F 9 项: 默认 30/显式 45/600 接受/601→422/45s 窗口+40s 慢服务→200/10s→504 文案/改回 45 立即生效/回显); 验证 3 轮全 PASS(header 9 + selftest 32) → tests/2026-11-04-19-06-38_cc/。
- 待办: 生产重启自动补列(现隧道默认 30, 行为不变); 建议 llm 隧道设 300; 需核实 nginx `proxy_read_timeout` ≥ 配置值。
## 踩坑备忘(后续会话必读)
1. **PyInstaller --onefile 是双进程**: 杀 exe 用 `taskkill /F /IM nattunnel-client.exe`(父进程 kill 会留孤儿子进程)。
2. **TCP 建连竞态**: 公网侧数据帧可能早于 LAN 客户端建连完成 → 主循环先 `await open_tasks[pid]` 再入队(已在 run_tcp 实现)。
3. `asyncio.DatagramTransport.sendto()` 是**同步方法**, 不能 await。
4. sqlite 在并发 WS 风暴下会锁竞争卡死(可自恢复); 生产 MySQL 无此问题; 中继仍是单 worker 内存态(扩容前需 Redis Pub/Sub 化)。
5. RSA 私钥对象无 `public_bytes`, 用 `key.public_key().public_bytes(...)`。
6. **存量库加列**: 无迁移框架 → `seed.py` init_db 里幂等 `ALTER TABLE`(捕获 duplicate/1060); 新库由 create_all。schema 演进走这个模式(本次 proto 迁移用 AppSetting 标记)。
7. **.bat 必须纯 ASCII**: 中文 Windows cmd 按 GBK 解析 .bat, UTF-8 中文注释会乱码甚至吃掉后续语句。
8. **本机解释器无 `secrets.randbytes`**(3.12 定制安装): 需要随机字节用 `secrets.token_bytes(n)`。
9. **WS 桥接路径语义**: 裸流(子协议 nattunnel-relay)必须连裸路径 `/tunnel/<ID>`; 带子路径(如 `/tunnel/<ID>/ws`)不匹配 FastAPI 路由,
   uvicorn 以 403 拒绝握手。用 `websocket.http.response` 预拒绝时 uvicorn 会打
   `ASGI callable returned without completing handshake` — 属正常表征(客户端得到 HTTP 响应)。
10. **PyInstaller --onefile 需要可写的系统 TEMP**: 受限环境(本沙箱)报 `Cannot create temporary directory!`;
    给子进程设 TEMP/TMP 到仓库内目录即可(`tools/exe_e2e_test.py` 已内置)。
11. **pip 直连 pypi 本机仅 17 kB/s 且 resolver 会回退到 pyinstaller 4.x**: 用 Tsinghua 镜像 + 固定版本
    (`pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller==6.1.0 ...`, 91s 完成);
    `run_code` 的 `process.env` 是空的(无 TEMP/USERPROFILE/SystemRoot), spawn 子进程时需自行补齐。
12. **spawn 子进程随 run_code 进程退出即被杀**: 服务器必须与测试在同一程序内启动/结束; spawn 时 stdin 用 pipe(非 tty)以走非交互首启路径,
    `server/.env` 缺失会触发首启交互向导(stdin 为 tty 时); Windows `stdio:"ignore"` 的 stdin 仍是 tty, 必须显式用 pipe。
13. **504 的语义边界**: `504 = 响应头窗口超时`(起算点在请求体全部入隧之后), 不代表隧道断开; `502 = 转发失败/链路中断`; `503 = room.lan is None`。
    窗口超时后 bridge 的 finally 发 T_CLOSE 掐本地请求, 内网服务随后写响应会 ConnectionAborted(WinError 10053) — 属预期。
## 风险 / 注意事项
1. 公网侧入口不鉴权(含 WS 桥接与子协议裸流) → 必须上 TLS; 短链可猜 → 敏感业务轮换 ID / 防火墙限 IP。
2. 多 worker 会破坏内存中继; 扩缩容需先做 Redis Pub/Sub 化(未来工作)。
3. 字节流为"每公网连接一条", 不支持半关等细粒度语义(对 RDP/SSH/HTTP/WS 足够)。
4. 客户端不持有本地配置(仅绑定令牌; 服务器地址构建时写死)。
5. WS 桥接不做 permessage-deflate(桥接层无扩展); 浏览器↔内网服务的压缩由各自实现负责。
## Git 提交记录
| hash | 类型 | 说明 | 状态 |
|---|---|---|---|
| `d3ec4ae` | docs(AI) | progress 追加本次会话条目(断线诊断 + header_timeout_s) | **已本地 commit, 未 push** |
| `872ebe9` | feat(AI) | 隧道配置项 header_timeout_s(响应头窗口 默认30/上限600) + 阶段 F 9 项 3 轮全 PASS | **已本地 commit, 未 push** |
| `1d4e4b5` | docs(AI) | deploy 文档 5b(WS 握手桥接验证) + README 自测环境说明 | **已本地 commit, 未 push** |
| `cd9650c` | docs(AI) | progress 归档精简(107→精简版; 全量在 ai_buckup) | **已本地 commit, 未 push** |
| `7a3b37a` | build(AI) | 重编译 exe + tools/exe_e2e_test.py(X1-X10; 24-26 轮全 PASS) | **已本地 commit, 未 push** |
| `5e3c7fd` | feat(AI) | 隧道协议改为 http/tcp/udp, 新 tcp 支持 WebSocket 握手桥接(20 轮全 PASS) | **已本地 commit, 未 push** |
(历史提交见 `git log`; 本表自 2026-11-03 起记录本地 commit hash。)
