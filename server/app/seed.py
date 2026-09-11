"""启动初始化: 建表 + 初始管理员 + 种子数据(幂等)。

- 管理员: users 表中尚无任何 admin 时, 于首次启动创建
    用户名/密码取自 .env (INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD);
    密码留空则生成随机强口令, 并在启动日志中**一次性**打印(请登录后立即修改)。
- 示例隧道 XgMacp2G (tcp, 本机端口 3389) — 仅演示用, 可删
- RSA 密钥对(登录握手用)
"""
import logging
import re
import secrets
import string

from .config import INITIAL_ADMIN_PASSWORD, INITIAL_ADMIN_USERNAME
from .database import Base, SessionLocal, engine
from .models import Tunnel, User
from .security import ensure_rsa_keys, hash_password

log = logging.getLogger("nattunnel.seed")

DEMO_TUNNEL_ID = "XgMacp2G"
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")


def _bootstrap_admin(db) -> User:
    """无 admin 时按 .env 初始化; 密码未配置则随机生成并一次性打印。"""
    username = (INITIAL_ADMIN_USERNAME or "admin").strip()[:32]
    if not _USERNAME_RE.match(username):
        log.warning("INITIAL_ADMIN_USERNAME 不合法(%r), 回退为 'admin'", username)
        username = "admin"

    password = INITIAL_ADMIN_PASSWORD
    generated = False
    if not password:
        generated = True
        alphabet = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(16))

    admin = User(username=username, password_hash=hash_password(password), role="admin")
    db.add(admin)
    if generated:
        log.warning("=" * 72)
        log.warning("首次启动: 已初始化管理员 '%s', 临时密码(仅此一次打印): %s", username, password)
        log.warning("请立即登录并调用 POST /api/password 修改密码!")
        log.warning("=" * 72)
    else:
        log.info("initial admin '%s' created from .env (INITIAL_ADMIN_PASSWORD set)", username)
    return admin


def init_db(retries: int = 6, delay: float = 2.0) -> None:
    import time

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            Base.metadata.create_all(engine)
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
