"""启动初始化: 环境向导 + 建表 + 初始管理员 + 种子数据(幂等)。

交互式终端首启(stdin 是 tty):
    1) server/.env 不存在, 或数据库不可用 → 向导选择 MySQL/sqlite、输入 MySQL 账号密码
       (连接失败时可再输管理员口令自动建库建用户), 写入 .env;
    2) users 表尚无 admin → 控制台引导输入用户名/密码(隐藏输入 + 二次确认)。
非交互场景(systemd/docker): 回退 .env 的 DATABASE_URL / INITIAL_ADMIN_PASSWORD,
密码未配置则随机生成并在启动日志中**一次性**打印。
"""
import logging
import os
import re
import secrets
import string
import sys
import time

try:
    import getpass
except ImportError:  # pragma: no cover - getpass 几乎总是可用
    getpass = None

from .config import BASE_DIR, INITIAL_ADMIN_PASSWORD, INITIAL_ADMIN_USERNAME
from .database import Base, get_engine, reconfigure
from .models import Tunnel, User
from .security import ensure_rsa_keys, hash_password

log = logging.getLogger("nattunnel.seed")

DEMO_TUNNEL_ID = "XgMacp2G"
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")
_ENV_PATH = BASE_DIR / ".env"


# ---------------------------------------------------------------- 交互输入辅助

def _ask(prompt: str, default: str = "") -> str:
    """读取一行(可见); EOF/Ctrl+C → 干净退出。"""
    try:
        raw = input(f"{prompt} [{default}]: " if default else f"{prompt}: ")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("已取消")
    return raw.strip() or default


def _ask_hidden(prompt: str) -> str:
    """隐藏读取一行; 无终端回显环境退回明文 input。"""
    if getpass is not None:
        try:
            return getpass.getpass(f"{prompt}: ")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("已取消")
        except Exception:
            pass
    return _ask(prompt)


def _ask_choice(prompt: str, default: int, choices: tuple) -> int:
    while True:
        raw = _ask(prompt, str(default))
        if raw in choices:
            return int(raw)
        print("请输入选项中的数字")


# ---------------------------------------------------------------- 管理员创建

def _prompt_admin_credentials() -> tuple:
    """首启交互式创建管理员(终端引导), 返回 (username, password)。"""
    print()
    print("=" * 64)
    print("检测到尚无管理员账号, 请在控制台创建")
    print("=" * 64)
    while True:
        username = _ask("管理员用户名", "admin")
        if not _USERNAME_RE.match(username):
            print("用户名不合法(2-32 位字母/数字/下划线), 请重新输入")
            continue
        password = _ask_hidden("管理员密码(至少 8 位, 隐藏输入)")
        if len(password) < 8:
            print("密码至少 8 位, 请重新输入")
            continue
        confirm = _ask_hidden("请再次输入管理员密码")
        if password != confirm:
            print("两次输入的密码不一致, 请重新输入")
            continue
        return username, password


def _bootstrap_admin(db) -> User:
    """无 admin 时创建。交互终端 => 控制台引导; 否则 .env / 随机+一次性打印。"""
    if sys.stdin.isatty():
        username, password = _prompt_admin_credentials()
        source = "console interactive"
    else:
        username = (INITIAL_ADMIN_USERNAME or "admin").strip()[:32]
        if not _USERNAME_RE.match(username):
            log.warning("INITIAL_ADMIN_USERNAME 不合法(%r), 回退为 'admin'", username)
            username = "admin"
        password = INITIAL_ADMIN_PASSWORD
        if not password:
            source = "random (printed once below)"
            alphabet = string.ascii_letters + string.digits
            password = "".join(secrets.choice(alphabet) for _ in range(16))
            log.warning("=" * 72)
            log.warning("首次启动(非交互): 已初始化管理员 '%s', 临时密码(仅此一次打印): %s", username, password)
            log.warning("请立即登录并调用 POST /api/password 修改密码!")
            log.warning("=" * 72)
        else:
            source = ".env INITIAL_ADMIN_PASSWORD"

    admin = User(username=username, password_hash=hash_password(password), role="admin")
    db.add(admin)
    log.info("initial admin '%s' created (%s)", username, source)
    return admin


# ---------------------------------------------------------------- 环境向导

def _test_db(url: str) -> None:
    """试连一次; 失败抛异常。"""
    from sqlalchemy import create_engine, text

    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        eng.dispose()


