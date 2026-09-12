"""SQLAlchemy 引擎与会话。

SessionLocal 是一个稳定的代理对象: 各模块 import 到的名字始终指向同一实例,
`reconfigure()` 可就地重建引擎(首启交互向导改写 .env 后热切换数据库连接)。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL


class Base(DeclarativeBase):
    pass


_state = {"engine": None, "make_session": None}


def reconfigure(url: str) -> None:
    """(重)建引擎; 原引擎 dispose。"""
    if _state["engine"] is not None:
        _state["engine"].dispose()
    _state["engine"] = create_engine(
        url, pool_pre_ping=True, pool_size=5, max_overflow=10
    )
    _state["make_session"] = sessionmaker(
        bind=_state["engine"], autoflush=False, autocommit=False
    )


reconfigure(DATABASE_URL)


class _SessionLocalProxy:
    def __call__(self):
        return _state["make_session"]()


SessionLocal = _SessionLocalProxy()


def get_engine():
    return _state["engine"]


def get_db():
    """FastAPI 依赖: 每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
