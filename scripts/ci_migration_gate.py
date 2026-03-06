#!/usr/bin/env python3
"""CI gate: verify schema bootstrap and core crawl/discover tests."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, env=merged_env, check=True)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rednote_ci_schema_") as tmp_dir:
        db_path = Path(tmp_dir) / "gate.db"
        db_url = f"sqlite:///{db_path}"

        run(
            [
                sys.executable,
                "scripts/init_schema.py",
                "--database-url",
                db_url,
            ],
            env={"DATABASE_URL": db_url, "PYTHONPATH": "src"},
        )

    # Mandatory smoke tests for core crawl/discover flow.
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_schema.py",
            "tests/test_init_schema_script.py",
            "tests/test_config.py",
            "tests/test_discover_collectors.py",
            "tests/test_discover_service.py",
            "tests/test_keyword_crawl_service.py",
            "tests/test_product_opportunity_service.py",
            "tests/test_raw_ingest_service.py",
            "tests/test_run_discover_cycle_script.py",
            "tests/test_run_external_crawler_script.py",
            "tests/test_run_product_opportunity_cycle_script.py",
            "tests/test_verify_live_crawl_script.py",
            "tests/test_ui_security.py",
            "tests/test_observability.py",
        ]
    )

    print("ci_migration_gate=ok")


if __name__ == "__main__":
    main()
