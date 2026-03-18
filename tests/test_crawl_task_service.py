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



def test_list_tasks_supports_status_platform_and_keyword_filters(session_factory):
    with session_factory() as session:
        service = CrawlTaskService(session)
        first = service.create_task("通勤收纳", platform="xhs")
        second = service.create_task("租房家电", platform="xhs")
        third = service.create_task("commute gadget", platform="douyin")
        service.start_task(second.id)
        service.fail_task(second.id, "boom")
        service.start_task(third.id)
        service.complete_task(third.id, note_count=3)

        filtered = service.list_tasks(statuses=[TaskStatus.failed], platform="xhs", keywords_query="租房")

        assert [row.id for row in filtered] == [second.id]


def test_update_task_allows_pending_and_failed_only(session_factory):
    with session_factory() as session:
        service = CrawlTaskService(session)
        pending = service.create_task("旧关键词", platform="xhs")
        updated = service.update_task(pending.id, keywords="新关键词", platform="douyin")
        assert updated.keywords == "新关键词"
        assert updated.platform == "douyin"

        failed = service.create_task("失败任务")
        service.start_task(failed.id)
        service.fail_task(failed.id, "network timeout")
        retried = service.update_task(failed.id, keywords="失败任务-修正", platform="xhs")
        assert retried.keywords == "失败任务-修正"

        running = service.create_task("运行中任务")
        service.start_task(running.id)
        with pytest.raises(InvalidTaskTransitionError):
            service.update_task(running.id, keywords="不允许")

        done = service.create_task("已完成")
        service.start_task(done.id)
        service.complete_task(done.id, note_count=1)
        with pytest.raises(InvalidTaskTransitionError):
            service.update_task(done.id, keywords="不允许")


def test_delete_task_allows_pending_and_failed_only(session_factory):
    with session_factory() as session:
        service = CrawlTaskService(session)
        pending = service.create_task("待删除")
        service.delete_task(pending.id)
        assert session.get(CrawlTask, pending.id) is None

        failed = service.create_task("失败待删除")
        service.start_task(failed.id)
        service.fail_task(failed.id, "err")
        service.delete_task(failed.id)
        assert session.get(CrawlTask, failed.id) is None

        done = service.create_task("完成只读")
        service.start_task(done.id)
        service.complete_task(done.id, note_count=1)
        with pytest.raises(InvalidTaskTransitionError):
            service.delete_task(done.id)
