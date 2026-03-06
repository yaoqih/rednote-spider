from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from rednote_spider.models import Base, DiscoverWatchKeyword, RawNote


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "run_discover_cycle.py"


def _prepare_db(db_path: Path) -> None:
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
        session.commit()


def test_run_discover_cycle_script_with_command_collector(tmp_path: Path):
    db_path = tmp_path / "discover_command.db"
    _prepare_db(db_path)

    command_script = tmp_path / "emit_discover_notes.py"
    command_script.write_text(
        "\n".join(
            [
                "import json, sys",
                "keyword = sys.argv[1]",
                "max_notes = int(sys.argv[2])",
                "notes = [",
                "  {'note_id': f'discover-{i+1}', 'title': keyword, 'content': '排队太慢很麻烦', 'likes': 9, 'comments_cnt': 2}",
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
            "--cycles",
            "1",
            "--interval-seconds",
            "0",
            "--keyword-limit",
            "5",
            "--note-limit",
            "2",
            "--command-template",
            f"{sys.executable} {command_script} \"{{keywords}}\" {{max_notes}}",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DATABASE_URL": f"sqlite:///{db_path}",
            "PYTHONPATH": "src",
            "OPPORTUNITY_LLM_PROVIDER": "mock",
        },
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["keywords_total"] == 1
    assert payload["succeeded"] == 1
    assert payload["failed"] == 0

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with Session(engine) as session:
        note_count = session.execute(select(RawNote)).scalars().all()
    assert len(note_count) == 2


def test_run_discover_cycle_script_requires_command_template(tmp_path: Path):
    db_path = tmp_path / "discover_command_missing_template.db"
    _prepare_db(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--cycles",
            "1",
            "--interval-seconds",
            "0",
            "--keyword-limit",
            "5",
            "--note-limit",
            "2",
            "--command-template",
            "",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{db_path}", "PYTHONPATH": "src"},
    )
    assert result.returncode != 0
    assert "--command-template is required" in result.stderr


def test_run_discover_cycle_script_rejects_non_positive_cycles(tmp_path: Path):
    db_path = tmp_path / "discover_invalid_cycles.db"
    _prepare_db(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--cycles",
            "0",
            "--command-template",
            "python -c \"print(1)\"",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{db_path}", "PYTHONPATH": "src"},
    )
    assert result.returncode != 0
    assert "--cycles must be >= 1" in result.stderr
