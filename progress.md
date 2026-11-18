# nattunnel — 跨会话进度台账

<!-- 已归档于 ai_buckup/progress.md_2026-11-18-18-06-00.md (120 行全量: 2026-09 与 2026-11-03~11-04 的会话条目、部署排障与踩坑全量均在备份) -->
<!-- 2026-11-18 18:06 归档动作: 精简历史会话摘要与踩坑条目, 新增本次 Unity UDP 入口会话条目 -->

## 项目目标
无公网 IP 电脑 ⇄ 有公网 IP 服务器(nginx 反代 www.xxx.com): 客户端 exe 将本机端口转发到 `www.xxx.com/tunnel/<8位短链>`。
技术栈: Python / FastAPI / MySQL / Redis / JWT / RSA 登录 / WebSocket 中继; 用户系统(管理员首启初始化, 代码无硬编码)。

## 关键约定
- 8 位短链: 字母表去混淆字符, 例 `XgMacp2G`; 自动生成或手工指定。
- 角色判定: `/tunnel/{id}` WS 携带有效 Bearer JWT 且属主/admin → LAN 侧; 否则公网侧(每连接一条流)。
- 帧协议: `[type 1B][peer_id 4B BE][payload]`; 0x01 NEW / 0x02 DATA / 0x03 FIN / 0x04 CLOSE / 0x05 HELLO / 0x21 CONFIG(热更新) / 0x41 UDP-DATA。
- 带宽由客户端令牌桶执行(双向共用, kbps, 0=不限)。
- 中继与 UDP 房间状态都在进程内存(Redis 记在线/限流) → **必须单 worker**。
- **协议语义(2026-11-03)**: `http` = 混合(浏览器直开逐请求直转 + WS 握手桥接 + 子协议裸流); `tcp` = 原生字节流(纯 HTTP → 400); `udp` = 数据报(WS 握手 → 400)。
- **UDP 原生入口(2026-11-18)**: `udp` 隧道可配 `udp_port`(公网 UDP 端口)+ `udp_allow`(源 IP 白名单); 服务器在该端口直接收发数据报(nginx 不参与); 每个源 `ip:port` = 一个 peer_id(与子协议裸流共用房间与 `0x41` 帧); Unity `UnityTransport` 填 `Server Address=公网IP` + `Server Port=udp_port`(`docs/unity-ngo.md`)。
- 公网侧 WS 归属: Bearer→LAN; 子协议 `nattunnel-relay`→帧协议裸流; 其余(浏览器式)→ http_bridge 桥接到内网 WS 服务。
- 存量 `proto=tcp`(旧浏览器直开语义)首启一次性迁移为 `http`(`proto_tcp_to_http_v1` 标记)。
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
- [x] 客户端: 无本地配置(绑定令牌 + 构建时写死服务器地址); T_CONFIG 热更新; PyInstaller --onefile + favicon 图标
- [x] 网页管理端: 单文件(登录/隧道 CRUD+在线状态/用户/改密/绑定令牌签发/exe 下载); 协议三项 + 语义说明
- [x] 纯 HTTP 直转(http 隧道): 逐请求直转 + `<base>` 注入 + Content-Length 同步; nginx map 头兼容
- [x] WebSocket 握手桥接(http/tcp): 101 + 数据帧/文本帧双向透明, `Sec-WebSocket-Key` 原样转发, 子协议回带; 拒绝码 404/502/504 与内网非 101 透传
- [x] 混部子路径部署(nginx embed + 相对路径 + API_BASES 回退) + docs/deploy-keliit-top.md
- [x] 端到端自测: selftest 阶段 A(字节流+热更新)/B(UDP)/C(绑定令牌)/D(纯 HTTP 直转+tcp 拒绝)/E(WS 握手桥接) + api_check, 20 轮全 PASS(2026-11-03)
- [x] 管理页“编辑隧道”保存(2026-11-04): `editingTid` 判定 + 编辑态 ID 只读; 台架 3 轮全 PASS
- [x] exe 双下载通道(登录态 API + 静态直链, 单副本 server/app/static/)
- [x] 隧道配置项 `header_timeout_s`(2026-11-04): 响应头窗口 默认30/上限600, 逐请求读库(改后下一个请求生效); 阶段 F 9 项 3 轮全 PASS
- [x] **UDP 原生入口(2026-11-18)**: `udp_port`/`udp_allow` + `udp_relay.py` + `GET /api/udp/status` + 管理页 Unity 提示 + 阶段 G 21 项 + 台架 20 轮 9/9 全 PASS
- [ ] 部署到 www.keliit.top: 子路径 `/tunnel/` + `/nattunnel-admin/`; 部署后改管理员密码(步骤见 docs/deploy-keliit-top.md)
- [ ] `tcp+udp` 同端口双栈混合(2026-11-03 用户选方案A, 列为后续需求)
- [ ] Unity 真机(UnityTransport) + 真实公网/防火墙入站 UDP 验一次; 客户端首包修复后重编译 exe(`client\build.bat`) 并 `tools/exe_e2e_test.py` 验收
## 会话摘要(最近)
### 2026-11-18 13:03 — records/2026-11-18-13-03-15.md (Unity 标准 UDP 入口, 分支 feature/AI_Unity)
- 需求原文: “现在项目的 UDP 不是标准的 UDP 端口映射, 我需要给 UNITY 用…新增一个 unity 选项? 走分支 feature/AI_Unity”。定案: **不新增 `proto=unity`** —— Unity 用的就是标准 UDP(新增取值=同一语义两个名字); Unity 入口 = `udp` 隧道 + `udp_port`/`udp_allow` + 管理页提示 + `docs/unity-ngo.md`。
- `server/app/udp_relay.py`(新): `UdpPeer`(与 LAN 侧 `_QueuePub` 接口兼容, 只投 `0x41` payload)/`UdpEndpoint`(asyncio DatagramProtocol: 白名单/尺寸/对端上限校验, 新源地址→建 peer)/`UdpHub`(port→端点; bind/sync/unbind; 启动时按库绑定; 5s GC 回收空闲 `UDP_IDLE_TIMEOUT=60`)。
- `main.py`: lifespan 起停 hub + `GET /api/udp/status`(仅登录); `routers/tunnels.py`: 公网 UDP 端口唯一(409 提示占用者)、非 `udp` 清空字段、保存后 `sync`、删除 `unbind`; `config.py` 五参数 + `.env.example` UDP 段; `models.py`/`seed.py` 幂等 ALTER。
- 管理页: `UDP 端口(公网)` 列 + `tm-udpport`/`tm-udpallow` + Unity(UnityTransport 填法 + 防火墙入站 UDP) 提示 + 启动片段 UDP 段; `saveTunnel` 校验“UDP 端口仅 udp 隧道”。
- 客户端 `run_udp` **首包竞态修复**(`peer_tasks`: `U_DATA` 早于建端任务完成时先 `await` 再投数据) —— 与 2026-11-03 TCP 路径同款同法; exe 需重编译才生效。
- 实测: 台架 `tools/unity_udp_test.py` **20 轮 9/9 全 PASS**(每轮 selftest 53 项 阶段 A-G + api_check 26 项) → tests/2026-11-18-13-03-15_cc/(record_1..20 + opinion.md), 累积 tests/opinion.md。

