# 部署 nattunnel 到 www.keliit.top（与现有站点混部）

www.keliit.top 现有 vhost 已跑着其他站点（443/TLS 已就绪）。nattunnel 以**子路径**方式嵌入，
不新建 server 块、不改现有站点：

| 对外地址 | 内容 |
|---|---|
| `https://www.keliit.top/tunnel/<短链>` | 隧道入口（WS 中继 + 纯 HTTP 直转短链），免登录 |
| `https://www.keliit.top/nattunnel-admin/` | 网页管理端（JWT 登录；前缀可自改，见 nginx 注释） |

客户端：已构建的 `client/dist/nattunnel-client.exe`（**内置 www.keliit.top**），局域网 PC 上
`nattunnel-client.exe -a <绑定令牌>` 即可，无需任何配置。

## 1. 上传代码（保留仓库根布局）

```bash
mkdir -p /opt/nattunnel
# 只需上传 server/ 树即可(client/ 仅构建 exe 用, 服务器上不再需要):
#   /opt/nattunnel/server/          后端(其中 app/static/nattunnel-client.exe 即客户端 exe 唯一副本,
#                                   两条下载通道都读它; build.bat 构建后自动同步)
```

> 不要上传 `server/.env`、`server/local.db`（本地测试数据，gitignore 里已排除）。

## 2. Python 环境 + 依赖

```bash
cd /opt/nattunnel/server
python3 -m venv .venv                    # 需要 Python >= 3.10
.venv/bin/pip install -r requirements.txt   # 大陆服务器可先配 pip 镜像源
```

## 3. 首次启动（终端交互配置：自动建 .env + 管理员）

**无需手工写 .env** — 在服务器终端里直接跑，控制台向导会引导全部配置：

```bash
cd /opt/nattunnel/server
.venv/bin/python run.py            # 8000 被其他站占用时: .venv/bin/python run.py --port 9000
```

向导流程（`.env` 不存在时触发；已存在则跳过直连）：

```text
[1] MySQL   [2] sqlite 文件(最快, 免安装)      <- 选 1 时依次输入:
MySQL 主机 [127.0.0.1]:            # 默认值直接回车
MySQL 端口 [3306]:
数据库名 [nattunnel]:
账号 [nattunnel]:
密码:                              # 隐藏输入; 立即试连
  (若 Access denied / 库不存在 => 可再输 MySQL root 密码, 向导自动建库+建账号+授权)
Redis 连接串(没有可留空) [redis://127.0.0.1:6379/0]:
=> 写入 server/.env(JWT_SECRET 自动生成)

检测到尚无管理员账号, 请在控制台创建
管理员用户名 [admin]:
管理员密码(至少 8 位, 隐藏输入): _
请再次输入管理员密码: _
```

看到 `Application startup complete` 即就绪；`Ctrl+C` 退出，然后交给 systemd（下节）。
> 已有现成 `.env`（模板见 `server/.env.example`）则向导不触发；非交互场景
> （systemd/docker）回退 `.env` 的 DATABASE_URL / INITIAL_ADMIN_PASSWORD。

## 4. systemd 服务（单 worker！中继状态在进程内存）

`/etc/systemd/system/nattunnel.service`：

```ini
[Unit]
Description=nattunnel server
After=network.target

[Service]
# 下面两处改成你的实际安装目录(示例 /opt/nattunnel; venv 名以你创建时为准, .venv 或 env)
WorkingDirectory=/opt/nattunnel/server
ExecStart=/opt/nattunnel/server/.venv/bin/python run.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

> **端口以 `.env` 的 PORT 为准**(ExecStart 不带 --port): 向导默认写入 `PORT=8000`,
> 若你要用 9000, 先 `sed -i 's/^PORT=8000$/PORT=9000/' .env`(nginx proxy_pass 同步)。

```bash
systemctl daemon-reload
systemctl enable --now nattunnel
journalctl -u nattunnel -f     # "Application startup complete" 即就绪; 之后每次重启不再出现任何提示
```

> HOST 默认只绑 `127.0.0.1`（混部安全）；**不要**在防火墙放通 8000 端口。

## 5. nginx：嵌入现有 vhost

参考 `server/nginx/nattunnel-embed.conf`：

1. **http { }** 上下文中加：
   ```nginx
   map $http_upgrade $nattunnel_conn { default upgrade; '' close; }
   ```
   （配置里已有等价 map 就直接复用其变量名）
2. **www.keliit.top 的 server { ... }** 块内加两个 location：
   - `location /tunnel/` → `proxy_pass http://127.0.0.1:8000;`（原样直通，含 Upgrade 头）
   - `location /nattunnel-admin/` → `proxy_pass http://127.0.0.1:8000/;`（剥前缀）
