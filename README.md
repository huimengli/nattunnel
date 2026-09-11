# nattunnel — 本地端口经公网服务器隧道映射

无公网 IP 的电脑, 通过一台有公网 IP 的服务器(nginx 反代 `www.xxx.com`),
把本机端口转发到公网入口 `www.xxx.com/tunnel/<8位短链>`。

```
[互联网用户]                [服务器(公网IP)]                      [内网电脑]
   |                            |                                    |
   | wss://www.xxx.com/        | nginx                             | nattunnel-client.exe
   |   tunnel/XgMacp2G  ------>|  /tunnel/* --WS--> FastAPI :8000  <-|  (出站 WS, Bearer JWT)
   |                            |              |                    |
   |                            |        MySQL(用户/隧道)  Redis(在线状态/限流计数)
```

- **客户端** `client/nattunnel_client.py`(可打包 exe): 携带 authtoken(JWT, `-a` 参数或控制台输入)
  → 握手拉取隧道配置(前端端口 / tcp|udp / 带宽上限) → 出站 WS 建隧, 转发本机 `<local_target_host>:<local_port>`;
  服务端改配置后经 `T_CONFIG` 帧**热更新**(端口/带宽即生效, 协议变更自动重连)。
- **后端** `server/`: FastAPI + MySQL(SQLAlchemy) + Redis, JWT 鉴权, RSA 公钥下发, `/tunnel/{id}` WebSocket 中继。
- **公网侧**无需装任何东西: 任何支持 WS 的二进制客户端连 `wss://www.xxx.com/tunnel/<ID>` 即可;
  仓库自带测试工具 `tools/ws_tcp_test.py` / `tools/ws_udp_test.py`。
- **浏览器直开(tcp 隧道)**: 同一路径 `/tunnel/<ID>` 同时接受普通 HTTP/HTTPS 请求 ——
  服务端把请求经隧道直转发到本地服务并按流回传, 浏览器打开短链即可访问内网的 Web 应用;
  udp 隧道仅支持 WS。
- **HTML `<base>` 注入**: 对 text/html 响应在 `<head>` 后注入 `<base href="/tunnel/<ID>/">`,
  把相对引用(CSS/JS/图标, 含 JS 运行时 fetch)钉在隧道前缀内 — 子路径部署的 Web 应用(liteLLM/llama.cpp UI 等)
  浏览器开箱即用; gzip/chunked 的 HTML 不做注入(此类应用需自身支持子路径或改用 WS 客户端)。

## 管理员账号(启动时初始化, 代码无硬编码凭据)

首次启动(数据库中还没有 admin)时自动创建, 凭据来自 `server/.env`(不入库):

- `INITIAL_ADMIN_USERNAME` (默认 `admin`) / `INITIAL_ADMIN_PASSWORD`;
- **密码留空** → 生成 16 位随机强口令, 在启动日志中**一次性**打印;
- 登录后立即改密: `POST /api/password` `{old_password, new_password}`;
- 已存在 admin 时永不覆盖(重启动不重建)。

| 项 | 值 |
|---|---|
| 示例隧道短链 | `XgMacp2G` (tcp, 本机端口 3389, 不限速, 可删) |

## 快速开始(服务器端)

```bash
cd server
# 1) 依赖库(MySQL/Redis), 二选一:
docker compose up -d
#    或自行安装并保证 .env 中连接串正确
# 2) 配置(.env 不入库; 生产环境建议显式设置 INITIAL_ADMIN_PASSWORD)
cp .env.example .env          # 按需修改
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 3) 启动(必须单 worker; 在 server/ 目录下)
python run.py                 # http://127.0.0.1:8000
# 4) 首次启动后: 用 .env/日志中的账号登录, 调用 POST /api/password 修改密码
```

## nginx(服务器)

把 `server/nginx/nattunnel.conf` 放入 `/etc/nginx/conf.d/`, 有证书则启用 443 块,
然后 `nginx -t && nginx -s reload`。关键点是 `/tunnel/` 的 Upgrade 头与长超时;
该 location 同时服务 WS 握手(Upgrade: websocket → `Connection: upgrade`)与普通
HTTP 浏览器请求(map `$connection_upgrade`, 无 Upgrade 时 → close);
`location /` 反代网页管理端。

