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
# 把整个仓库(或至少 server/ 与 client/dist/)放到 /opt/nattunnel/ 下:
#   /opt/nattunnel/server/          后端
#   /opt/nattunnel/client/dist/nattunnel-client.exe   供管理端"下载 exe"按钮
```

> 不要上传 `server/.env`、`server/local.db`（本地测试数据，gitignore 里已排除）。

## 2. Python 环境 + 依赖

```bash
cd /opt/nattunnel/server
python3 -m venv .venv                    # 需要 Python >= 3.10
.venv/bin/pip install -r requirements.txt   # 大陆服务器可先配 pip 镜像源
```

## 3. 配置 .env（首次启动）

```bash
cd /opt/nattunnel/server
cat > .env <<EOF
# 首启用 sqlite(免装 MySQL); 日后想切 MySQL 见第 8 步
DATABASE_URL=sqlite:///./nattunnel.db
REDIS_URL=redis://127.0.0.1:6379/0      # 没装 Redis 可留空, 服务自动降级(内存兜底)
JWT_SECRET=$(openssl rand -hex 32)
INITIAL_ADMIN_USERNAME=admin
# INITIAL_ADMIN_PASSWORD 留空 => 首启生成随机密码并打印到启动日志(推荐, 登完立即改密)
EOF
```

## 4. systemd 服务（单 worker！中继状态在进程内存）

`/etc/systemd/system/nattunnel.service`：

```ini
[Unit]
Description=nattunnel server
After=network.target

[Service]
WorkingDirectory=/opt/nattunnel/server
ExecStart=/opt/nattunnel/server/.venv/bin/python run.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now nattunnel
journalctl -u nattunnel -f     # 看到 "Application startup complete" 即就绪;
                                # 首次启动日志会打印初始管理员随机密码
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

1. 浏览器开 `https://www.keliit.top/nattunnel-admin/` → admin + 日志里的密码登录
2. 建隧道（本地目标主机填局域网内机器的 IP/域名，如 llama.cpp 所在主机的地址）
3. "签发客户端令牌" 得到绑定令牌
4. 局域网 PC 运行 `nattunnel-client.exe -a <令牌>` → 管理面板该隧道显示在线
5. 浏览器直开短链 `https://www.keliit.top/tunnel/<短链>` → 目标服务页面
6. 管理端"客户端下载"按钮可拿到 exe（服务端需提供 `/opt/nattunnel/client/dist/`）

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