3. 确认现有站点没有占用 `/tunnel/`、`/nattunnel-admin/` 前缀。
4. `nginx -t && nginx -s reload`

## 6. 验证清单

```bash
curl -s https://www.keliit.top/nattunnel-admin/api/health   # => {"ok":true,...}
```

1. 浏览器开 `https://www.keliit.top/nattunnel-admin/` → 用第 4 步控制台设置的账号密码登录
2. 建隧道（本地目标主机填局域网内机器的 IP/域名，如 llama.cpp 所在主机的地址）
3. "签发客户端令牌" 得到绑定令牌
4. 局域网 PC 运行 `nattunnel-client.exe -a <令牌>` → 管理面板该隧道显示在线
   > **exe 必须是 2026-09-12 之后的构建**: 旧构建缺 cryptography 依赖(启动即崩),
   > 且不带 API 前缀回退(混部下握手 404)。若走"下载 exe"按钮分发,
    > (build.bat 构建后自动同步) — 两条下载通道读的是同一份文件, 只需上传这一份。
5. 浏览器直开短链 `https://www.keliit.top/tunnel/<短链>` → 目标服务页面
 6. 管理端 exe 卡片有两条下载通道(内容一致): 登录态接口 /api/client/download 与静态直链 /nattunnel-admin/nattunnel-client.exe — 两者都读 server/app/static/nattunnel-client.exe(服务器唯一副本), 直链免登录

## 7. 安全注意

- TLS 由现有 443 证书提供；客户端**强制校验证书**，证书无效则连不上。
- 8 位短链可被枚举：敏感服务建议给该隧道单独加 nginx `allow/deny` IP 白名单，或定期换短链。
- 管理端路径 `/nattunnel-admin/` 有 JWT 保护；如需更隐蔽可在第 5 步改前缀名（同一份代码直接生效）。

## 8. 日后切 MySQL（可选）

```bash
docker compose up -d mysql        # 仓库根 docker-compose.yml, 建 nattunnel 库/用户
# .env: DATABASE_URL=mysql+pymysql://nattunnel:nattunnel@127.0.0.1:3306/nattunnel?charset=utf8mb4
systemctl restart nattunnel        # 首启自动建表(含存量列幂等迁移)
```

## 9. 故障排查

### `Access denied for user 'nattunnel'@'localhost' (using password: YES)`

两种根因, 按序检查：

1. **`.env` 里 DATABASE_URL 的密码与 MySQL 实际用户密码不一致** — 两者必须一字不差。
2. **用户主机范围坑**: DSN 经 TCP 连 `127.0.0.1`, MySQL 的用户匹配的是 `'nattunnel'@'127.0.0.1'`
   或 `'nattunnel'@'%'`; 只建了 `'nattunnel'@'localhost'`(仅 unix socket) 时 TCP 连接会被拒。

一键修复 SQL（root 登录执行, `<你的密码>` 换成与 `.env` 一致的值）：

```sql
CREATE DATABASE IF NOT EXISTS nattunnel CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'nattunnel'@'127.0.0.1' IDENTIFIED BY '<你的密码>';
CREATE USER IF NOT EXISTS 'nattunnel'@'localhost'  IDENTIFIED BY '<你的密码>';
GRANT ALL PRIVILEGES ON nattunnel.* TO 'nattunnel'@'127.0.0.1';
GRANT ALL PRIVILEGES ON nattunnel.* TO 'nattunnel'@'localhost';
FLUSH PRIVILEGES;
```

### 其他

- **端口**: `python run.py --port 9000` 只在 CLI/systemd 里生效; nginx 两处 `proxy_pass`
  必须指向同一端口, 防火墙不放通该端口。
- **启动卡在重试 db init**: 上面 MySQL 问题的表现(每 2s 重试 6 次后退出); 或 Redis/DB 容器未起。
- **首启没出现管理员创建提示**: stdin 不是终端(如 systemd 直接拉起) — 属正常降级为
  `.env INITIAL_ADMIN_PASSWORD`/随机+日志打印; 想交互创建就手动在终端先跑一次。
