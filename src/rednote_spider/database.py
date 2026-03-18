"""Database setup and session helpers."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./rednote.db"


def _default_database_url() -> str:
    raw = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    return raw or DEFAULT_DATABASE_URL


try:
    from .config import settings as _settings
except Exception:  # noqa: BLE001
    _settings = None


def make_engine(database_url: str | None = None) -> Engine:
    resolved_database_url = database_url or getattr(_settings, "database_url", None) or _default_database_url()
    return create_engine(
        resolved_database_url,
        future=True,
        json_serializer=lambda payload: json.dumps(payload, ensure_ascii=False),
        json_deserializer=json.loads,
    )


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)

@contextmanager
def session_scope() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
