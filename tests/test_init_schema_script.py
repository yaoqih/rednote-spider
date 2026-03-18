from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from rednote_spider.models import CORE_TABLES


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "init_schema.py"


def test_init_schema_script_detects_existing_schema_drift(tmp_path: Path):
    db_path = tmp_path / "drift.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        for table_name in CORE_TABLES:
            conn.execute(text(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)"))
    engine.dispose()

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--database-url",
            f"sqlite:///{db_path}",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert result.returncode != 0
    assert "schema_drift_detected" in result.stderr


def test_init_schema_script_backfills_scheduler_note_limit_column(tmp_path: Path):
    db_path = tmp_path / "scheduler_note_limit.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE scheduler_runtime_config (
                    id INTEGER PRIMARY KEY,
                    mode VARCHAR(32) NOT NULL UNIQUE,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    loop_interval_seconds INTEGER NOT NULL DEFAULT 60,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO scheduler_runtime_config (id, mode, enabled, loop_interval_seconds)
                VALUES (1, 'discover', 1, 120)
                """
            )
        )
    engine.dispose()

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--database-url",
            f"sqlite:///{db_path}",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert result.returncode == 0, result.stderr

    migrated_engine = create_engine(f"sqlite:///{db_path}", future=True)
    inspector = inspect(migrated_engine)
    columns = {col["name"] for col in inspector.get_columns("scheduler_runtime_config")}
    assert "note_limit" in columns


def test_init_schema_script_creates_login_runtime_tables(tmp_path: Path):
    db_path = tmp_path / "login_schema.db"

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--database-url",
            f"sqlite:///{db_path}",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )

    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "login_runtime_state" in tables
    assert "login_event" in tables
