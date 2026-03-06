#!/usr/bin/env python3
"""Run product-opportunity evaluation for crawled notes/comments."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from rednote_spider.config import settings
from rednote_spider.database import make_engine
from rednote_spider.observability import configure_logging, get_logger, log_database_target
from rednote_spider.services.product_opportunity_service import ProductOpportunityService

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run product opportunity cycle")
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--limit-tasks", type=int, default=20)
    parser.add_argument("--prescreen-threshold", type=float, default=3.2)
    parser.add_argument("--match-threshold", type=float, default=0.26)
    parser.add_argument("--retry-backoff-base-minutes", type=int, default=5)
    parser.add_argument("--retry-backoff-max-minutes", type=int, default=720)
    return parser


def _is_schema_missing_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "no such table",
        "does not exist",
        "undefinedtable",
    )
    return any(token in text for token in markers)


def main() -> None:
    configure_logging(settings.log_level)
    args = build_parser().parse_args()

    if args.task_id < 0:
        raise SystemExit("--task-id must be >= 0")
    if args.limit_tasks < 1:
        raise SystemExit("--limit-tasks must be >= 1")
    if args.retry_backoff_base_minutes < 1:
        raise SystemExit("--retry-backoff-base-minutes must be >= 1")
    if args.retry_backoff_max_minutes < 1:
        raise SystemExit("--retry-backoff-max-minutes must be >= 1")

    log_database_target(logger, database_url=args.database_url, source="run_product_opportunity_cycle")
    engine = make_engine(args.database_url)
    try:
        with Session(engine) as session:
            service = ProductOpportunityService(session)
            if args.task_id > 0:
                summary = service.process_task(
                    args.task_id,
                    prescreen_threshold=float(args.prescreen_threshold),
                    match_threshold=float(args.match_threshold),
                )
            else:
                summary = service.process_recent_done_tasks(
                    limit=int(args.limit_tasks),
                    prescreen_threshold=float(args.prescreen_threshold),
                    match_threshold=float(args.match_threshold),
                    retry_backoff_base_minutes=int(args.retry_backoff_base_minutes),
                    retry_backoff_max_minutes=int(args.retry_backoff_max_minutes),
                )
            print(json.dumps(asdict(summary), ensure_ascii=False))
    except SQLAlchemyError as exc:
        if _is_schema_missing_error(exc):
            raise SystemExit(
                "database schema is not initialized; run `python scripts/init_schema.py` first"
            ) from exc
        raise SystemExit(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
