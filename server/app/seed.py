"""启动初始化: 建表 + 初始管理员 + 种子数据(幂等)。

- 管理员: users 表中尚无任何 admin 时, 于首次启动创建:
    1) 交互式终端(stdin 是 tty): 控制台引导输入用户名/密码(密码隐藏输入);
    2) 非交互(systemd/docker 等无终端场景): 取 .env 的 INITIAL_ADMIN_PASSWORD,
       未配置则生成随机强口令并在启动日志中**一次性**打印(请登录后立即修改)。
- 示例隧道 XgMacp2G (tcp, 本机端口 3389) — 仅演示用, 可删
- RSA 密钥对(登录握手用)
"""
import logging
import re
import secrets
import string
import sys

try:
    import getpass
except ImportError:  # pragma: no cover - getpass 几乎总是可用
    getpass = None

from .config import INITIAL_ADMIN_PASSWORD, INITIAL_ADMIN_USERNAME
from .database import Base, SessionLocal, engine
from .models import Tunnel, User
from .security import ensure_rsa_keys, hash_password

log = logging.getLogger("nattunnel.seed")

DEMO_TUNNEL_ID = "XgMacp2G"
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")


def _prompt_admin_credentials() -> tuple:
    """首启交互式创建管理员(终端引导), 返回 (username, password)。"""
    print()
    print("=" * 64)
    print("首次启动: 检测到尚无管理员账号, 请在控制台创建")
    print("=" * 64)
    while True:
        try:
            username = input("管理员用户名 [默认 admin]: ").strip() or "admin"
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("已取消管理员创建(无输入)")
        if not _USERNAME_RE.match(username):
            print("用户名不合法(2-32 位字母/数字/下划线), 请重新输入")
            continue

        def _ask(prompt: str) -> str:
            if getpass is not None:
                try:
                    return getpass.getpass(prompt)
                except (EOFError, KeyboardInterrupt):
                    raise
                except Exception:
                    pass  # 无终端回显环境, 退回明文 input()
            return input(prompt)

        try:
            password = _ask("管理员密码(至少 8 位, 隐藏输入): ")
            if len(password) < 8:
                print("密码至少 8 位, 请重新输入")
                continue
            confirm = _ask("请再次输入管理员密码: ")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("已取消管理员创建(无输入)")
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


def _migrate_local_target_host() -> None:
    """存量库幂等迁移: tunnels 表补 local_target_host 列(新建库由 create_all 处理)。"""
    from sqlalchemy import text

    with engine.begin() as conn:
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


def init_db(retries: int = 6, delay: float = 2.0) -> None:
    import time

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            Base.metadata.create_all(engine)
            _migrate_local_target_host()
            _seed()
            log.info("database initialized (attempt %d)", attempt)
            return
        except Exception as exc:
            last_err = exc
            if attempt < retries:
                log.warning(
                    "db init failed (attempt %d/%d): %s — retry in %.0fs",
                    attempt, retries, exc, delay,
                )
                time.sleep(delay)
    raise RuntimeError(f"database initialization failed: {last_err}")


def _seed() -> None:
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
