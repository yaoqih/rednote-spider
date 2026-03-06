"""Collector interfaces for discover pipeline."""

from __future__ import annotations

from typing import Any, Protocol

from .command_template_runner import run_command_template_json
from .config import settings


type CollectedPayload = tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]


class KeywordCollector(Protocol):
    def collect(self, keyword: str, limit: int) -> CollectedPayload:
        """Collect normalized notes and comments_by_note for a keyword."""


class CommandKeywordCollector:
    """Run external command template and collect normalized note payloads."""

    def __init__(self, command_template: str):
        self.command_template = (command_template or "").strip()

    def collect(self, keyword: str, limit: int) -> CollectedPayload:
        if limit < 1:
            return [], {}
        keyword_text = keyword.strip()
        if not keyword_text:
            raise ValueError("keyword is required")
        if not self.command_template:
            raise ValueError("command_template is required")

        payload = run_command_template_json(
            command_template=self.command_template,
            keywords=keyword_text,
            max_notes=limit,
            error_prefix="discover command failed",
            timeout_seconds=settings.crawl_command_timeout_seconds,
        )
        return self._normalize_payload(payload=payload, limit=limit)

    def _normalize_payload(
        self,
        *,
        payload: dict[str, Any] | list[dict[str, Any]],
        limit: int,
    ) -> CollectedPayload:
        if not isinstance(payload, dict):
            raise ValueError("discover command payload must be object with notes/comments")
        raw_notes = payload.get("notes")
        if not isinstance(raw_notes, list):
            raise ValueError("discover payload.notes must be a list")
        comments_by_note = self._extract_comments_by_note(payload.get("comments"))

        notes: list[dict[str, Any]] = []
        for idx, row in enumerate(raw_notes):
            if not isinstance(row, dict):
                continue
            notes.append(self._normalize_note(row=row, index=idx))
            if len(notes) >= limit:
                break

        selected_note_ids = {str(item["note_id"]) for item in notes}
        selected_comments_by_note = {
            note_id: rows for note_id, rows in comments_by_note.items() if note_id in selected_note_ids
        }
        return notes, selected_comments_by_note

    @staticmethod
    def _normalize_note(*, row: dict[str, Any], index: int) -> dict[str, Any]:
        note_id = str(row.get("note_id") or "").strip()
        if not note_id:
            raise ValueError(f"discover payload notes[{index}] missing note_id")

        return {
            "note_id": note_id,
            "title": str(row.get("title") or row.get("desc") or ""),
            "content": str(row.get("content") or row.get("desc") or ""),
            "author": str(row.get("author") or row.get("nickname") or ""),
            "likes": CommandKeywordCollector._safe_int(row.get("likes") or row.get("liked_count")),
            "comments_cnt": CommandKeywordCollector._safe_int(
                row.get("comments_cnt") or row.get("comment_count")
            ),
            "collected_cnt": CommandKeywordCollector._safe_int(
                row.get("collected_cnt") or row.get("collected_count")
            ),
            "share_cnt": CommandKeywordCollector._safe_int(
                row.get("share_cnt") or row.get("share_count")
            ),
            "note_url": str(row.get("note_url") or row.get("url") or ""),
            "created_at": row.get("created_at"),
        }

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _extract_comments_by_note(raw_comments: Any) -> dict[str, list[dict[str, Any]]]:
        comments_by_note: dict[str, list[dict[str, Any]]] = {}
        if not isinstance(raw_comments, list):
            return comments_by_note
        for idx, row in enumerate(raw_comments):
            if not isinstance(row, dict):
                continue
            note_id = str(row.get("note_id") or "").strip()
            if not note_id:
                continue
            comment_id = str(row.get("comment_id") or row.get("id") or "").strip()
            if not comment_id:
                raise ValueError(f"discover payload comments[{idx}] missing comment_id")
            comments_by_note.setdefault(note_id, []).append(
                {
                    "comment_id": comment_id,
                    "content": str(row.get("content") or row.get("text") or ""),
                    "author": str(row.get("author") or row.get("nickname") or ""),
                    "likes": CommandKeywordCollector._safe_int(row.get("likes") or row.get("like_count")),
                    "parent_id": str(row.get("parent_id") or ""),
                }
            )
        return comments_by_note
