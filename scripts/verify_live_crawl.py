#!/usr/bin/env python3
"""Run one crawl task and print persistence summary for manual acceptance."""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from rednote_spider.config import settings
from rednote_spider.models import CrawlTaskNote, RawComment
from rednote_spider.observability import configure_logging, get_logger, log_database_target
from rednote_spider.services.crawl_task_service import CrawlTaskService
from rednote_spider.services.keyword_crawl_service import KeywordCrawlService

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify one crawl run for MVP acceptance")
    parser.add_argument("--keywords", default="通勤 焦虑")
    parser.add_argument("--platform", default="xhs")
    parser.add_argument("--max-notes", type=int, default=10)
    parser.add_argument("--command-template", default=settings.crawl_command_template)
    parser.add_argument("--database-url", default=settings.database_url)
    return parser


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    engine = create_engine(args.database_url, future=True)
    with Session(engine) as session:
        task = CrawlTaskService(session).create_task(keywords=args.keywords, platform=args.platform)
        result = KeywordCrawlService(session).run_task(
            task_id=task.id,
            max_notes=args.max_notes,
            backend="command",
            command_template=args.command_template,
        )
        note_count = session.execute(
            select(func.count()).select_from(CrawlTaskNote).where(CrawlTaskNote.task_id == task.id)
        ).scalar_one()
        comment_count = session.execute(
            select(func.count())
            .select_from(RawComment)
            .join(CrawlTaskNote, RawComment.note_id == CrawlTaskNote.note_id)
            .where(CrawlTaskNote.task_id == task.id)
        ).scalar_one()
        persisted_task = CrawlTaskService(session).get_task(task.id)

        return {
            "task_id": task.id,
            "backend": "command",
            "keywords": args.keywords,
            "task_status": persisted_task.status.value,
            "task_note_count": int(persisted_task.note_count),
            "ingested_note_count": int(note_count),
            "ingested_comment_count": int(comment_count),
            "notes_upserted": result.notes_upserted,
            "comments_upserted": result.comments_upserted,
        }


def main() -> None:
    configure_logging(settings.log_level)
    args = build_parser().parse_args()
    if args.max_notes < 1:
        raise SystemExit("--max-notes must be >= 1")
    if not str(args.command_template or "").strip():
        raise SystemExit("--command-template is required")

    log_database_target(logger, database_url=args.database_url, source="verify_live_crawl")
    try:
        summary = run_once(args)
        print(json.dumps(summary, ensure_ascii=False))
    except OperationalError as exc:
        message = str(exc)
        if "no such table" in message.lower():
            raise SystemExit(
                "database schema is not initialized; run `python scripts/init_schema.py` first"
            ) from exc
        raise SystemExit(message) from exc
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
