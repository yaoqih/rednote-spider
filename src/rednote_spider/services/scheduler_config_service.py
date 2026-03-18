"""Database-backed scheduler runtime configuration."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from ..models import SchedulerRuntimeConfig

SUPPORTED_SCHEDULER_MODES: tuple[str, ...] = ("discover", "opportunity")


class SchedulerConfigService:
    """Manage persisted runtime scheduler settings."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def list_configs(self) -> list[SchedulerRuntimeConfig]:
        with self.session_factory() as session:
            rows = [self._ensure_row(session, mode) for mode in SUPPORTED_SCHEDULER_MODES]
            session.commit()
            for row in rows:
                session.refresh(row)
            return rows

    def get_config(self, mode: str) -> SchedulerRuntimeConfig:
        normalized_mode = self._normalize_mode(mode)
        with self.session_factory() as session:
            row = self._ensure_row(session, normalized_mode)
            session.commit()
            session.refresh(row)
            return row

    def set_config(
        self,
        mode: str,
        *,
        enabled: bool,
        loop_interval_seconds: int,
        note_limit: int | None = None,
    ) -> SchedulerRuntimeConfig:
        normalized_mode = self._normalize_mode(mode)
        if loop_interval_seconds < 1:
            raise ValueError("loop_interval_seconds must be >= 1")
        if normalized_mode != "discover" and note_limit is not None:
            raise ValueError("note_limit is only supported for discover mode")
        if note_limit is not None and note_limit < 1:
            raise ValueError("note_limit must be >= 1")

        with self.session_factory() as session:
            row = self._ensure_row(session, normalized_mode)
            row.enabled = enabled
            row.loop_interval_seconds = int(loop_interval_seconds)
            if normalized_mode == "discover":
                row.note_limit = (
                    int(note_limit)
                    if note_limit is not None
                    else row.note_limit or self._default_note_limit(normalized_mode)
                )
            else:
                row.note_limit = None
            session.commit()
            session.refresh(row)
            return row

    def _ensure_row(self, session: Session, mode: str) -> SchedulerRuntimeConfig:
        row = session.execute(
            select(SchedulerRuntimeConfig).where(SchedulerRuntimeConfig.mode == mode)
        ).scalar_one_or_none()
        if row is None:
            row = SchedulerRuntimeConfig(
                mode=mode,
                enabled=True,
                loop_interval_seconds=self._default_interval(mode),
                note_limit=self._default_note_limit(mode),
            )
            session.add(row)
            session.flush()
        elif mode == "discover" and row.note_limit is None:
            row.note_limit = self._default_note_limit(mode)
        return row

    def _default_interval(self, mode: str) -> int:
        if mode == "discover":
            return int(settings.sched_discover_loop_interval_seconds)
        if mode == "opportunity":
            return int(settings.sched_opportunity_loop_interval_seconds)
        raise ValueError(f"unsupported mode: {mode}")

    def _default_note_limit(self, mode: str) -> int | None:
        if mode == "discover":
            return int(settings.sched_discover_note_limit)
        if mode == "opportunity":
            return None
        raise ValueError(f"unsupported mode: {mode}")

    def _normalize_mode(self, mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized not in SUPPORTED_SCHEDULER_MODES:
            raise ValueError(f"unsupported mode: {mode}")
        return normalized
