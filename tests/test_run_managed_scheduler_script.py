from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from rednote_spider.models import Base, DiscoverWatchKeyword, ProductOpportunity, RawNote, SchedulerRuntimeConfig


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "run_managed_scheduler.py"


def _prepare_db(db_path: Path, *, enabled: bool, interval: int = 120, note_limit: int = 9) -> None:
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            DiscoverWatchKeyword(
                keyword="宠物 美容",
                platform="xhs",
                enabled=True,
                poll_interval_minutes=60,
            )
        )
        session.add(
            SchedulerRuntimeConfig(
                mode="discover",
                enabled=enabled,
                loop_interval_seconds=interval,
                note_limit=note_limit,
            )
        )
        session.commit()


def test_run_managed_scheduler_script_skips_disabled_discover(tmp_path: Path):
    db_path = tmp_path / "managed_scheduler_disabled.db"
    _prepare_db(db_path, enabled=False)

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--mode",
            "discover",
            "--once",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DATABASE_URL": f"sqlite:///{db_path}",
            "PYTHONPATH": "src",
        },
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["mode"] == "discover"
    assert payload["enabled"] is False
    assert payload["status"] == "skipped_disabled"
    assert payload["loop_interval_seconds"] == 120
    assert payload["note_limit"] == 9


def test_run_managed_scheduler_script_runs_enabled_discover(tmp_path: Path):
    db_path = tmp_path / "managed_scheduler_enabled.db"
    _prepare_db(db_path, enabled=True, interval=45)

    command_script = tmp_path / "emit_discover_notes.py"
    command_script.write_text(
        "\n".join(
            [
                "import json, sys",
                "keyword = sys.argv[1]",
                "max_notes = int(sys.argv[2])",
                "notes = [",
                "  {'note_id': f'managed-{i+1}', 'title': keyword, 'content': '排队太慢很麻烦', 'likes': 9, 'comments_cnt': 2}",
                "  for i in range(max_notes)",
                "]",
                "print(json.dumps({'notes': notes}, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--mode",
            "discover",
            "--once",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DATABASE_URL": f"sqlite:///{db_path}",
            "PYTHONPATH": "src",
            "SCHED_DISCOVER_COMMAND_TEMPLATE": f'{sys.executable} {command_script} "{{keywords}}" {{max_notes}}',
            "OPPORTUNITY_LLM_PROVIDER": "mock",
        },
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["mode"] == "discover"
    assert payload["enabled"] is True
    assert payload["status"] == "completed"
    assert payload["loop_interval_seconds"] == 45
    assert payload["note_limit"] == 9
    assert payload["summary"]["discover"]["succeeded"] == 1
    assert payload["summary"]["discover"]["failed"] == 0
    assert payload["summary"]["opportunity"]["tasks_scanned"] == 1
    assert payload["summary"]["opportunity"]["notes_scanned"] == 9
    assert payload["summary"]["opportunity"]["created"] == 9

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with Session(engine) as session:
        notes = session.execute(select(RawNote).order_by(RawNote.note_id.asc())).scalars().all()
        opportunities = session.execute(select(ProductOpportunity).order_by(ProductOpportunity.id.asc())).scalars().all()
    assert len(notes) == 9
    assert len(opportunities) == 9
    assert {row.note_id for row in notes} == {f"managed-{i}" for i in range(1, 10)}


def test_run_managed_scheduler_script_rejects_standalone_opportunity_mode(tmp_path: Path):
    db_path = tmp_path / "managed_scheduler_invalid_mode.db"
    _prepare_db(db_path, enabled=True)

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--mode",
            "opportunity",
            "--once",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DATABASE_URL": f"sqlite:///{db_path}",
            "PYTHONPATH": "src",
        },
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
