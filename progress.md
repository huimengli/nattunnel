# nattunnel — 跨会话进度台账

## 项目目标

无公网 IP 电脑 ⇄ 有公网 IP 服务器(nginx 反代 www.xxx.com):
客户端 exe 将本机端口转发到 `www.xxx.com/tunnel/<8位短链>`。
技术栈: Python / FastAPI / MySQL / Redis / JWT / RSA 握手 / WebSocket 中继; 支持 tcp|udp、前端端口与带宽配置; 用户系统(管理员于首次启动时初始化, 凭据经 .env/日志, 代码无硬编码)。

## 关键约定

- 8 位短链: 字母表去混淆字符, 例 `XgMacp2G`; 自动生成或手工指定。
- 角色判定: `/tunnel/{id}` WS 携带有效 Bearer JWT 且为属主/admin → LAN 侧; 否则公网侧(每连接一条流)。
- 帧协议: `[type 1B][peer_id 4B BE][payload]`; 0x01 NEW / 0x02 DATA / 0x03 FIN / 0x04 CLOSE / 0x05 HELLO / 0x21 CONFIG(热更新) / 0x41 UDP-DATA。
- 带宽由客户端令牌桶执行(双向共用, kbps, 0=不限)。
- 中继状态在进程内存(Redis 记录在线状态与登录失败计数) → **必须单 worker**。

## 凭据 / 环境

| 项 | 值 |
|---|---|
| 管理员 | 首次启动初始化: `.env` 的 `INITIAL_ADMIN_USERNAME`(默认 admin)/`INITIAL_ADMIN_PASSWORD`(留空则随机生成+日志一次性打印), 登录后 POST /api/password 修改; 代码与文档无硬编码凭据 |
| 示例隧道 | XgMacp2G (tcp, 3389, 不限速) |
| 后端默认地址 | http://127.0.0.1:8000 (server/ 目录 `python run.py`) |
| MySQL/Redis | server/docker-compose.yml 一键起 |

## 状态总览

