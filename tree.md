# nattunnel 项目结构

```
nattunnel/
├── progress.md                  # 跨会话进度台账(状态总览/踩坑备忘)
├── tree.md                      # 本文件: 项目结构
├── README.md                    # 总览与快速开始
├── .gitignore                   # venv/db/exe 产物/临时文件
├── records/                     # 每次会话记录 yyyy-MM-dd-hh-mm-ss.md
│   ├── 2026-09-11-00-33-34.md   # 会话 1: 全量代码 + 自测 + EXE 验收
│   └── 2026-09-11-11-19-31.md   # 会话 2: 移除硬编码凭据, 管理员启动时初始化
│
├── server/                      # FastAPI 后端
│   ├── requirements.txt         # fastapi/uvicorn/sqlalchemy/pymysql/cryptography/PyJWT/redis/dotenv
│   ├── .env.example             # DATABASE_URL / REDIS_URL / JWT_SECRET ...
│   ├── run.py                   # 入口: python run.py (server/ 目录下, 单 worker)
│   ├── docker-compose.yml       # mysql 8 + redis 7 一键依赖
│   ├── nginx/
│   │   └── nattunnel.conf       # /tunnel/*(WS Upgrade + 纯 HTTP 直转) 与 /api/* 反代配置
│   └── app/
│       ├── __init__.py
│       ├── main.py              # FastAPI 应用 + WS /tunnel/{tid} 中继主循环(T_FIN 转发)
│       ├── config.py            # .env 配置读取
│       ├── database.py          # SQLAlchemy engine/session/Base
│       ├── models.py            # User / Tunnel / AppSetting
│       ├── schemas.py           # Pydantic 请求/响应
│       ├── security.py          # pbkdf2 密码哈希 / JWT / RSA 密钥对管理
│       ├── deps.py              # Bearer 鉴权依赖(get_current_user/require_admin/get_current_payload)
│       ├── http_bridge.py       # /tunnel/{tid} 纯 HTTP 直转中间件(浏览器开短链, tcp 隧道)
│       ├── relay.py             # Hub/Room 房间表, 帧编解码, Redis 在线状态/限流
│       └── seed.py              # 建表 + 首启初始化管理员(.env/随机口令) + 隧道 XgMacp2G + RSA 键
│       └── routers/
│           ├── __init__.py
│           ├── auth.py          # public-key / login(RSA→JWT) / me
│           ├── users.py         # 管理员用户 CRUD(级联删隧道)
│           └── tunnels.py       # 隧道 CRUD + lan_online/pub_count 状态
│
├── client/                      # Python 客户端(可打包 exe)
│   ├── requirements.txt         # websockets
│   ├── nattunnel_client.py      # 主程序: RSA登录→JWT→拉配置→TCP/UDP转发+令牌桶限流+重连
│   ├── config.example.json      # server/tunnel_id/username/password/local_target_host
│   ├── build.bat                # PyInstaller --onefile → dist\nattunnel-client.exe
│   └── dist/                    # (构建产物) nattunnel-client.exe + config.json 同目录运行
│
└── tools/                       # 测试工具(公网侧/自测/巡检)
    ├── ws_tcp_test.py           # 公网侧: stdin/stdout ↔ 隧道(TCP)
    ├── ws_udp_test.py           # 公网侧: 行交互或 --send 一次性(UDP)
    ├── api_check.py             # 管理员/用户 API 巡检(建用户/越权/CRUD/清理)
    └── selftest.py              # 端到端自测(进程内跑真实客户端代码, TCP+UDP echo)
```

运行视图:

- 服务器: `server/` 下 `python run.py` → :8000; nginx 反代 `www.xxx.com/tunnel/*`、`/api/*`
- 内网电脑: `client\dist\nattunnel-client.exe` + `config.json`(同目录)
- 公网测试: `tools\ws_tcp_test.py wss://www.xxx.com/tunnel/XgMacp2G`
