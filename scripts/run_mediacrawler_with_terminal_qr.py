#!/usr/bin/env python3
"""Run MediaCrawler with rednote runtime patches installed."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REDNOTE_SRC = PROJECT_ROOT / "src"
DEFAULT_QR_DIR = PROJECT_ROOT / "logs" / "login_qr"


def _read_bool_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _ensure_pythonpath() -> None:
    if str(REDNOTE_SRC) not in sys.path:
        sys.path.insert(0, str(REDNOTE_SRC))
    media_crawler_root = Path.cwd().resolve()
    if str(media_crawler_root) not in sys.path:
        sys.path.insert(0, str(media_crawler_root))


def _install_runtime_patches() -> None:
    from rednote_spider.mediacrawler_runtime import install_resilient_navigation_patch
    from rednote_spider.mediacrawler_qr import emit_terminal_image_and_save, install_qr_login_flow_patch
    from rednote_spider.mediacrawler_phone import install_phone_login_patch

    qr_dir = Path(os.environ.get("REDNOTE_QR_OUTPUT_DIR", DEFAULT_QR_DIR)).expanduser().resolve()

    def _patched_show(self: Image.Image, *args, **kwargs) -> None:
        del args, kwargs
        emit_terminal_image_and_save(self.copy(), output_dir=qr_dir, filename_prefix="xhs-login")

    Image.Image.show = _patched_show
    install_resilient_navigation_patch()
    install_qr_login_flow_patch()
    install_phone_login_patch()


def _configure_crawler_defaults() -> None:
    from rednote_spider.mediacrawler_runtime import apply_shared_login_profile_defaults
    import config as crawler_config

    apply_shared_login_profile_defaults(
        crawler_config,
        enable_cdp=_read_bool_env("REDNOTE_CRAWL_ENABLE_CDP", default=False),
    )


def _prepare_runtime_argv() -> None:
    from rednote_spider.mediacrawler_runtime import (
        has_display_server,
        normalize_mediacrawler_cli_args,
    )

    original_args = list(sys.argv[1:])
    if not has_display_server():
        return

    normalized_args = normalize_mediacrawler_cli_args(original_args)
    if normalized_args != original_args:
        print(
            "[rednote-spider] interactive login requires a headed browser; overriding --headless to false.",
            file=sys.stderr,
            flush=True,
        )
        sys.argv = [sys.argv[0], *normalized_args]


def main() -> None:
    _ensure_pythonpath()
    _prepare_runtime_argv()
    _configure_crawler_defaults()
    _install_runtime_patches()
    sys.argv[0] = "main.py"
    main_path = Path.cwd().resolve() / "main.py"
    if not main_path.exists():
        raise SystemExit(f"MediaCrawler main.py not found in cwd: {Path.cwd().resolve()}")
    runpy.run_path(str(main_path), run_name="__main__")


if __name__ == "__main__":
    main()