## 网页管理端(浏览器)

直接访问 `http(s)://www.xxx.com/`(或本机 `http://127.0.0.1:8000/`)进入管理页:

- **登录**: 用户名+密码(JSON 经 TLS 传输) → JWT(存 sessionStorage);
- **认证令牌(绑定隧道)**: 选择某条隧道签发携带其 ID 的 authtoken, exe 凭它定位隧道(每行隧道的“令牌”按钮可直接签发);
- **隧道配置**: 列表(含 exe 在线状态/公网连接数)、新建/编辑(协议、本地端口、带宽、启用)/删除;
  改动即时热推给在线客户端(T_CONFIG), 无需重启 exe;
- **客户端接入信息**: 一键复制每条隧道对应的 `config.json` 片段给 exe;
- **用户管理**(仅管理员): 建/删用户; **修改密码**: 本人在线改密。
- 页面每 5 秒自动刷新状态; 单文件无构建依赖(`server/app/static/index.html`)。

## 客户端(内网电脑)

```bash
cd client
# 开发模式
pip install -r requirements.txt
copy config.example.json config.json   # 按实际改 server(tunnel_id 由绑定令牌决定, 不必填)
python nattunnel_client.py -v
# 打包 exe
build.bat                            # 产物 dist\nattunnel-client.exe
```

**启动必须提供 authtoken**(网页管理端对目标隧道签发), 两种途径:

| 途径 | 用法 |
|---|---|
| 命令行 | `nattunnel-client.exe -a <token>` |
| 控制台 | 省略 `-a` 启动, 按提示粘贴 token(可反复重输直到有效) |

**令牌与隧道绑定**: 网页管理端对每条隧道签发的令牌都携带该隧道的 ID。客户端启动时
先 `GET /api/me` 从令牌里读到绑定的 `tunnel_id`, 据此确定要转发哪个隧道的配置 ——
**`config.json` 不再需要写 tunnel_id**(旧配置/未绑定令牌仍可回退用 config 里的值)。
启动横幅会打印**公网访问短链** `http(s)://服务器/tunnel/<8位随机短链>`(服务端自动生成), 供公网侧接入。

可选参数:

- `-s/--server http(s)://公网服务器` — 覆盖 `config.json` 里的 `server`;
- `--config <路径>` — 指定配置位置(exe 默认取同目录 `config.json`)。

`config.json` 只需 `{server, local_target_host?, verify_ssl?}`(无需账号密码与 tunnel_id)。
拿到 token 后客户端依次: `GET /api/me` 校验令牌+读绑定隧道 → `GET /api/tunnels/{id}` 握手取配置
→ `WS /tunnel/{id}` (Bearer) 建隧转发。隧道掉线自动指数退避重连(1s→30s);
令牌失效(401)时客户端会明确提示重新获取。

**热更新**: 在网页管理端修改本地端口/带宽后, 服务端立即经 `T_CONFIG` 帧下发,
运行中的客户端无需重启: 新流直接用新端口, 令牌桶即时改速; 仅协议(tcp↔udp)变更会触发自动重连。

