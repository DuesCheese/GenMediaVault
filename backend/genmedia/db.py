from functools import lru_cache

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    return create_engine(settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def session_factory():
    return sessionmaker(engine(), expire_on_commit=False)


def get_db(request: Request):
    from .maintenance import update_in_progress
    read_only = request.method in ("GET", "HEAD", "OPTIONS") or request.url.path.startswith('/api/v1/search') or request.url.path == '/api/v1/translations/lookup'
    if not read_only and update_in_progress():
        raise HTTPException(503, "系统正在升级，暂时只读，请在更新完成后重试")
    with session_factory()() as db:
        if request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith("/api/v1/search") and not request.url.path.startswith("/api/v1/jobs/") and request.url.path != '/api/v1/translations/lookup':
            from .maintenance import BACKUP_LOCK
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock_shared(:key)"), {"key": BACKUP_LOCK}):
                raise HTTPException(503, "正在创建一致备份，暂时只读；请在备份完成后重试")
        yield db
