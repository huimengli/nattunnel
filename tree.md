# nattunnel 项目结构

```
nattunnel/
├── progress.md                  # 跨会话进度台账(状态总览/踩坑备忘/Git 记录)
├── tree.md                      # 本文件: 项目结构(仅非 .gitignore 文件)
├── README.md                    # 总览与快速开始(协议 http/tcp/udp 语义表)
├── todo.md                      # 待办清单
├── need_user.md                 # 需用户确认的阻塞问题(Q/A)
├── favicon.ico                  # 仓库图标; build.bat --icon 打进 exe
├── .gitignore                   # venv/db/.env/exe 产物/临时文件
├── records/                     # 需求与会话记录 yyyy-MM-dd-HH-mm-ss.md
│   ├── 2026-09-11-00-33-34.md … 2026-09-12-11-24-24.md   # 历史会话(骨架/凭据/部署/下载)
│   └── 2026-11-03-13-21-03.md   # 会话: 协议改造 http/tcp/udp + WebSocket 握手桥接
├── ai_buckup/                   # 归档(仅本次已归档的台账/问答)
│   ├── progress.md_2026-11-03-18-58-00.md
│   └── need_user.md_2026-11-03-18-58-00.md
├── tests/                       # 测试记录
│   ├── opinion.md               # 累积结论
│   └── 2026-11-03-13-21-03_cc/  # 本批次 20 轮: record_1..20.md + opinion.md
│
├── server/                      # FastAPI 后端(单 worker)
│   ├── requirements.txt         # fastapi/uvicorn[standard]/sqlalchemy/pymysql/cryptography/PyJWT/redis/python-dotenv
│   ├── .env.example             # DATABASE_URL / REDIS_URL / JWT_SECRET / HOST / PORT
│   ├── run.py                   # 入口: python run.py [--host/--port](server/ 目录下)
│   ├── docker-compose.yml       # mysql 8 + redis 7 一键依赖
│   ├── nginx/
│   │   ├── nattunnel.conf       # 独占域名: /tunnel/*(WS Upgrade + 纯 HTTP 直转) + /
│   │   └── nattunnel-embed.conf # 混部: 现有 vhost 加 /tunnel/ 与 /nattunnel-admin/
│   └── app/
│       ├── __init__.py
│       ├── main.py              # FastAPI 应用 + /tunnel/{tid} WS 中继(角色判定/data_types/子协议回填)
│       ├── config.py            # .env 配置读取
│       ├── database.py          # SQLAlchemy engine/session/Base + reconfigure()
│       ├── models.py            # User / Tunnel(proto http|tcp|udp) / AppSetting
│       ├── schemas.py           # Pydantic 请求/响应(proto 取值校验)
│       ├── security.py          # pbkdf2 密码哈希 / JWT / RSA 密钥对管理
│       ├── deps.py              # Bearer 鉴权依赖
│       ├── http_bridge.py       # 隧道桥: 纯 HTTP 直转(仅 http 隧道) + WS 握手桥接(WSCodec) + 预拒绝
│       ├── relay.py             # Hub/Room 房间表, 帧编解码, RELAY_SUBPROTOCOL, data_type, Redis 在线/限流
│       ├── seed.py              # 建表 + 首启管理员 + 一次性 tcp→http 迁移 + 演示隧道 + RSA 键
│       ├── routers/             # auth(login/RSA) / users / tunnels(CRUD+token) / download
│       └── static/
│           ├── index.html       # 单文件管理页(协议三项下拉 + 语义说明 + 令牌/exe 下载)
│           └── nattunnel-client.exe  # exe 唯一副本(静态直链, build.bat 自动同步)
│
├── client/                      # Python 客户端(可打包 exe)
│   ├── requirements.txt         # websockets + cryptography
│   ├── nattunnel_client.py      # 绑定令牌→拉隧道配置→TCP(字节流)/UDP(数据报)转发 + 令牌桶 + T_CONFIG 热更新 + 重连
│   ├── build_config.py          # (build.bat 生成, gitignore) 构建时写死的服务器地址
│   ├── build.bat                # [-s <server>] 写死地址 + PyInstaller --onefile [--icon ..\favicon.ico]
│   └── nattunnel-client.spec    # PyInstaller 规格
│
├── docs/deploy-keliit-top.md    # keliit.top 子路径混部完整步骤
└── tools/                       # 公网侧/端到端测试工具(需 pip install websockets)
    ├── ws_tcp_test.py           # 子协议 nattunnel-relay 字节流裸流(HELLO/echo)
    ├── ws_udp_test.py           # 子协议 nattunnel-relay 数据报(HELLO/echo)
    ├── ws_handshake_test.py     # 浏览器式 WS 握手桥接(握手结果/子协议/echo)
    ├── selftest.py              # 端到端: A 字节流+热更新 / B UDP / C 绑定令牌 / D 纯 HTTP 直转 / E WS 握手桥接
    └── api_check.py             # API 全链路(含 proto 取值与改协议)
```
