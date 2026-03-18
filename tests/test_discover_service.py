from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.models import (
    Base,
    CrawlTask,
    CrawlTaskNote,
    DiscoverWatchKeyword,
    RawComment,
    RawNote,
    TaskStatus,
)
from rednote_spider.services.discover_service import DiscoverService


class StaticCollector:
    def __init__(
        self,
        notes: list[dict[str, Any]],
        comments_by_note: dict[str, list[dict[str, Any]]] | None = None,
    ):
        self.notes = notes
        self.comments_by_note = comments_by_note or {}

    def collect(
        self,
        keyword: str,  # noqa: ARG002
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        selected = self.notes[:limit]
        selected_note_ids = {str(row.get("note_id") or "") for row in selected}
        selected_comments = {
            note_id: rows for note_id, rows in self.comments_by_note.items() if note_id in selected_note_ids
        }
        return selected, selected_comments


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "test_discover.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def test_discover_service_run_once_creates_task_and_ingests(tmp_path: Path):
    sf = _session_factory(tmp_path)
    collector = StaticCollector(
        notes=[
            {
                "note_id": "d-note-1",
                "title": "high signal",
                "content": "排队太慢很麻烦",
                "likes": 12,
                "comments_cnt": 3,
            },
            {
                "note_id": "d-note-2",
                "title": "high signal 2",
                "content": "总是来不及，太麻烦了",
                "likes": 10,
                "comments_cnt": 2,
            },
        ],
        comments_by_note={
            "d-note-1": [
                {"comment_id": "d-note-1-c1", "content": "评论1"},
                {"comment_id": "d-note-1-c2", "content": "评论2"},
            ],
            "d-note-2": [
                {"comment_id": "d-note-2-c1", "content": "评论3"},
            ],
        },
    )
    service = DiscoverService(sf, collector)
    service.upsert_keyword(keyword="pet grooming", poll_interval_minutes=60)

    summary = service.run_once(keyword_limit=10, note_limit=10)
    assert summary.keywords_total == 1
    assert summary.keywords_processed == 1
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert summary.tasks_done == 1
    assert summary.tasks_failed == 0

    with sf() as session:
        task = session.execute(select(CrawlTask).where(CrawlTask.keywords == "pet grooming")).scalar_one()
        assert task.status == TaskStatus.done
        notes = session.execute(
            select(RawNote)
            .join(CrawlTaskNote, CrawlTaskNote.note_id == RawNote.note_id)
            .where(CrawlTaskNote.task_id == task.id)
        ).scalars().all()
        comments = session.execute(select(RawComment).order_by(RawComment.comment_id)).scalars().all()
        assert len(notes) == 2
        assert [row.comment_id for row in comments] == ["d-note-1-c1", "d-note-1-c2", "d-note-2-c1"]

    second = service.run_once(keyword_limit=10, note_limit=10)
    assert second.keywords_total == 0


def test_discover_service_requires_collector_for_run(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = DiscoverService(sf, collector=None)
    service.upsert_keyword(keyword="k1")

    try:
        service.run_once(keyword_limit=1, note_limit=1)
        raise AssertionError("run_once should fail without collector")
    except ValueError as exc:
        assert "collector is required" in str(exc)


def test_discover_service_due_scan_not_starved_by_non_due_rows(tmp_path: Path):
    sf = _session_factory(tmp_path)
    base_time = datetime.now() - timedelta(minutes=30)

    with sf() as session:
        for i in range(40):
            session.add(
                DiscoverWatchKeyword(
                    keyword=f"not-due-{i}",
                    platform="xhs",
                    enabled=True,
                    poll_interval_minutes=10_000,
                    last_polled_at=base_time,
                )
            )
        for i in range(20):
            session.add(
                DiscoverWatchKeyword(
                    keyword=f"due-{i}",
                    platform="xhs",
                    enabled=True,
                    poll_interval_minutes=1,
                    last_polled_at=base_time,
                )
            )
        session.commit()

    service = DiscoverService(sf, StaticCollector([]))
    due_ids = service._list_due_keyword_ids(limit=10)
    assert len(due_ids) == 10

    with sf() as session:
        due_keywords = [session.get(DiscoverWatchKeyword, row_id).keyword for row_id in due_ids]
    assert all(keyword.startswith("due-") for keyword in due_keywords)


def test_discover_service_runs_crawl_stage_and_updates_polling_cursor(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = DiscoverService(
        sf,
        StaticCollector(
            [
                {"note_id": "f-note-1", "title": "x", "content": "排队很麻烦"},
            ]
        ),
    )
    service.upsert_keyword(keyword="failing keyword", poll_interval_minutes=60)

    summary = service.run_once(keyword_limit=10, note_limit=10)
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert summary.tasks_done == 1
    assert summary.tasks_failed == 0

    with sf() as session:
        task = session.execute(select(CrawlTask).where(CrawlTask.keywords == "failing keyword")).scalar_one()
        keyword = session.execute(
            select(DiscoverWatchKeyword).where(DiscoverWatchKeyword.keyword == "failing keyword")
        ).scalar_one()
        assert task.status == TaskStatus.done
        assert task.error_message is None
        assert keyword.last_polled_at is not None



def test_discover_service_delete_keyword_removes_watch_row(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = DiscoverService(sf, collector=None)
    created = service.upsert_keyword(keyword="delete me", poll_interval_minutes=30)

    service.delete_keyword(created.id)

    with sf() as session:
        row = session.get(DiscoverWatchKeyword, created.id)
        assert row is None



def test_discover_service_update_keyword_supports_keyword_text_change(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = DiscoverService(sf, collector=None)
    created = service.upsert_keyword(keyword="old keyword", poll_interval_minutes=30)

    updated = service.update_keyword(
        created.id,
        keyword="new keyword",
        platform="douyin",
        poll_interval_minutes=45,
        enabled=False,
    )

    assert updated.id == created.id
    assert updated.keyword == "new keyword"
    assert updated.platform == "douyin"
    assert updated.poll_interval_minutes == 45
    assert updated.enabled is False
