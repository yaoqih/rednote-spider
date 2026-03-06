"""Discover pipeline: watchlist -> collect -> ingest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..discover_collectors import KeywordCollector
from ..models import CrawlTaskNote, DiscoverWatchKeyword, TaskStatus
from ..observability import get_logger
from .crawl_task_service import CrawlTaskService
from .raw_ingest_service import RawIngestService

logger = get_logger(__name__)


@dataclass(slots=True)
class DiscoverCycleSummary:
    keywords_total: int = 0
    keywords_processed: int = 0
    succeeded: int = 0
    failed: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    elapsed_ms: int = 0


class DiscoverService:
    """Keyword watchlist + periodic collection for the crawl MVP."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        collector: KeywordCollector | None,
    ):
        self.session_factory = session_factory
        self.collector = collector

    def upsert_keyword(
        self,
        *,
        keyword: str,
        platform: str = "xhs",
        poll_interval_minutes: int = 60,
        enabled: bool = True,
    ) -> DiscoverWatchKeyword:
        if poll_interval_minutes < 1:
            raise ValueError("poll_interval_minutes must be >= 1")
        keyword_text = keyword.strip()
        if not keyword_text:
            raise ValueError("keyword is required")

        with self.session_factory() as session:
            row = session.execute(
                select(DiscoverWatchKeyword).where(DiscoverWatchKeyword.keyword == keyword_text)
            ).scalar_one_or_none()
            if row is None:
                row = DiscoverWatchKeyword(
                    keyword=keyword_text,
                    platform=platform,
                    poll_interval_minutes=poll_interval_minutes,
                    enabled=enabled,
                )
                session.add(row)
            else:
                row.platform = platform
                row.poll_interval_minutes = poll_interval_minutes
                row.enabled = enabled
            session.commit()
            session.refresh(row)
            return row

    def list_keywords(self, *, only_enabled: bool = False, limit: int = 200) -> list[DiscoverWatchKeyword]:
        with self.session_factory() as session:
            stmt = select(DiscoverWatchKeyword).order_by(DiscoverWatchKeyword.id.asc()).limit(limit)
            if only_enabled:
                stmt = stmt.where(DiscoverWatchKeyword.enabled.is_(True))
            return session.execute(stmt).scalars().all()

    def set_keyword_enabled(self, keyword_id: int, enabled: bool) -> DiscoverWatchKeyword:
        with self.session_factory() as session:
            row = session.get(DiscoverWatchKeyword, keyword_id)
            if row is None:
                raise ValueError(f"keyword_id={keyword_id} not found")
            row.enabled = enabled
            session.commit()
            session.refresh(row)
            return row

    def run_once(
        self,
        *,
        keyword_limit: int = 20,
        note_limit: int = 20,
    ) -> DiscoverCycleSummary:
        if keyword_limit < 1:
            raise ValueError("keyword_limit must be >= 1")
        if note_limit < 1:
            raise ValueError("note_limit must be >= 1")
        if self.collector is None:
            raise ValueError("collector is required for run_once")

        started = perf_counter()
        summary = DiscoverCycleSummary()
        due_ids = self._list_due_keyword_ids(limit=keyword_limit)
        summary.keywords_total = len(due_ids)

        logger.info(
            "discover_cycle_started",
            extra={
                "event": "discover_cycle_started",
                "keywords_total": summary.keywords_total,
                "note_limit": note_limit,
            },
        )

        for keyword_id in due_ids:
            summary.keywords_processed += 1
            result = self._process_keyword(keyword_id=keyword_id, note_limit=note_limit)
            if result.get("status") == "done":
                summary.succeeded += 1
                summary.tasks_done += 1
            else:
                summary.failed += 1
                summary.tasks_failed += 1

        summary.elapsed_ms = int((perf_counter() - started) * 1000)
        logger.info(
            "discover_cycle_completed",
            extra={
                "event": "discover_cycle_completed",
                "keywords_total": summary.keywords_total,
                "processed": summary.keywords_processed,
                "succeeded": summary.succeeded,
                "failed": summary.failed,
                "tasks_done": summary.tasks_done,
                "tasks_failed": summary.tasks_failed,
                "elapsed_ms": summary.elapsed_ms,
            },
        )
        return summary

    def _list_due_keyword_ids(self, *, limit: int) -> list[int]:
        now = datetime.now()
        with self.session_factory() as session:
            rows = session.execute(
                select(DiscoverWatchKeyword)
                .where(DiscoverWatchKeyword.enabled.is_(True))
                .order_by(DiscoverWatchKeyword.last_polled_at.asc().nullsfirst(), DiscoverWatchKeyword.id.asc())
            ).scalars().all()

        due: list[int] = []
        for row in rows:
            if row.last_polled_at is None:
                due.append(row.id)
            else:
                interval = timedelta(minutes=max(1, row.poll_interval_minutes))
                if now - row.last_polled_at >= interval:
                    due.append(row.id)
            if len(due) >= limit:
                break
        return due

    def _process_keyword(self, *, keyword_id: int, note_limit: int) -> dict[str, object]:
        with self.session_factory() as session:
            keyword = session.get(DiscoverWatchKeyword, keyword_id)
            if keyword is None:
                return {"status": "failed", "error_message": f"keyword_id={keyword_id} not found"}
            keyword_text = keyword.keyword
            keyword_platform = keyword.platform

        task_id: int | None = None
        try:
            with self.session_factory() as session:
                crawl_service = CrawlTaskService(session)
                task = crawl_service.create_task(keyword_text, platform=keyword_platform)
                task_id = task.id
                crawl_service.start_task(task.id)

            notes, comments_by_note = self.collector.collect(keyword_text, note_limit)
            task_note_count = 0

            with self.session_factory() as session:
                ingest_service = RawIngestService(session)
                ingest_service.ingest_notes(task_id, notes)
                ingest_service.ingest_comments_by_note(comments_by_note)
                task_note_count = session.execute(
                    select(func.count())
                    .select_from(CrawlTaskNote)
                    .where(CrawlTaskNote.task_id == task_id)
                ).scalar_one()

            with self.session_factory() as session:
                crawl_service = CrawlTaskService(session)
                crawl_service.complete_task(task_id, note_count=int(task_note_count))

                keyword_row = session.get(DiscoverWatchKeyword, keyword_id)
                if keyword_row is not None:
                    keyword_row.last_polled_at = datetime.now()
                session.commit()

            logger.info(
                "discover_keyword_completed",
                extra={
                    "event": "discover_keyword_completed",
                    "keyword_id": keyword_id,
                    "keyword": keyword_text,
                    "task_id": task_id,
                    "collected_count": len(notes),
                    "task_status": "done",
                },
            )
            return {"status": "done", "task_id": task_id}
        except Exception as exc:  # noqa: BLE001
            if task_id is not None:
                self._mark_task_failed_if_running(task_id=task_id, error_message=str(exc))
            logger.exception(
                "discover_keyword_failed",
                extra={
                    "event": "discover_keyword_failed",
                    "keyword_id": keyword_id,
                    "task_id": task_id,
                    "error": str(exc),
                },
            )
            return {"status": "failed", "error_message": str(exc), "task_id": task_id}

    def _mark_task_failed_if_running(self, *, task_id: int, error_message: str) -> None:
        with self.session_factory() as session:
            try:
                service = CrawlTaskService(session)
                task = service.get_task(task_id)
                if task.status == TaskStatus.running:
                    service.fail_task(task_id, error_message[:1000])
            except Exception:  # noqa: BLE001
                session.rollback()
