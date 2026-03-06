#!/usr/bin/env python3
"""Manage discover watch keywords."""

from __future__ import annotations

import argparse

from rednote_spider.config import settings
from rednote_spider.database import SessionLocal
from rednote_spider.observability import configure_logging, get_logger, log_database_target
from rednote_spider.services.discover_service import DiscoverService

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage discover watch keywords")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add")
    add.add_argument("keyword")
    add.add_argument("--platform", default="xhs")
    add.add_argument("--interval", type=int, default=60, help="poll interval minutes")
    add.add_argument("--disabled", action="store_true")

    sub.add_parser("list")

    enable = sub.add_parser("enable")
    enable.add_argument("keyword_id", type=int)

    disable = sub.add_parser("disable")
    disable.add_argument("keyword_id", type=int)

    return parser


def main() -> None:
    configure_logging(settings.log_level)
    args = build_parser().parse_args()
    log_database_target(logger, database_url=settings.database_url, source="manage_discover_keywords")
    service = DiscoverService(SessionLocal, collector=None)

    if args.command == "add":
        row = service.upsert_keyword(
            keyword=args.keyword,
            platform=args.platform,
            poll_interval_minutes=args.interval,
            enabled=not args.disabled,
        )
        print(
            f"keyword_upserted=id:{row.id},keyword:{row.keyword},enabled:{row.enabled},"
            f"interval:{row.poll_interval_minutes}"
        )
        return

    if args.command == "list":
        rows = service.list_keywords(limit=1000)
        for row in rows:
            last_polled = row.last_polled_at.isoformat(sep=" ", timespec="seconds") if row.last_polled_at else "-"
            print(
                f"id={row.id} keyword={row.keyword} enabled={row.enabled} "
                f"interval={row.poll_interval_minutes}m last_polled_at={last_polled}"
            )
        if not rows:
            print("(no keywords)")
        return

    if args.command == "enable":
        row = service.set_keyword_enabled(args.keyword_id, True)
        print(f"keyword_enabled=id:{row.id},keyword:{row.keyword}")
        return

    if args.command == "disable":
        row = service.set_keyword_enabled(args.keyword_id, False)
        print(f"keyword_disabled=id:{row.id},keyword:{row.keyword}")
        return


if __name__ == "__main__":
    main()
