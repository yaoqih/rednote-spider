"""Runtime patches shared by MediaCrawler wrapper scripts."""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Mapping, Sequence

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
except Exception:  # noqa: BLE001
    PlaywrightTimeoutError = TimeoutError

XHS_HOME_URL = "https://www.xiaohongshu.com"
RELAXED_HOME_WAIT_UNTIL = "domcontentloaded"
RELAXED_HOME_FALLBACK_WAIT_UNTIL = "commit"
RELAXED_HOME_TIMEOUT_MS = 90_000
INTERACTIVE_LOGIN_TYPES = frozenset({"qrcode", "phone"})


def has_display_server(env: dict[str, str] | None = None) -> bool:
    values = env or os.environ
    return bool(str(values.get("DISPLAY") or "").strip() or str(values.get("WAYLAND_DISPLAY") or "").strip())


def _read_cli_option(argv: Sequence[str], option: str) -> str | None:
    for index, token in enumerate(argv):
        if token == option:
            if index + 1 >= len(argv):
                return None
            return str(argv[index + 1]).strip()
        if token.startswith(f"{option}="):
            return token.split("=", 1)[1].strip()
    return None


def _upsert_cli_option(argv: Sequence[str], option: str, value: str) -> list[str]:
    normalized: list[str] = []
    replaced = False
    index = 0
    while index < len(argv):
        token = str(argv[index])
        if token == option:
            normalized.extend([option, value])
            replaced = True
            if index + 1 < len(argv) and not str(argv[index + 1]).startswith("--"):
                index += 2
            else:
                index += 1
            continue
        if token.startswith(f"{option}="):
            normalized.append(f"{option}={value}")
            replaced = True
            index += 1
            continue
        normalized.append(token)
        index += 1

    if not replaced:
        normalized.extend([option, value])
    return normalized


def _resolve_login_method(argv: Sequence[str]) -> str:
    return (_read_cli_option(argv, "--lt") or "").strip().lower()


def _map_login_type_to_browser_method(login_type: str) -> str:
    if login_type == "qrcode":
        return "qr"
    return login_type


def normalize_mediacrawler_cli_args(argv: Sequence[str]) -> list[str]:
    normalized = [str(token) for token in argv]
    login_type = _resolve_login_method(normalized)
    if login_type not in INTERACTIVE_LOGIN_TYPES:
        return normalized
    return _upsert_cli_option(normalized, "--headless", "false")


def should_reexec_with_xvfb(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    xvfb_run_path: str | None = None,
) -> bool:
    login_type = _resolve_login_method(argv)
    if login_type not in INTERACTIVE_LOGIN_TYPES:
        return False
    display_ready = has_display_server(dict(env) if env is not None else None)
    launch_options = build_browser_launch_options(
        method=_map_login_type_to_browser_method(login_type),
        has_display=display_ready,
        prefer_headed=True,
    )
    return bool(launch_options["requires_virtual_display"] and xvfb_run_path)


def apply_shared_login_profile_defaults(config_obj: Any, *, enable_cdp: bool = False) -> None:
    config_obj.SAVE_LOGIN_STATE = True
    config_obj.ENABLE_CDP_MODE = bool(enable_cdp)
    config_obj.ENABLE_IP_PROXY = False
    config_obj.AUTO_CLOSE_BROWSER = True


def build_browser_launch_options(
    *,
    method: str,
    has_display: bool | None = None,
    prefer_headed: bool = True,
) -> dict[str, bool]:
    interactive_method = str(method or "").strip() in {"qr", "phone"}
    display_ready = has_display_server() if has_display is None else bool(has_display)
    wants_headed = bool(prefer_headed) and interactive_method
    requires_virtual_display = wants_headed and not display_ready
    if str(method or "").strip() == "probe":
        return {"headless": True, "requires_virtual_display": False}
    return {
        "headless": not wants_headed,
        "requires_virtual_display": requires_virtual_display,
    }


def is_relaxed_home_navigation(url: str | None, kwargs: dict[str, Any]) -> bool:
    token = (url or "").rstrip("/")
    wait_until = kwargs.get("wait_until")
    return token == XHS_HOME_URL and wait_until in {None, "", "load"}


def build_relaxed_home_navigation_kwargs(
    kwargs: dict[str, Any],
    *,
    wait_until: str = RELAXED_HOME_WAIT_UNTIL,
    minimum_timeout_ms: int = RELAXED_HOME_TIMEOUT_MS,
) -> dict[str, Any]:
    updated = dict(kwargs)
    updated["wait_until"] = wait_until
    raw_timeout = updated.get("timeout")
    try:
        current_timeout = int(raw_timeout) if raw_timeout is not None else 0
    except (TypeError, ValueError):
        current_timeout = 0
    updated["timeout"] = max(current_timeout, int(minimum_timeout_ms))
    return updated


async def goto_with_relaxed_home_navigation(
    goto_func: Callable[..., Awaitable[Any]],
    page: Any,
    url: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    if not is_relaxed_home_navigation(url, kwargs):
        return await goto_func(page, url, *args, **kwargs)

    primary_kwargs = build_relaxed_home_navigation_kwargs(kwargs)
    try:
        return await goto_func(page, url, *args, **primary_kwargs)
    except PlaywrightTimeoutError:
        fallback_kwargs = build_relaxed_home_navigation_kwargs(
            kwargs,
            wait_until=RELAXED_HOME_FALLBACK_WAIT_UNTIL,
        )
        return await goto_func(page, url, *args, **fallback_kwargs)


def install_resilient_navigation_patch() -> None:
    from playwright.async_api import Page

    if getattr(Page, "_rednote_relaxed_home_patch_installed", False):
        return

    original_goto = Page.goto

    async def _patched_goto(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        return await goto_with_relaxed_home_navigation(
            original_goto,
            self,
            url,
            *args,
            **kwargs,
        )

    Page.goto = _patched_goto
    Page._rednote_relaxed_home_patch_installed = True
