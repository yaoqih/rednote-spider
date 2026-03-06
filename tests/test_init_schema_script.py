from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

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
