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
- [x] 客户端: authtoken 认证(`-a`/控制台)→握手拉配置→TCP/UDP 转发+令牌桶限流+自动重连; T_CONFIG 热更新(端口/带宽即生效, 协议变更自动重连); `-s/--server`; build.bat(PyInstaller)
- [x] 网页管理端: 单文件管理页(登录/隧道 CRUD+在线状态/用户管理/改密/认证令牌签发)
- [x] nginx 反代配置、docker-compose、公网侧测试工具、端到端 selftest
- [x] 本机冒烟实测: selftest 9/9 PASS + api_check 全 PASS (sqlite+Redis, 2026-09-11)
- [x] PyInstaller 出 exe: client/dist/nattunnel-client.exe (~13.3MB), EXE 全链路 E2E PASS(真实回显)
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

## 踩坑备忘(后续会话必读)

1. **PyInstaller --onefile 是双进程**: `Stop-Process -Id <父>` 会留下孤儿子进程继续运行;
   杀 exe 一律用 `taskkill /F /IM nattunnel-client.exe`。
2. **TCP 竞态**: 公网侧数据帧可能早于 LAN 客户端建连完成到达 → 主循环须先 `await open_tasks[pid]`
   再入队(已在 run_tcp 中实现); 新增流逻辑时保持该顺序。
3. `asyncio.DatagramTransport.sendto()` 是**同步方法**, 不能 await。
4. sqlite 冒烟路径在并发 WS 风暴下会锁竞争卡死(服务器可自恢复); MySQL 生产无此问题,
   但中继仍是单 worker 内存态 — 扩容前需 Redis Pub/Sub 化。
5. `RSA` 私钥对象无 `public_bytes`, 要 `key.public_key().public_bytes(...)`。

## 风险 / 注意事项

1. 公网侧不鉴权 → 必须上 TLS; 短链可猜 → 敏感业务轮换 ID / 防火墙限 IP。
2. 多 worker 会破坏内存中继; 扩缩容需先做 Redis Pub/Sub 化(未来工作)。
3. TCP 流为“每公网连接一条”, 不支持半关细粒度之外的复杂语义(对 RDP/SSH/HTTP 足够)。
4. 客户端不再持有任何账号密码(仅 authtoken JWT) — 分发场景只需随附 config.json + 网页签发的令牌。
