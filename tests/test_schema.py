from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from rednote_spider.models import Base, CORE_TABLES


def test_core_tables_declared() -> None:
    declared = set(Base.metadata.tables.keys())
    assert set(CORE_TABLES).issubset(declared)
    assert "login_runtime_state" in declared
    assert "login_event" in declared
    assert "login_qr_session" not in declared
    assert "login_phone_session" not in declared


def test_create_all_builds_core_tables_on_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "schema.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    created = set(inspector.get_table_names())
    assert set(CORE_TABLES).issubset(created)