- [x] 项目骨架 + 台账(progress.md / tree.md / records/)
- [x] 后端: 用户/JWT/RSA 登录/隧道 CRUD/WS 中继/种子数据
- [x] 客户端: **无本地配置**(config.json 已废除) — 服务器地址 `build.bat -s` 构建时写死; 隧道 ID 由**绑定令牌**决定(`-a`/控制台); 端口/协议/带宽/**本地目标主机**全部运行时从服务器隧道设置读取; T_CONFIG 热更新(端口/带宽/目标主机即生效, 协议变更自动重连); build.bat(PyInstaller --onefile, -s 写死服务器 + favicon.ico 图标)
- [x] 网页管理端: 单文件管理页(登录/隧道 CRUD+在线状态/用户管理/改密/认证令牌签发/**客户端 exe 下载按钮**)
- [x] nginx 反代配置、docker-compose、公网侧测试工具、端到端 selftest
- [x] **纯 HTTP 直转**: 浏览器可直接打开 `http(s)://域名/tunnel/<短链>[/子路径]`(tcp 隧道, HttpBridgeMiddleware); nginx map 头兼容; HTML 响应注入 `<base>` 使相对引用(含 JS fetch)落在隧道前缀
- [x] 本机冒烟实测: selftest 20/20 PASS + api_check 13 项全 PASS (sqlite+Redis, 2026-09-11)
- [x] PyInstaller 出 exe: client/dist/nattunnel-client.exe, EXE 全链路 E2E PASS(真实回显); **图标**: 仓库根 favicon.ico 经 build.bat `--icon` 打进 exe
- [x] 去硬编码凭据: 管理员启动时初始化(.env/随机口令+日志一次性打印) + POST /api/password; 仓库无真实账号密码, 可推 git
- [ ] 部署到真实服务器(MySQL/Redis/nginx TLS)并改管理员密码

## 会话记录

### 2026-09-11 00:33 — records/2026-09-11-00-33-34.md

- 从零搭建全部代码(server/app/*, client/*, tools/*, nginx, docker-compose)。
- 设计并实现帧协议与角色判定; 种子化初始管理员(当时硬编码的凭据已于会话 2 全部移除)与示例隧道 XgMacp2G。
- venv(py3.12)安装依赖; py_compile 全通过; sqlite 冒烟 + uvicorn:8100 实测。
- selftest 端到端 7/7 PASS(TCP/UDP echo 往返, 进程内跑真实客户端代码)。
- api_check PASS(建用户/越权403/建隧道/PATCH/列表/清理)。
- EXE 构建成功并 E2E PASS: 公网侧 WS → EXE(lan) → 本地 echo 服务 → 原样回显。
- 修复 4 个实测发现的 bug(详见记录文件的"缺陷与修复"节)。

### 2026-09-11 11:19 — records/2026-09-11-11-19-31.md

- 因项目要推 git, 移除全部硬编码管理员凭据(lt 账号): seed 改为**首启初始化**(env 密码或随机 16 位口令+日志一次性打印), 已有 admin 永不覆盖。
- 新增 `POST /api/password`(改密); `.env`/`client/config.json` 纳入 gitignore; 测试脚本凭据走参数/环境变量。
- 实测: env 首启/随机首启/改密/重启不重置/api_check 回归 全 PASS; grep 复核仓库零残留, 可安全推送。

### 2026-09-11 14:36 — records/2026-09-11-14-36-13.md

- **配置热更新**: 新增帧 `0x21 T_CONFIG`(server→LAN, JSON)。网页 PATCH 隧道时:
  端口/带宽变更 → 保留房间并即时推给在线客户端(新流用新端口、令牌桶改速);
  协议变更 → 拆房间, 客户端自动重连拉新配置。修复"改端口后运行中的客户端不生效"。
- **客户端 token 化**: 启动必须携带 authtoken — `-a/--auth <token>` 或启动后控制台粘贴(可反复输入);
  `GET /api/me` 先校验令牌, 再握手拉隧道配置建隧。exe 不再持有账号密码; RSA 登录通道保留在 API 侧。
- **客户端 CLI**: 新增 `-s/--server` 覆盖 config.json 的 server; config.json 精简为
  `{server, tunnel_id, local_target_host?, verify_ssl?}`(去掉 username/password, 旧配置兼容)。
- **网页管理端**: 新增"认证令牌"卡片(生成/复制 authtoken 供 exe 使用); 客户端接入信息片段同步更新。
- 新接口 `POST /api/auth/token`; selftest 新增 A4/A5 热更新检查(9/9 PASS); api_check 回归 PASS;
  EXE 实测: `-a` 建隧 ✓ / stdin 粘贴建隧 ✓ / 无效令牌退出码2 ✓ / 运行中改端口即生效(E2E probe) ✓。

### 2026-09-11 15:15 — records/2026-09-11-15-15-26.md

- **令牌绑定隧道**: JWT 增加 `tunnel_id` 声明; 新接口 `POST /api/tunnels/{tid}/token`(属主/admin);
  `/api/me` 回带绑定 ID。客户端凭令牌确定隧道配置(config.json 不再需要 tunnel_id, 旧配置回退兼容)。
- 客户端启动横幅新增**公网访问短链** `{server}/tunnel/<8位随机短链>`(服务端自动生成)。
- 网页管理端: 令牌卡片改为按隧道签发 + 每行"令牌"按钮; 接入片段去 tunnel_id。
- selftest 新增阶段 C(C1–C4, 无 tunnel_id 配置凭令牌定位) → **13/13 PASS**; api_check 加绑定令牌检查 → ALL PASS。

### 2026-09-11 16:53 — records/2026-09-11-16-53-59.md

- **诊断**: 用户浏览器访问短链显示 `{"detail":"Not Found"}` — 非故障: `/tunnel/{id}` 原为纯 WS 端点,
  普通 GET 落到静态挂载; 隧道与客户端均正常(经隧道实测收到 llama.cpp 200 HTML)。
- **增强(浏览器直开短链)**: 新增 `server/app/http_bridge.py` — `/tunnel/{tid}[/子路径]` 接受纯 HTTP(无 Upgrade),
  每请求一条隧道流转发到本地服务并流式回传(头限时 20s/CL+chunked+FIN 判尾; LAN 离线 503, udp 400)。
  main.py: 注册中间件 + LAN 循环补转发 T_FIN; nginx conf: map `$connection_upgrade`(普通 HTTP 不再强推 upgrade)。
- 踩坑两例: ①隧道模型无 `local_target_host`(Host 头改固定 127.0.0.1:<port>); ②`_send_lan` 空 payload 丢帧致 T_NEW 静默丢失 → 504, 已修。
- selftest 新增阶段 D(D1–D5) → **18/18 PASS**; api_check 加"HTTP 直转离线 503" → ALL PASS;
  实战: 浏览器式 GET `xN5MYedx` → **llama.cpp UI 200 完整 HTML** ✓。

### 2026-09-11 18:00 — records/2026-09-11-18-00-20.md

- **白屏诊断**: 浏览器开短链后页面本体 200, 但相对资源(`_app/...`/favicon/manifest)解析到
  `/tunnel/_app/...`(掉出隧道 ID 前缀)→ 静态 404 白屏。根因: 文档 URL 不带尾斜杠时, 浏览器相对引用
  以 `/tunnel/` 为基点。
- **修复(HTML `<base>` 注入)**: 纯 HTTP 直转对 text/html 响应在 `<head>` 后注入
  `<base href="/tunnel/{tid}/">`, 钉住全部相对解析(含 JS 运行时 fetch); Content-Length 同步 +注入长;
  gzip/chunked 响应不注入(chunked 显式块大小会被插字节破坏)。
- 踩坑: 首版注入后 `pop_body` 仍按原 CL 截断 → 尾部 31 字节丢失; `note_injected()` 修复。
- selftest 新增 D6(注入 + CL 同步) → **19/19 ALL PASS**; api_check 回归 ALL PASS;
  实战: llama.cpp UI 经隧道全资源加载(页面 12669B / bundle.js 8.8MB / CSS 542KB / favicon / manifest),
  `/health` 透传 OK。

### 2026-09-11 18:15 — records/2026-09-11-18-15-06.md

- **网页客户端下载按钮**: 新接口 `GET /api/client/download`(登录态) 流式返回
  `client/dist/nattunnel-client.exe`; 未构建 → 404 提示 build.bat。
  管理端"exe 客户端接入信息"卡片加"下载客户端 exe"按钮(Blob 下载, 401 自动登出)。
- selftest 新增 D7(200 + exe 大小) → **20/20 ALL PASS**; api_check 加下载检查 → ALL PASS;
  实测: 带令牌 200 / CL=13341996(与磁盘一致) / `attachment` 头, 无令牌 401。

### 2026-09-11 18:57 — records/2026-09-11-18-57-13.md

- **exe 图标**: 用户放置仓库根 `favicon.ico`(64×64 ICO, 16958B); `build.bat` 加
  `--icon ..\favicon.ico`(缺失时跳过不中断)。重新构建并**验证字节级嵌入**(PE 资源区含完整 16936B 图像载荷)。
- 构建前需停掉运行中的 exe(onefile 父进程锁文件); 重建后重启 XgMacp2G 与 xN5MYedx 两客户端均在线,
  llama.cpp `/health` 与下载端点(新 CL=13299500)正常。

### 2026-09-11 19:49 — records/2026-09-11-19-49-36.md

- **废除 config.json**: 客户端零本地配置。服务器地址 `build.bat -s <url>` 生成 `client/build_config.py`
  随 PyInstaller 写死进 exe; 隧道 ID 只认绑定令牌; `local_target_host` 新加为**隧道字段**
  (models/schemas/API/网页弹窗/T_CONFIG), 客户端从服务器配置读取并可热更新。
- selftest 20/20 + api_check 13 全 PASS; 实测 PATCH local_target_host 热更新后新流即用新主机。

## 踩坑备忘(后续会话必读)

1. **PyInstaller --onefile 是双进程**: `Stop-Process -Id <父>` 会留下孤儿子进程继续运行;
   杀 exe 一律用 `taskkill /F /IM nattunnel-client.exe`。
2. **TCP 竞态**: 公网侧数据帧可能早于 LAN 客户端建连完成到达 → 主循环须先 `await open_tasks[pid]`
   再入队(已在 run_tcp 中实现); 新增流逻辑时保持该顺序。
3. `asyncio.DatagramTransport.sendto()` 是**同步方法**, 不能 await。
4. sqlite 冒烟路径在并发 WS 风暴下会锁竞争卡死(服务器可自恢复); MySQL 生产无此问题,
   但中继仍是单 worker 内存态 — 扩容前需 Redis Pub/Sub 化。
5. `RSA` 私钥对象无 `public_bytes`, 要 `key.public_key().public_bytes(...)`。
6. **存量库加列**: 项目无迁移框架 — 在 `seed.py` 的 init_db 里做幂等 `ALTER TABLE`(捕获
   duplicate column/1060 忽略); 新建库由 create_all 处理。schema 演进一律走这个模式。

## 风险 / 注意事项

1. 公网侧不鉴权 → 必须上 TLS; 短链可猜 → 敏感业务轮换 ID / 防火墙限 IP。
2. 多 worker 会破坏内存中继; 扩缩容需先做 Redis Pub/Sub 化(未来工作)。
3. TCP 流为“每公网连接一条”, 不支持半关细粒度之外的复杂语义(对 RDP/SSH/HTTP 足够)。
4. 客户端不再持有任何本地配置(仅 authtoken JWT; 服务器地址构建时写死, 其余全从服务器读) — 分发场景只需 exe + 网页签发的绑定令牌。