## API 一览 (JWT: `Authorization: Bearer <token>`)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查(redis 状态) |
| GET | `/api/auth/public-key` | RSA 公钥(PEM) |
| POST | `/api/login` | 二选一: `{secure_payload}`=base64(RSA(JSON)) 或 `{username,password}` (网页, 需 TLS) → JWT |
| GET | `/api/me` | 当前用户; 令牌绑定隧道时附带 `tunnel_id` |
| POST | `/api/auth/token` | 为当前登录账号签发 authtoken(不绑隧道, 兼容旧客户端) |
| POST | `/api/tunnels/{tid}/token` | 为指定隧道签发**绑定令牌**(属主或管理员) — exe 推荐用这个 |
| POST | `/api/password` | 修改本人密码 `{old_password, new_password(>=8位)}` |
| GET/POST | `/api/users` | 管理员: 列/建用户 `{username,password,role?}` |
| DELETE | `/api/users/{username}` | 管理员: 删用户(级联删其隧道) |
| GET | `/api/tunnels` | 我的隧道(admin 全部), 含 `lan_online`/`pub_count` |
| POST | `/api/tunnels` | 建隧道 `{tunnel_id?(8位), name?, proto, local_port, bandwidth_kbps?}` |
| GET/PATCH/DELETE | `/api/tunnels/{tid}` | 读/改(端口、协议、带宽、启用)/删 |
| WS | `/tunnel/{tid}` | Bearer JWT 且属主/admin = LAN 侧; 否则公网侧(每条连接一条流) |
| HTTP | `/tunnel/{tid}[/子路径]` | 纯 HTTP 直转(tcp 隧道): 浏览器直接打开短链访问内网 Web 服务; 无 LAN 在线 → 503 |

## 隧道帧协议(WS 二进制消息, 统一 5 字节头 `[type][peer_id BE32]`)

| type | 含义 |
|---|---|
| `0x01` NEW_STREAM | server→LAN: 公网对端(pid)已连接, 请建立本地连接 |
| `0x02` DATA | TCP 字节流(双向, 按 pid 路由) |
| `0x03` FIN | 流结束(半关) |
| `0x04` CLOSE | 流拆除 |
| `0x05` HELLO | server→公网侧: 分配 peer_id |
| `0x21` CONFIG | server→LAN: 配置热更新 JSON `{proto, local_port, bandwidth_kbps}` |
| `0x41` DATA(UDP) | UDP 数据报(双向, 按 pid 路由) |

- TCP: 每个公网 WS 连接 = 一条流; LAN 侧收到 NEW_STREAM 后 connect `<local_target_host>:<local_port>`。
- UDP: 每个公网 WS 连接 = 一个对端; LAN 侧为该对端起 127.0.0.1 临时 socket, 数据报经隧道发到本机服务端口。
- 带宽: `bandwidth_kbps`(0=不限)由客户端令牌桶执行, 双向共用。

## 端到端自测(无需公网)

```bash
# 1) 用 sqlite 起一个临时服务器(冒烟路径 Redis 可缺省), 建议同时设置初始管理员
cd server && DATABASE_URL="sqlite:///./selftest.db" \
    INITIAL_ADMIN_PASSWORD="dev-only-12345" \
    python -m uvicorn app.main:app --port 8100
# 2) 另一终端(测试凭据走参数/环境变量, 不入库)
NATTUNNEL_TEST_PASSWORD=dev-only-12345 python tools/selftest.py --server http://127.0.0.1:8100
python tools/api_check.py --admin-password dev-only-12345 --server http://127.0.0.1:8100
```

## 目录结构

见根目录 `tree.md`; 跨会话进度台账见 `progress.md`, 每次会话记录在 `records/`。

## 安全说明

- **仓库内无任何硬编码账号密码**: 管理员于首次启动时初始化(见上节), `.env` 与 `client/config.json` 均被 gitignore。
- exe 客户端不再持有账号密码: 它使用网页管理端签发的 authtoken(JWT, 默认 7 天)建隧;
  `/api/login` 仍保留双通道(JSON / RSA 加密报文)供 API 侧登录 —— **都依赖 TLS**, 务必为域名配置 443。
- 登录报文可经 RSA(2048) 公钥加密; JWT 默认 7 天有效, 密钥可经 `.env` 或自动持久化。
- 公网侧入口本身不鉴权 —— **务必为 `www.xxx.com` 配置 TLS(443)**, 否则数据明文过网。
- 纯 HTTP 直转使内网 Web 服务可被浏览器**免认证直接访问**: 若该服务自身无登录/鉴权,
  请放在可信网络内或另行加防护(网关鉴权、IP 白名单等)。
- 隧道 8 位短链可被猜到: 敏感业务请配合服务器防火墙限制源 IP, 或定期轮换 `tunnel_id`。