def _ensure_mysql_schema(host, port, dbname, user, password, root_password) -> None:
    """以 MySQL 管理员身份自动建库 + 建 'user'@127.0.0.1/'localhost' 并授权。"""
    from sqlalchemy import create_engine, text
    from urllib.parse import quote_plus

    eng = create_engine(
        f"mysql+pymysql://root:{quote_plus(root_password)}@{host}:{port}/?charset=utf8mb4"
    )
    try:
        with eng.begin() as conn:
            conn.execute(text(
                f"CREATE DATABASE IF NOT EXISTS `{dbname}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            ))
            for h in ("127.0.0.1", "localhost"):
                conn.execute(text(
                    f"CREATE USER IF NOT EXISTS '{user}'@'{h}' IDENTIFIED BY '{password}'"
                ))
                conn.execute(text(
                    f"GRANT ALL PRIVILEGES ON `{dbname}`.* TO '{user}'@'{h}'"
                ))
    finally:
        eng.dispose()


def _generate_env_file(database_url: str, redis_url: str) -> None:
    jwt_secret = os.environ.get("JWT_SECRET", "").strip() or secrets.token_hex(32)
    host = os.environ.get("HOST", "").strip() or "127.0.0.1"
    port = os.environ.get("PORT", "").strip() or "8000"
    lines = [
        "# 由首启交互向导生成; 可手工修改(改动需重启服务生效)",
        "",
        "# 数据库连接串",
        f"DATABASE_URL={database_url}",
        "",
        "# Redis 连接串(没有 Redis 可留空, 自动降级为内存)",
        f"REDIS_URL={redis_url}",
        "",
        "# JWT 密钥(留空 => 首启自动生成并持久化到 app_settings 表)",
        f"JWT_SECRET={jwt_secret}",
        "JWT_EXPIRE_DAYS=7",
        "",
        "# uvicorn 监听(nginx 反代走回环; 混部服务器勿改成 0.0.0.0)",
        f"HOST={host}",
        f"PORT={port}",
    ]
    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("generated .env -> %s", _ENV_PATH)


def _interactive_env_setup() -> None:
    """引导选库、收集账号密码, 写 .env 并热切换引擎; 成功后返回。"""
    from urllib.parse import quote_plus

    print()
    print("=" * 64)
    print("首启环境配置: 选择数据库并生成 server/.env")
    print("=" * 64)
    while True:
        choice = _ask_choice("[1] MySQL   [2] sqlite 文件(最快, 免安装)", 1, ("1", "2"))
        url = None
        if choice == 2:
            path = _ask("sqlite 文件路径", "./nattunnel.db")
            candidate = "sqlite:///" + path
            try:
                _test_db(candidate)
                url = candidate
            except Exception as exc:
                print(f"sqlite 不可用: {exc}")
        else:
            host = _ask("MySQL 主机", "127.0.0.1")
            port = _ask("MySQL 端口", "3306")
            dbname = _ask("数据库名", "nattunnel")
            user = _ask("账号", "nattunnel")
            password = _ask_hidden("密码")
            candidate = (
                f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
                f"@{host}:{port}/{quote_plus(dbname)}?charset=utf8mb4"
            )
            try:
                _test_db(candidate)
                url = candidate
            except Exception as exc:
                msg = str(exc)
                print(f"MySQL 连接失败: {msg}")
                if re.search(r"1045|Access denied|1049|Unknown database", msg):
                    root_pw = _ask_hidden(
                        "MySQL 管理员(root)密码 — 用于自动建库建账号, 直接回车跳过"
                    )
                    if root_pw:
                        try:
                            _ensure_mysql_schema(host, port, dbname, user, password, root_pw)
                            _test_db(candidate)
                            print("已自动创建数据库与账号")
                            url = candidate
                        except Exception as exc2:
                            print(f"自动创建失败: {exc2}")
        if url is None:
            continue

        redis_url = _ask("Redis 连接串(没有可留空)", "redis://127.0.0.1:6379/0")
        _generate_env_file(url, redis_url)
        reconfigure(url)
        print(".env 已生成, 数据库连接正常, 继续初始化...")
        return


# ---------------------------------------------------------------- 迁移与初始化

def _migrate_local_target_host() -> None:
    """存量库幂等迁移: tunnels 表补 local_target_host 列(新建库由 create_all 处理)。"""
    from sqlalchemy import text

    with get_engine().begin() as conn:
        try:
            conn.execute(
                text(
                    "ALTER TABLE tunnels ADD COLUMN local_target_host "
                    "VARCHAR(255) NOT NULL DEFAULT '127.0.0.1'"
                )
            )
            log.info("migration: tunnels.local_target_host column added")
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate" in msg or "1060" in msg:
                return  # 列已存在, 正常
            log.warning("migration check tunnels.local_target_host failed: %s", exc)


def _is_db_config_error(exc) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in (
        "access denied", "1045", "unknown database", "1049",
        "can't connect", "connection refused", "2003", "11001",
        "name or service not found",
    ))


def init_db(retries: int = 6, delay: float = 2.0) -> None:
    interactive = sys.stdin.isatty()
    if interactive and not _ENV_PATH.exists():
        _interactive_env_setup()

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            Base.metadata.create_all(get_engine())
            _migrate_local_target_host()
            _seed()
            log.info("database initialized (attempt %d)", attempt)
            return
        except Exception as exc:
            last_err = exc
            if interactive and _is_db_config_error(exc):
                print(f"\n数据库不可用: {exc}\n重新输入配置或切换 sqlite:")
                _interactive_env_setup()
            elif attempt < retries:
                if not interactive and not _ENV_PATH.exists():
                    log.warning("server/.env 缺失(非交互模式): 请手工创建, "
                                "或在终端里直接运行 python run.py 走引导式配置")
                log.warning("db init failed (attempt %d/%d): %s — retry in %.0fs",
                            attempt, retries, exc, delay)
                time.sleep(delay)
    raise RuntimeError(f"database initialization failed: {last_err}")


def _seed() -> None:
    from .database import SessionLocal

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.role == "admin").first()
        if admin is None:
            admin = _bootstrap_admin(db)
        # 已有管理员则保持现状(不覆盖密码)

        demo = db.query(Tunnel).filter_by(tunnel_id=DEMO_TUNNEL_ID).first()
        if demo is None:
            db.flush()
            db.add(
                Tunnel(
                    tunnel_id=DEMO_TUNNEL_ID,
                    owner_id=admin.id,
                    name="demo",
                    proto="tcp",
                    local_port=3389,
                    bandwidth_kbps=0,
                    enabled=True,
                )
            )
            log.info("seeded demo tunnel %s", DEMO_TUNNEL_ID)

        ensure_rsa_keys(db)
        # 确保 JWT 密钥存在(若 .env 未配置), 使用独立会话写入
        from .security import resolve_jwt_secret

        resolve_jwt_secret()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
