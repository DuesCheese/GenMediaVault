from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    return create_engine(settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def session_factory():
    return sessionmaker(engine(), expire_on_commit=False)


def get_db():
    with session_factory()() as db:
        yield db
