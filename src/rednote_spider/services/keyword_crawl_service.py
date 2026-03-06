"""Keyword-driven crawl runner for the simplified MVP (command-only backend)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..command_template_runner import run_command_template_json
from ..config import settings
from ..models import CrawlTaskNote
from .crawl_task_service import CrawlTaskService
from .raw_ingest_service import RawIngestService


@dataclass(slots=True)
class CrawlRunResult:
    task_id: int
    backend: str
    note_count: int
    notes_upserted: int
    comments_upserted: int


class KeywordCrawlService:
    def __init__(self, session: Session):
        self.session = session
        self.tasks = CrawlTaskService(session)
        self.ingest = RawIngestService(session)

    def run_task(
        self,
        *,
        task_id: int,
        max_notes: int = 20,
        backend: str = "command",
        command_template: str | None = None,
    ) -> CrawlRunResult:
        task = self.tasks.start_task(task_id)
        if max_notes < 1:
            self.tasks.fail_task(task_id, "max_notes must be >= 1")
            raise ValueError("max_notes must be >= 1")

        try:
            notes, comments_by_note = self._collect_payload(
                keywords=task.keywords,
                max_notes=max_notes,
                backend=backend,
                command_template=command_template,
            )

            note_summary = self.ingest.ingest_notes(task_id=task.id, notes=notes)
            comment_summary = self.ingest.ingest_comments_by_note(comments_by_note)

            note_count = self.session.execute(
                select(func.count())
                .select_from(CrawlTaskNote)
                .where(CrawlTaskNote.task_id == task.id)
            ).scalar_one()
            self.tasks.complete_task(task.id, int(note_count))

            return CrawlRunResult(
                task_id=task.id,
                backend="command",
                note_count=int(note_count),
                notes_upserted=note_summary.total,
                comments_upserted=comment_summary.total,
            )
        except Exception as exc:  # noqa: BLE001
            # A flush/commit failure leaves the Session in failed state until rollback.
            self.session.rollback()
            try:
                self.tasks.fail_task(task.id, str(exc))
            except Exception:  # noqa: BLE001
                self.session.rollback()
            raise

    def _collect_payload(
        self,
        *,
        keywords: str,
        max_notes: int,
        backend: str,
        command_template: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        if backend != "command":
            raise ValueError("only command backend is supported")
        return self._collect_command_payload(
            keywords=keywords,
            max_notes=max_notes,
            command_template=command_template,
        )

    def _collect_command_payload(
        self,
        *,
        keywords: str,
        max_notes: int,
        command_template: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        payload = run_command_template_json(
            command_template=(command_template or "").strip() or settings.crawl_command_template,
            keywords=keywords,
            max_notes=max_notes,
            error_prefix="crawl command failed",
            timeout_seconds=settings.crawl_command_timeout_seconds,
        )
        return self._normalize_external_payload(payload)

    def _normalize_external_payload(
        self, payload: dict[str, Any] | list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        notes: list[dict[str, Any]] = []
        comments_by_note: dict[str, list[dict[str, Any]]] = {}

        if not isinstance(payload, dict):
            raise ValueError("command payload must be object with notes/comments")

        raw_notes = payload.get("notes")
        if not isinstance(raw_notes, list):
            raise ValueError("payload.notes must be a list")
        notes = [self._normalize_note(n) for n in raw_notes if isinstance(n, dict)]

        raw_comments = payload.get("comments")
        if raw_comments is not None and not isinstance(raw_comments, list):
            raise ValueError("payload.comments must be a list")

        selected_note_ids = {str(item["note_id"]) for item in notes}
        if isinstance(raw_comments, list):
            for idx, row in enumerate(raw_comments):
                if not isinstance(row, dict):
                    continue
                note_id = str(row.get("note_id") or "").strip()
                if not note_id or note_id not in selected_note_ids:
                    continue
                comment_id = str(row.get("comment_id") or row.get("commentId") or row.get("id") or "").strip()
                if not comment_id:
                    raise ValueError(f"payload.comments[{idx}].comment_id is required")
                comments_by_note.setdefault(note_id, []).append(
                    {
                        "comment_id": comment_id,
                        "content": str(row.get("content") or row.get("text") or ""),
                        "author": str(row.get("author") or row.get("nickname") or ""),
                        "likes": self._safe_int(row.get("likes") or row.get("like_count")),
                        "parent_id": str(row.get("parent_id") or row.get("parentId") or ""),
                    }
                )
        return notes, comments_by_note

    @staticmethod
    def _normalize_note(row: dict[str, Any]) -> dict[str, Any]:
        note_id = str(row.get("note_id") or "").strip()
        if not note_id:
            raise ValueError("note row missing note_id")
        note = dict(row)
        note.pop("comments", None)
        note["note_id"] = note_id
        return note

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