### 2026-11-04 20:17 — records/2026-11-04-20-17-27.md (管理页“编辑隧道”保存: 大小写短链)
- 根因: `saveTunnel()` 用大写化输入查表判编辑/新建 → `smGvs9xQ` ≠ `SMGVS9XQ` → POST → MySQL CI → 409; 修复: `editingTid` + 编辑态 ID 只读 + 8 位大小写正则; 台架 3 轮全 PASS; 部署只需覆盖 index.html(无需重启)。

### 2026-11-04 19:06 — records/2026-11-04-19-06-38.md (断线诊断 + header_timeout_s)
- 504 起算点在**请求体全部入隧后 20 秒**(`_HEADER_TIMEOUT`), 46 图 prefill 必然超时; 1011 keepalive 是下游表现, 不是真断线(nginx 会话 17.6MB/67s ≈ 2Mbps)。新增 `header_timeout_s`(默认 30, 1..600) 与 `bandwidth_kbps` 同管线(逐请求读库, 不推 T_CONFIG); WS 握手窗口固定 20 秒; 阶段 F 9 项 3 轮全 PASS; 待办: 生产 llm 隧道设 300 + 核实 nginx `proxy_read_timeout`。

### 2026-11-03 13:21 — records/2026-11-03-13-21-03.md (协议改造 http/tcp/udp + WS 握手桥接)
- `relay.py`: `PROTOS=(http,tcp,udp)` + `RELAY_SUBPROTOCOL` + `data_type(proto)` + `alloc_peer(room)`; `http_bridge.py` 新增 `WSCodec`(最小 RFC6455 编解码) + 握手透传(101 用同一 key 完成 accept 并回带子协议) + `_ws_reject` 预拒绝; 纯 HTTP 直转限定 `http`; 客户端 `proto in (tcp,http)` 走字节流, 仅切 `udp` 需重连; 方案A(用户拍板): 不新增 mixed, `http` 即混合; 20 轮全 PASS(32+18)。
## 踩坑备忘(后续会话必读)
1. **PyInstaller --onefile 是双进程**: 杀 exe 用 `taskkill /F /IM nattunnel-client.exe`(父进程 kill 会留孤儿子进程)。
2. **TCP 建连竞态**: 公网侧数据帧可能早于 LAN 客户端建连完成 → 主循环先 `await open_tasks[pid]` 再入队(UDP 路径同款见 17)。
3. `asyncio.DatagramTransport.sendto()` 是**同步方法**, 不能 await。
4. sqlite 在并发 WS 风暴下会锁竞争卡死(可自恢复); 生产 MySQL 无此问题; 中继仍是单 worker 内存态(扩容前需 Redis Pub/Sub 化)。
5. RSA 私钥对象无 `public_bytes`, 用 `key.public_key().public_bytes(...)`。
6. **存量库加列**: 无迁移框架 → `seed.py` init_db 里幂等 `ALTER TABLE`(捕获 duplicate/1060); 新库由 create_all(本次 `udp_port`/`udp_allow` 同此模式)。
7. **.bat 必须纯 ASCII**: 中文 Windows cmd 按 GBK 解析 .bat, UTF-8 中文注释会乱码甚至吃掉后续语句。
8. **本机解释器无 `secrets.randbytes`**(3.12 定制安装): 需要随机字节用 `secrets.token_bytes(n)`。
9. **WS 桥接路径语义**: 裸流(子协议)必须连裸路径 `/tunnel/<ID>`; 带子路径不匹配路由被 403; `websocket.http.response` 预拒绝时 uvicorn 打 `ASGI callable returned without completing handshake` 属正常表征。
10. **PyInstaller --onefile 需要可写的系统 TEMP**: 受限环境报 `Cannot create temporary directory!`; 给子进程设 TEMP/TMP 到仓库内目录即可。
11. **pip 直连 pypi 本机仅 17 kB/s 且 resolver 回退到 pyinstaller 4.x**: 用 Tsinghua 镜像 + 固定版本; `run_code` 的 `process.env` 是空的(无 TEMP/USERPROFILE/SystemRoot), spawn 子进程需自行补齐。
12. **spawn 子进程随 run_code 进程退出即被杀**: 服务器与测试须在同一程序内起停; stdin 用 pipe(非 tty)才走非交互首启(`server/.env` 缺失时 tty 会触发首启向导)。
13. **504 的语义边界**: `504 = 响应头窗口超时`(起算点在请求体入隧后), 不代表隧道断开; `502 = 转发失败/链路中断`; `503 = room.lan is None`; 窗口超时后 finally 发 T_CLOSE 掐本地请求, 内网服务随后写响应会 ConnectionAborted(10053) 属预期。
14. **短链是大小写混合的**: `ALPHABET` 含小写; UI 侧 `toUpperCase()` 会破坏查表与 PATCH(编辑保存变 POST → CI → 409) 并静默改写手填 ID; 判定须用 `editingTid`。
15. **同进程“回显 + 收包”必须非阻塞**: 自测 `udp_native` 用阻塞 `recv` 会饿死事件循环 → 同进程的回显服务永不触发(阶段 G5-G8 必失败); 改 `setblocking(False)` + `await asyncio.sleep` 轮询。
16. **改 import 不要整块替换**: 为加 `/api/udp/status` 重排 `main.py` import 时删掉 `from .relay import (...)` → `RELAY_SUBPROTOCOL` 未定义 + 握手未完成表征, 自测 A-E/G 的“lan 侧上线”全 FAIL(不是语法错误, 难一眼看出)。
17. **UDP 入口首包竞态(与 TCP 同款)**: `run_udp` 的 `T_NEW` 建端是 `create_task`, 首包 `U_DATA` 早于其完成 → `peers.get(pid)` 为 None 丢首包; **服务端加睡眠无效**(帧已在客户端缓冲), 必须客户端 `await peer_tasks[pid]` 再投数据。
18. **台架 Windows 细节**: `stdin=subprocess.DEVNULL` 使 `isatty()` 为真 → 服务器走首启向导后 `SystemExit: 已取消`(用 `stdin=PIPE`); 进程退出后 sqlite 文件释放有延迟(删库需重试); 父进程 GBK 控制台打印中文需 `sys.stdout.reconfigure` + 子进程 `PYTHONIOENCODING=utf-8`。
## 风险 / 注意事项
1. 公网侧入口不鉴权(含 WS 桥接与子协议裸流) → 必须上 TLS; 短链可猜 → 敏感业务轮换 ID / 防火墙限 IP。
2. 多 worker 会破坏内存中继(含 UDP 房间); 扩缩容需先做 Redis Pub/Sub 化(未来工作)。
3. 字节流为“每公网连接一条”, 不支持半关等细粒度语义(对 RDP/SSH/HTTP/WS 足够)。
4. 客户端不持有本地配置(仅绑定令牌; 服务器地址构建时写死)。
5. WS 桥接不做 permessage-deflate(桥接层无扩展); 浏览器↔内网服务的压缩由各自实现负责。
6. **UDP 原生入口不鉴权且不经 nginx**: 需放行**入站 UDP**(与 443/80 无关), 用 `udp_allow` + 随机高位端口; Unity `Server Address` 是公网 IP(域名需先解析), 换 IP 要改 Unity 配置; 服务端不重复限流(限速在客户端)。
## Git 提交记录
| hash | 类型 | 说明 | 状态 |
|---|---|---|---|
| `2dd3a20` | feat(AI) | UDP 原生入口(`udp_port`/`udp_allow` + `udp_relay.py` + `/api/udp/status` + 管理页 Unity 提示 + 客户端首包竞态修复; 20 轮 9/9 全 PASS) | **已本地 commit, 未 push** |
| `c79f236` | docs(AI) | progress 归档(120 行全量入 ai_buckup) + 本次会话条目 + tree/README/unity 文档 | **已本地 commit, 未 push** |
| `2572c29` | docs(AI) | progress 精简至 100 行内 + 补 hash(本表后续 hash 见 `git log`) | **已本地 commit, 未 push** |
| `7844f34` | fix(AI) | 管理页“编辑隧道”保存走 POST → 409(大小写短链); 台架 3 轮全 PASS | **已本地 commit, 未 push** |
| `d3ec4ae` | docs(AI) | progress 追加本次会话条目(断线诊断 + header_timeout_s) | **已本地 commit, 未 push** |
| `872ede9` | feat(AI) | 隧道配置项 header_timeout_s(响应头窗口 默认30/上限600) + 阶段 F 9 项 3 轮全 PASS | **已本地 commit, 未 push** |
| `1d4e4b5` | docs(AI) | deploy 文档 5b(WS 握手桥接验证) + README 自测环境说明 | **已本地 commit, 未 push** |
| `cd9650c` | docs(AI) | progress 归档精简(107→精简版; 全量在 ai_buckup) | **已本地 commit, 未 push** |
| `7a3b37a` | build(AI) | 重编译 exe + tools/exe_e2e_test.py(X1-X10; 24-26 轮全 PASS) | **已本地 commit, 未 push** |
| `5e3c7fd` | feat(AI) | 隧道协议改为 http/tcp/udp, 新 tcp 支持 WebSocket 握手桥接(20 轮全 PASS) | **已本地 commit, 未 push** |
(历史提交见 `git log`; 本表自 2026-11-03 起记录本地 commit hash。)
