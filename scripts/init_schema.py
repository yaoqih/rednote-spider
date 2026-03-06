#!/usr/bin/env python3
"""Initialize core schema with SQLAlchemy metadata (no Alembic)."""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine, inspect

from rednote_spider.config import settings
from rednote_spider.models import Base, CORE_TABLES
from rednote_spider.observability import configure_logging, get_logger, log_database_target

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize core database schema")
    parser.add_argument("--database-url", default=settings.database_url)
    return parser


def main() -> None:
    configure_logging(settings.log_level)
    args = build_parser().parse_args()
    log_database_target(logger, database_url=args.database_url, source="init_schema")
    engine = create_engine(args.database_url, future=True)
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    missing = [name for name in CORE_TABLES if name not in existing]
    if missing:
        raise SystemExit(f"schema_init_failed missing_tables={','.join(missing)}")
    drift_items: list[str] = []
    for table_name in CORE_TABLES:
        expected_cols = set(Base.metadata.tables[table_name].columns.keys())
        actual_cols = {col["name"] for col in inspector.get_columns(table_name)}
        missing_cols = sorted(expected_cols - actual_cols)
        if missing_cols:
            drift_items.append(f"{table_name}({','.join(missing_cols)})")
    if drift_items:
        raise SystemExit(f"schema_drift_detected missing_columns={' ; '.join(drift_items)}")
    print(f"schema_init=ok table_count={len(existing)}")


if __name__ == "__main__":
    main()
