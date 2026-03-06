"""Database setup and session helpers."""

from __future__ import annotations

from contextlib import contextmanager
import json

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings


def make_engine(database_url: str | None = None) -> Engine:
    return create_engine(
        database_url or settings.database_url,
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
