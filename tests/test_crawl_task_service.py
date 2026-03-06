from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.exceptions import InvalidTaskTransitionError
from rednote_spider.models import Base, CrawlTask, TaskStatus
from rednote_spider.services.crawl_task_service import CrawlTaskService


@pytest.fixture()
def session_factory(tmp_path: Path):
    db_path = tmp_path / "test_rednote.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def test_create_task_defaults(session_factory):
    with session_factory() as session:
        service = CrawlTaskService(session)
        task = service.create_task("通勤痛点")

    with session_factory() as session:
        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.keywords == "通勤痛点"
        assert persisted.status == TaskStatus.pending
        assert persisted.platform == "xhs"
        assert persisted.note_count == 0


def test_valid_transitions(session_factory):
    with session_factory() as session:
        service = CrawlTaskService(session)
        task = service.create_task("租房")
        task = service.start_task(task.id)
        assert task.status == TaskStatus.running

        task = service.complete_task(task.id, note_count=12)
        assert task.status == TaskStatus.done
        assert task.note_count == 12


def test_failed_retry_transition(session_factory):
    with session_factory() as session:
        service = CrawlTaskService(session)
        task = service.create_task("早高峰")
        task = service.start_task(task.id)
        task = service.fail_task(task.id, "network timeout")
        assert task.status == TaskStatus.failed
        assert task.error_message == "network timeout"

        task = service.start_task(task.id)
        assert task.status == TaskStatus.running
        assert task.error_message is None


def test_fail_task_truncates_oversized_error_message(session_factory):
    with session_factory() as session:
        service = CrawlTaskService(session)
        task = service.create_task("登录失败")
        task = service.start_task(task.id)

        long_error = "x" * 2000
        task = service.fail_task(task.id, long_error)

        assert task.status == TaskStatus.failed
        assert task.error_message is not None
        assert len(task.error_message) <= 1024
        assert task.error_message.endswith("...(truncated)")


def test_invalid_transition_raises(session_factory):
    with session_factory() as session:
        service = CrawlTaskService(session)
        task = service.create_task("出门忘带")

        with pytest.raises(InvalidTaskTransitionError):
            service.complete_task(task.id, note_count=1)

        started = service.start_task(task.id)
        done = service.complete_task(started.id, note_count=1)

        with pytest.raises(InvalidTaskTransitionError):
            service.start_task(done.id)
