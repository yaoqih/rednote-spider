"""Minimal raw note/comment ingestion with idempotent upsert."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ..models import CrawlTaskNote, RawComment, RawNote


@dataclass(slots=True)
class UpsertSummary:
    inserted: int = 0
    updated: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated

    def merge(self, other: "UpsertSummary") -> None:
        self.inserted += other.inserted
        self.updated += other.updated


class RawIngestService:
    """Single-entry ingestion service used by crawl and discover flows."""

    def __init__(self, session: Session):
        self.session = session

    def ingest_notes(self, task_id: int, notes: list[dict[str, Any]]) -> UpsertSummary:
        summary = self._upsert_notes(task_id=task_id, notes=notes)
        self.session.commit()
        return summary

    def ingest_comments(self, note_id: str, comments: list[dict[str, Any]]) -> UpsertSummary:
        summary = self._upsert_comments(note_id=note_id, comments=comments)
        self.session.commit()
        return summary

    def ingest_comments_by_note(self, comments_by_note: dict[str, list[dict[str, Any]]]) -> UpsertSummary:
        summary = UpsertSummary()
        for note_id, comments in comments_by_note.items():
            partial = self._upsert_comments(note_id=note_id, comments=comments)
            summary.merge(partial)
        self.session.commit()
        return summary

    def _upsert_notes(self, *, task_id: int, notes: list[dict[str, Any]]) -> UpsertSummary:
        summary = UpsertSummary()
        if not notes:
            return summary

        note_ids = [str(n.get("note_id") or "").strip() for n in notes]
        existing = self._note_map([note_id for note_id in note_ids if note_id])

        for idx, payload in enumerate(notes):
            note_id = str(payload.get("note_id") or "").strip()
            if not note_id:
                raise ValueError(f"notes[{idx}].note_id is required")

            model = existing.get(note_id)
            if model is None:
                model = RawNote(task_id=task_id, note_id=note_id)
                self.session.add(model)
                existing[note_id] = model
                summary.inserted += 1
            else:
                summary.updated += 1

            self._apply_note_fields(model, payload)
            # Persist raw_note first. On PostgreSQL, FK checks on non-PK unique keys
            # can fail if crawl_task_note is flushed before raw_note(note_id).
            self.session.flush()
            self._link_task_note(task_id=task_id, note_id=note_id)

        return summary

    def _upsert_comments(self, *, note_id: str, comments: list[dict[str, Any]]) -> UpsertSummary:
        summary = UpsertSummary()
        if not comments:
            return summary

        note_token = note_id.strip()
        if not note_token:
            raise ValueError("note_id is required")

        parent = self.session.execute(
            select(RawNote).where(RawNote.note_id == note_token)
        ).scalar_one_or_none()
        if parent is None:
            raise ValueError(f"note_id={note_token} not found; insert note before comments")

        comment_ids = [str(c.get("comment_id") or "").strip() for c in comments]
        existing = self._comment_map([comment_id for comment_id in comment_ids if comment_id])

        for idx, payload in enumerate(comments):
            comment_id = str(payload.get("comment_id") or "").strip()
            if not comment_id:
                raise ValueError(f"comments[{idx}].comment_id is required")

            model = existing.get(comment_id)
            if model is None:
                model = RawComment(note_id=note_token, comment_id=comment_id)
                self.session.add(model)
                existing[comment_id] = model
                summary.inserted += 1
            else:
                summary.updated += 1

            self._apply_comment_fields(model, payload, note_id=note_token)

        return summary

    def _note_map(self, note_ids: list[str]) -> dict[str, RawNote]:
        if not note_ids:
            return {}
        stmt: Select[tuple[RawNote]] = select(RawNote).where(RawNote.note_id.in_(set(note_ids)))
        rows = self.session.execute(stmt).scalars().all()
        return {row.note_id: row for row in rows}

    def _comment_map(self, comment_ids: list[str]) -> dict[str, RawComment]:
        if not comment_ids:
            return {}
        stmt: Select[tuple[RawComment]] = select(RawComment).where(
            RawComment.comment_id.in_(set(comment_ids))
        )
        rows = self.session.execute(stmt).scalars().all()
        return {row.comment_id: row for row in rows}

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                try:
                    ts = float(text)
                except ValueError as exc:
                    raise TypeError(f"unsupported datetime value: {value!r}") from exc
                if ts > 1e12:
                    ts = ts / 1000.0
                return datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)
        raise TypeError(f"unsupported datetime value: {value!r}")

    def _link_task_note(self, *, task_id: int, note_id: str) -> None:
        row = self.session.execute(
            select(CrawlTaskNote).where(
                CrawlTaskNote.task_id == task_id,
                CrawlTaskNote.note_id == note_id,
            )
        ).scalar_one_or_none()
        if row is None:
            self.session.add(CrawlTaskNote(task_id=task_id, note_id=note_id))

    def _apply_note_fields(self, model: RawNote, payload: dict[str, Any]) -> None:
        model.title = payload.get("title")
        model.content = payload.get("content")
        model.author = payload.get("author")
        model.likes = int(payload.get("likes", 0) or 0)
        model.comments_cnt = int(payload.get("comments_cnt", 0) or 0)
        model.collected_cnt = int(payload.get("collected_cnt", 0) or 0)
        model.share_cnt = int(payload.get("share_cnt", 0) or 0)
        model.note_url = payload.get("note_url")
        model.created_at = self._coerce_datetime(payload.get("created_at"))

    def _apply_comment_fields(self, model: RawComment, payload: dict[str, Any], note_id: str) -> None:
        model.note_id = note_id
        model.content = payload.get("content")
        model.author = payload.get("author")
        model.likes = int(payload.get("likes", 0) or 0)
        model.parent_id = payload.get("parent_id")
