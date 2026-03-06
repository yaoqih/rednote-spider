from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.models import Base, CrawlTask, CrawlTaskNote, RawComment, RawNote, TaskStatus
from rednote_spider.services.raw_ingest_service import RawIngestService


@pytest.fixture()
def session_factory(tmp_path: Path):
    db_path = tmp_path / "test_ingest.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def _create_task(session: Session) -> CrawlTask:
    task = CrawlTask(keywords="test", platform="xhs", status=TaskStatus.pending)
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def test_ingest_notes_insert_then_update(session_factory):
    with session_factory() as session:
        task = _create_task(session)
        svc = RawIngestService(session)

        first = svc.ingest_notes(
            task.id,
            [
                {"note_id": "n1", "title": "first", "likes": 1},
                {"note_id": "n2", "title": "second", "likes": 2},
            ],
        )
        assert first.inserted == 2
        assert first.updated == 0

        second = svc.ingest_notes(
            task.id,
            [
                {"note_id": "n1", "title": "first-updated", "likes": 5},
                {"note_id": "n3", "title": "third", "likes": 1},
            ],
        )
        assert second.inserted == 1
        assert second.updated == 1

        rows = session.execute(select(RawNote).order_by(RawNote.note_id)).scalars().all()
        assert [r.note_id for r in rows] == ["n1", "n2", "n3"]
        assert rows[0].title == "first-updated"
        assert rows[0].likes == 5


def test_ingest_notes_requires_note_id(session_factory):
    with session_factory() as session:
        task = _create_task(session)
        svc = RawIngestService(session)

        with pytest.raises(ValueError, match="note_id"):
            svc.ingest_notes(task.id, [{"title": "missing-id"}])


def test_ingest_comments_requires_parent_note(session_factory):
    with session_factory() as session:
        svc = RawIngestService(session)
        with pytest.raises(ValueError):
            svc.ingest_comments("missing-note", [{"comment_id": "c1", "content": "x"}])


def test_ingest_comments_insert_then_update(session_factory):
    with session_factory() as session:
        task = _create_task(session)
        svc = RawIngestService(session)
        svc.ingest_notes(task.id, [{"note_id": "n100", "title": "base"}])

        first = svc.ingest_comments(
            "n100",
            [
                {"comment_id": "c1", "content": "hi", "likes": 1},
                {"comment_id": "c2", "content": "hello", "likes": 2},
            ],
        )
        assert first.inserted == 2
        assert first.updated == 0

        second = svc.ingest_comments(
            "n100",
            [
                {"comment_id": "c2", "content": "hello-2", "likes": 9},
                {"comment_id": "c3", "content": "new", "likes": 1},
            ],
        )
        assert second.inserted == 1
        assert second.updated == 1

        rows = session.execute(select(RawComment).order_by(RawComment.comment_id)).scalars().all()
        assert [r.comment_id for r in rows] == ["c1", "c2", "c3"]
        assert rows[1].content == "hello-2"
        assert rows[1].likes == 9


def test_ingest_notes_keeps_task_lineage_for_duplicate_note_id(session_factory):
    with session_factory() as session:
        task1 = _create_task(session)
        task2 = _create_task(session)
        svc = RawIngestService(session)

        svc.ingest_notes(
            task1.id,
            [{"note_id": "shared-note", "title": "first"}],
        )
        svc.ingest_notes(
            task2.id,
            [{"note_id": "shared-note", "title": "second"}],
        )

        note = session.execute(
            select(RawNote).where(RawNote.note_id == "shared-note")
        ).scalar_one()
        assert note.task_id == task1.id
        assert note.title == "second"

        links = session.execute(
            select(CrawlTaskNote).where(CrawlTaskNote.note_id == "shared-note").order_by(CrawlTaskNote.task_id.asc())
        ).scalars().all()
        assert [row.task_id for row in links] == [task1.id, task2.id]


def test_ingest_notes_accepts_epoch_created_at(session_factory):
    with session_factory() as session:
        task = _create_task(session)
        svc = RawIngestService(session)

        svc.ingest_notes(
            task.id,
            [{"note_id": "epoch-note", "created_at": 1_700_000_000}],
        )

        row = session.execute(select(RawNote).where(RawNote.note_id == "epoch-note")).scalar_one()
        assert row.created_at is not None


def test_ingest_comments_by_note_upserts_multiple_notes(session_factory):
    with session_factory() as session:
        task = _create_task(session)
        svc = RawIngestService(session)
        svc.ingest_notes(task.id, [{"note_id": "n1"}, {"note_id": "n2"}])

        summary = svc.ingest_comments_by_note(
            {
                "n1": [{"comment_id": "n1-c1", "content": "a"}],
                "n2": [{"comment_id": "n2-c1", "content": "b"}],
            }
        )
        assert summary.inserted == 2
        assert summary.updated == 0
