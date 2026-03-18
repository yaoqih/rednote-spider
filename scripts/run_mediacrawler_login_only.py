#!/usr/bin/env python3
"""Run the unified MediaCrawler login-only runtime."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REDNOTE_SRC = PROJECT_ROOT / "src"

if str(REDNOTE_SRC) not in sys.path:
    sys.path.insert(0, str(REDNOTE_SRC))

from rednote_spider.mediacrawler_login_runtime import main


if __name__ == "__main__":
    raise SystemExit(main())
