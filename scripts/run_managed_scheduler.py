#!/usr/bin/env python3
"""Run one database-managed scheduler iteration."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict

from rednote_spider.config import settings
from rednote_spider.database import SessionLocal
from rednote_spider.discover_collectors import CommandKeywordCollector
from rednote_spider.observability import configure_logging, get_logger, log_database_target
from rednote_spider.services.discover_service import DiscoverService
from rednote_spider.services.product_opportunity_service import ProductOpportunityService
from rednote_spider.services.scheduler_config_service import SchedulerConfigService

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one managed scheduler iteration")
    parser.add_argument("--mode", choices=["discover"], required=True)
    parser.add_argument("--once", action="store_true", help="accepted for compatibility; single-run is default")
    return parser


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None and str(value).strip() else int(default)


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None and str(value).strip() else float(default)


def _run_discover_once(*, note_limit: int) -> dict[str, int]:
    command_template = os.getenv("SCHED_DISCOVER_COMMAND_TEMPLATE") or settings.crawl_command_template
    if not str(command_template or "").strip():
        raise SystemExit("discover command_template is required")

    collector = CommandKeywordCollector(command_template)
    service = DiscoverService(SessionLocal, collector)
    summary = service.run_once(
        keyword_limit=_get_int_env("SCHED_DISCOVER_KEYWORD_LIMIT", 20),
        note_limit=int(note_limit),
    )
    return asdict(summary)


def _run_opportunity_once() -> dict[str, int]:
    with SessionLocal() as session:
        service = ProductOpportunityService(session)
        summary = service.process_recent_done_tasks(
            limit=_get_int_env("SCHED_OPPORTUNITY_LIMIT_TASKS", 20),
            prescreen_threshold=_get_float_env("SCHED_OPPORTUNITY_PRESCREEN_THRESHOLD", 3.2),
            match_threshold=_get_float_env("SCHED_OPPORTUNITY_MATCH_THRESHOLD", 0.26),
            retry_backoff_base_minutes=_get_int_env("SCHED_OPPORTUNITY_RETRY_BACKOFF_BASE_MINUTES", 5),
            retry_backoff_max_minutes=_get_int_env("SCHED_OPPORTUNITY_RETRY_BACKOFF_MAX_MINUTES", 720),
        )
        return asdict(summary)


def _run_discover_pipeline_once(*, note_limit: int) -> dict[str, object]:
    return {
        "discover": _run_discover_once(note_limit=note_limit),
        "opportunity": _run_opportunity_once(),
    }


def run_mode_once(mode: str) -> dict[str, object]:
    config = SchedulerConfigService(SessionLocal).get_config(mode)
    payload: dict[str, object] = {
        "mode": config.mode,
        "enabled": bool(config.enabled),
        "loop_interval_seconds": int(config.loop_interval_seconds),
    }
    if mode == "discover":
        payload["note_limit"] = int(config.note_limit or _get_int_env("SCHED_DISCOVER_NOTE_LIMIT", settings.sched_discover_note_limit))
    if not config.enabled:
        payload["status"] = "skipped_disabled"
        logger.info(
            "managed_scheduler_skipped_disabled",
            extra={"event": "managed_scheduler_skipped_disabled", **payload},
        )
        return payload

    summary = _run_discover_pipeline_once(note_limit=int(payload["note_limit"]))

    payload["status"] = "completed"
    payload["summary"] = summary
    logger.info(
        "managed_scheduler_completed",
        extra={"event": "managed_scheduler_completed", **payload},
    )
    return payload


def main() -> None:
    configure_logging(settings.log_level)
    args = build_parser().parse_args()
    log_database_target(logger, database_url=settings.database_url, source="run_managed_scheduler")
    print(json.dumps(run_mode_once(args.mode), ensure_ascii=False))


if __name__ == "__main__":
    main()
