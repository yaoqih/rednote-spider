#!/usr/bin/env python3
"""Run discover cycles: watchlist -> command collect -> ingest."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict

from rednote_spider.config import settings
from rednote_spider.database import SessionLocal
from rednote_spider.discover_collectors import CommandKeywordCollector
from rednote_spider.observability import configure_logging, get_logger, log_database_target
from rednote_spider.services.discover_service import DiscoverService

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run discover cycles")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--keyword-limit", type=int, default=20)
    parser.add_argument("--note-limit", type=int, default=20)
    parser.add_argument(
        "--command-template",
        default=settings.crawl_command_template,
        help='command template, supports "{keywords}" and "{max_notes}"',
    )
    return parser


def main() -> None:
    configure_logging(settings.log_level)
    args = build_parser().parse_args()
    if args.cycles < 1:
        raise SystemExit("--cycles must be >= 1")
    if args.keyword_limit < 1:
        raise SystemExit("--keyword-limit must be >= 1")
    if args.note_limit < 1:
        raise SystemExit("--note-limit must be >= 1")
    if args.interval_seconds < 0:
        raise SystemExit("--interval-seconds must be >= 0")
    if not str(args.command_template or "").strip():
        raise SystemExit("--command-template is required")

    log_database_target(logger, database_url=settings.database_url, source="run_discover_cycle")
    collector = CommandKeywordCollector(args.command_template)
    service = DiscoverService(SessionLocal, collector)

    for idx in range(args.cycles):
        summary = service.run_once(
            keyword_limit=args.keyword_limit,
            note_limit=args.note_limit,
        )
        logger.info(
            "discover_cycle_summary",
            extra={
                "event": "discover_cycle_summary",
                **asdict(summary),
                "cycle_index": idx + 1,
                "cycles": args.cycles,
            },
        )
        print(json.dumps(asdict(summary), ensure_ascii=False))
        if idx < args.cycles - 1 and args.interval_seconds > 0:
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
