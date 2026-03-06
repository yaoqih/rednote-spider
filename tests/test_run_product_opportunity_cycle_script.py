from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from rednote_spider.models import (
    Base,
    CrawlTask,
    CrawlTaskNote,
    ProductOpportunity,
    RawComment,
    RawNote,
    TaskStatus,
)


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "run_product_opportunity_cycle.py"


def _prepare_db(db_path: Path) -> int:
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        task = CrawlTask(keywords="通勤", platform="xhs", status=TaskStatus.done)
        session.add(task)
        session.flush()
        task.note_count = 1
        session.add(
            RawNote(
                task_id=task.id,
                note_id="script-note-1",
                title="通勤焦虑",
                content="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
                likes=8,
                comments_cnt=1,
            )
        )
        session.add(
            RawComment(
                note_id="script-note-1",
                comment_id="script-note-1-c1",
                content="这个问题每周都在发生，求解决方案",
                likes=2,
            )
        )
        session.add(CrawlTaskNote(task_id=task.id, note_id="script-note-1"))
        session.commit()
        return int(task.id)


def test_run_product_opportunity_cycle_script_success(tmp_path: Path):
    db_path = tmp_path / "product_cycle.db"
    task_id = _prepare_db(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--database-url",
            f"sqlite:///{db_path}",
            "--task-id",
            str(task_id),
            "--prescreen-threshold",
            "2.0",
            "--match-threshold",
            "0.3",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src", "OPPORTUNITY_LLM_PROVIDER": "mock"},
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["tasks_scanned"] == 1
    assert payload["notes_scanned"] == 1
    assert payload["created"] == 1
    assert payload["matched"] == 0
    assert payload["ignored"] == 0

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with Session(engine) as session:
        opportunities = session.execute(select(ProductOpportunity)).scalars().all()
    assert len(opportunities) == 1


def test_run_product_opportunity_cycle_script_requires_schema(tmp_path: Path):
    db_path = tmp_path / "missing_schema.db"

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--database-url",
            f"sqlite:///{db_path}",
            "--limit-tasks",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src", "OPPORTUNITY_LLM_PROVIDER": "mock"},
    )
    assert result.returncode != 0
    assert "database schema is not initialized" in result.stderr


def test_run_product_opportunity_cycle_script_rejects_invalid_backoff(tmp_path: Path):
    db_path = tmp_path / "product_cycle_invalid_backoff.db"
    _prepare_db(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--database-url",
            f"sqlite:///{db_path}",
            "--limit-tasks",
            "1",
            "--retry-backoff-base-minutes",
            "0",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src", "OPPORTUNITY_LLM_PROVIDER": "mock"},
    )
    assert result.returncode != 0
    assert "--retry-backoff-base-minutes must be >= 1" in result.stderr
