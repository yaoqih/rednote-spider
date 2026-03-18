"""Login-only MediaCrawler runtime for unified XHS authentication."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from sqlalchemy.orm import Session, sessionmaker

from .login_runtime_events import format_login_runtime_event
from .mediacrawler_runtime import apply_shared_login_profile_defaults, build_browser_launch_options

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REDNOTE_SRC = PROJECT_ROOT / "src"
DEFAULT_QR_OUTPUT_DIR = PROJECT_ROOT / "logs" / "login_qr"
DEFAULT_SECURITY_OUTPUT_DIR = PROJECT_ROOT / "logs" / "login_security"


@dataclass(slots=True)
class MediaCrawlerLoginRuntimeConfig:
    platform: str
    method: str
    attempt_id: int
    phone_number: str = ""
    crawler_cwd: Path = PROJECT_ROOT.parent / "MediaCrawler"
    qr_output_dir: Path = DEFAULT_QR_OUTPUT_DIR
    security_output_dir: Path = DEFAULT_SECURITY_OUTPUT_DIR
    prefer_headed: bool = True


def emit_login_runtime_event(
    event_type: str,
    message: str = "",
    *,
    attempt_id: int,
    stream: TextIO | None = None,
    **payload: object,
) -> str:
    rendered = format_login_runtime_event(
        event_type,
        message,
        attempt_id=int(attempt_id),
        **payload,
    )
    target = stream or sys.stderr
    print(rendered, file=target, flush=True)
    return rendered


def map_phone_stage_event(stage: str, message: str = "", **payload: object) -> tuple[str, str, dict[str, object]]:
    mapping = {
        "waiting_code": "waiting_phone_code",
        "need_verify": "waiting_security_verification",
        "verifying": "verifying",
        "invalid_sms_code": "invalid_sms_code",
        "failed": "authentication_failed",
    }
    event_type = mapping.get(stage, stage)
    normalized_payload = dict(payload)
    return event_type, message, normalized_payload


def consume_sms_code_from_database(
    *,
    session_factory: sessionmaker[Session],
    attempt_id: int,
    platform: str = "xhs",
) -> str | None:
    from .services.login_controller_service import LoginControllerService

    return LoginControllerService(session_factory).consume_submitted_sms_code(
        attempt_id=int(attempt_id),
        platform=platform,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MediaCrawler login-only runtime")
    parser.add_argument("--platform", default=os.getenv("REDNOTE_LOGIN_PLATFORM", "xhs"))
    parser.add_argument("--method", choices=("probe", "qr", "phone"), required=True)
    parser.add_argument("--attempt-id", type=int, default=int(os.getenv("REDNOTE_LOGIN_ATTEMPT_ID", "0") or 0))
    parser.add_argument("--phone-number", default=os.getenv("REDNOTE_LOGIN_PHONE", ""))
    return parser


def build_runtime_config(args: argparse.Namespace) -> MediaCrawlerLoginRuntimeConfig:
    configured_crawler_cwd = str(
        os.getenv("REDNOTE_LOGIN_CRAWLER_CWD") or os.getenv("LOGIN_RUNTIME_CRAWLER_CWD") or ""
    ).strip()
    return MediaCrawlerLoginRuntimeConfig(
        platform=str(args.platform or "xhs").strip() or "xhs",
        method=str(args.method or "").strip(),
        attempt_id=max(0, int(args.attempt_id or 0)),
        phone_number=str(args.phone_number or "").strip(),
        crawler_cwd=(
            Path(configured_crawler_cwd).expanduser().resolve()
            if configured_crawler_cwd
            else (PROJECT_ROOT.parent / "MediaCrawler").resolve()
        ),
        qr_output_dir=Path(os.getenv("REDNOTE_QR_OUTPUT_DIR", DEFAULT_QR_OUTPUT_DIR)).expanduser().resolve(),
        security_output_dir=Path(
            os.getenv("REDNOTE_SECURITY_OUTPUT_DIR", DEFAULT_SECURITY_OUTPUT_DIR)
        ).expanduser().resolve(),
        prefer_headed=str(os.getenv("REDNOTE_LOGIN_PREFER_HEADED", "1")).strip().lower() not in {"0", "false", "no"},
    )


def _ensure_pythonpath(crawler_cwd: Path) -> None:
    if str(REDNOTE_SRC) not in sys.path:
        sys.path.insert(0, str(REDNOTE_SRC))
    if str(crawler_cwd) not in sys.path:
        sys.path.insert(0, str(crawler_cwd))


def _configure_mediacrawler_defaults(
    config_obj,
    *,
    platform: str,
    method: str,
    prefer_headed: bool,
) -> Path:
    launch_options = build_browser_launch_options(
        method=method,
        prefer_headed=prefer_headed,
    )
    config_obj.PLATFORM = platform
    config_obj.LOGIN_TYPE = "phone" if method == "phone" else "qrcode"
    config_obj.CRAWLER_TYPE = "search"
    apply_shared_login_profile_defaults(config_obj, enable_cdp=False)
    config_obj.HEADLESS = bool(launch_options["headless"])
    config_obj.CDP_HEADLESS = bool(launch_options["headless"])
    user_data_dir = Path.cwd().resolve() / "browser_data" / (config_obj.USER_DATA_DIR % platform)
    return user_data_dir.resolve()


def _install_runtime_patches(config: MediaCrawlerLoginRuntimeConfig) -> None:
    from .mediacrawler_phone import install_phone_login_patch
    from .mediacrawler_qr import emit_terminal_qr_and_save, install_qr_login_flow_patch
    from .mediacrawler_runtime import install_resilient_navigation_patch

    import tools.crawler_util as crawler_util
    from tools import utils as crawler_utils

    def _patched_show_qrcode(qr_code: str) -> None:
        image_path = emit_terminal_qr_and_save(
            qr_code,
            output_dir=config.qr_output_dir,
            filename_prefix="xhs-login",
        )
        emit_login_runtime_event(
            "qr_ready",
            "qr ready",
            attempt_id=config.attempt_id,
            image_path=str(image_path),
        )

    crawler_util.show_qrcode = _patched_show_qrcode
    crawler_utils.show_qrcode = _patched_show_qrcode
    install_resilient_navigation_patch()
    install_qr_login_flow_patch()
    os.environ["REDNOTE_LOGIN_ATTEMPT_ID"] = str(int(config.attempt_id))
    os.environ["REDNOTE_LOGIN_PLATFORM"] = config.platform
    os.environ.setdefault("REDNOTE_QR_OUTPUT_DIR", str(config.qr_output_dir))
    os.environ.setdefault("REDNOTE_SECURITY_OUTPUT_DIR", str(config.security_output_dir))
    install_phone_login_patch()


def _probe_payload(*, ok: bool, profile_dir: Path) -> dict[str, object]:
    return {
        "ok": bool(ok),
        "probed_at": datetime.now().isoformat(),
        "profile_dir": str(profile_dir),
    }


async def _create_runtime_session(config: MediaCrawlerLoginRuntimeConfig):
    import config as crawler_config
    from media_platform.xhs.core import XiaoHongShuCrawler
    from playwright.async_api import async_playwright

    launch_options = build_browser_launch_options(
        method=config.method,
        prefer_headed=config.prefer_headed,
    )
    profile_dir = _configure_mediacrawler_defaults(
        crawler_config,
        platform=config.platform,
        method=config.method,
        prefer_headed=config.prefer_headed,
    )
    _install_runtime_patches(config)

    crawler = XiaoHongShuCrawler()
    playwright = await async_playwright().start()
    try:
        chromium = playwright.chromium
        crawler.browser_context = await crawler.launch_browser(
            chromium,
            None,
            crawler.user_agent,
            headless=bool(launch_options["headless"]),
        )
        crawler.context_page = await crawler.browser_context.new_page()
        await crawler.context_page.goto(crawler.index_url)
        crawler.xhs_client = await crawler.create_xhs_client(None)
        return crawler, profile_dir, playwright
    except Exception:
        await playwright.stop()
        raise


async def _close_runtime_session(crawler, playwright) -> None:
    try:
        await crawler.close()
    finally:
        await playwright.stop()


async def _run_probe_only(config: MediaCrawlerLoginRuntimeConfig) -> int:
    crawler, profile_dir, playwright = await _create_runtime_session(config)
    try:
        emit_login_runtime_event("probe_started", "probe started", attempt_id=config.attempt_id)
        ok = await crawler.xhs_client.pong()
        emit_login_runtime_event(
            "probe_result",
            "already logged in" if ok else "not logged in",
            attempt_id=config.attempt_id,
            **_probe_payload(ok=ok, profile_dir=profile_dir),
        )
        return 0
    finally:
        await _close_runtime_session(crawler, playwright)


async def _run_login_flow(config: MediaCrawlerLoginRuntimeConfig) -> int:
    crawler, profile_dir, playwright = await _create_runtime_session(config)
    try:
        emit_login_runtime_event("probe_started", "probe started", attempt_id=config.attempt_id)
        ok = await crawler.xhs_client.pong()
        emit_login_runtime_event(
            "probe_result",
            "already logged in" if ok else "not logged in",
            attempt_id=config.attempt_id,
            **_probe_payload(ok=ok, profile_dir=profile_dir),
        )
        if ok:
            emit_login_runtime_event(
                "authenticated",
                "already logged in",
                attempt_id=config.attempt_id,
                **_probe_payload(ok=True, profile_dir=profile_dir),
            )
            return 0

        from media_platform.xhs.login import XiaoHongShuLogin

        login_obj = XiaoHongShuLogin(
            login_type="phone" if config.method == "phone" else "qrcode",
            login_phone=config.phone_number,
            browser_context=crawler.browser_context,
            context_page=crawler.context_page,
            cookie_str="",
        )
        try:
            await login_obj.begin()
        except SystemExit as exc:
            exit_code = int(exc.code or 1)
            emit_login_runtime_event(
                "authentication_failed",
                f"{config.method} login exited with code {exit_code}",
                attempt_id=config.attempt_id,
                **_probe_payload(ok=False, profile_dir=profile_dir),
            )
            return exit_code

        await crawler.xhs_client.update_cookies(browser_context=crawler.browser_context)
        final_ok = await crawler.xhs_client.pong()
        payload = _probe_payload(ok=final_ok, profile_dir=profile_dir)
        if final_ok:
            emit_login_runtime_event(
                "authenticated",
                f"{config.method} login authenticated",
                attempt_id=config.attempt_id,
                **payload,
            )
            return 0
        emit_login_runtime_event(
            "authentication_failed",
            f"{config.method} login finished but final probe failed",
            attempt_id=config.attempt_id,
            **payload,
        )
        return 1
    finally:
        await _close_runtime_session(crawler, playwright)


async def run_runtime(config: MediaCrawlerLoginRuntimeConfig) -> int:
    os.chdir(config.crawler_cwd)
    _ensure_pythonpath(config.crawler_cwd)
    if config.platform != "xhs":
        raise ValueError(f"unsupported platform: {config.platform}")
    if config.method == "probe":
        return await _run_probe_only(config)
    if config.method not in {"qr", "phone"}:
        raise ValueError(f"unsupported method: {config.method}")
    return await _run_login_flow(config)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = build_runtime_config(args)
    try:
        return asyncio.run(run_runtime(config))
    except Exception as exc:  # noqa: BLE001
        emit_login_runtime_event(
            "runtime_failed",
            str(exc),
            attempt_id=config.attempt_id,
        )
        return 1


__all__ = [
    "MediaCrawlerLoginRuntimeConfig",
    "build_parser",
    "build_runtime_config",
    "consume_sms_code_from_database",
    "emit_login_runtime_event",
    "main",
    "map_phone_stage_event",
    "run_runtime",
]
