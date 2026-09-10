from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from widegold.settings.app import get_settings


@lru_cache(maxsize=1)
def engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def session_factory():
    return sessionmaker(bind=engine(), class_=Session, expire_on_commit=False)


@contextmanager
def db_session():
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
