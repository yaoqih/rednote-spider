#!/usr/bin/env python3
"""Run unified login controller loop."""

from __future__ import annotations

import argparse
import time

from rednote_spider.config import settings
from rednote_spider.database import SessionLocal
from rednote_spider.login_controller import (
    LoginControllerRuntime,
    build_controller_config,
    run_login_controller_iteration,
    stop_login_controller_runtime,
)
from rednote_spider.observability import configure_logging, get_logger, log_database_target

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run unified login controller")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    configure_logging(settings.log_level)
    args = build_parser().parse_args()
    controller_config = build_controller_config()
    runtime = LoginControllerRuntime()
    log_database_target(logger, database_url=settings.database_url, source="run_login_controller")
    try:
        while True:
            runtime = run_login_controller_iteration(SessionLocal, runtime, controller_config)
            if args.once:
                break
            time.sleep(max(1, int(settings.login_controller_poll_seconds)))
    finally:
        stop_login_controller_runtime(runtime)


if __name__ == "__main__":
    main()
