from __future__ import annotations

import asyncio

from rednote_spider.mediacrawler_runtime import (
    PlaywrightTimeoutError,
    RELAXED_HOME_FALLBACK_WAIT_UNTIL,
    RELAXED_HOME_TIMEOUT_MS,
    RELAXED_HOME_WAIT_UNTIL,
    apply_shared_login_profile_defaults,
    build_browser_launch_options,
    build_relaxed_home_navigation_kwargs,
    goto_with_relaxed_home_navigation,
    is_relaxed_home_navigation,
    normalize_mediacrawler_cli_args,
    should_reexec_with_xvfb,
)


def test_is_relaxed_home_navigation_matches_default_xhs_homepage():
    assert is_relaxed_home_navigation("https://www.xiaohongshu.com/", {})
    assert is_relaxed_home_navigation("https://www.xiaohongshu.com", {"wait_until": "load"})
    assert is_relaxed_home_navigation("https://www.xiaohongshu.com", {"wait_until": None})
    assert not is_relaxed_home_navigation("https://www.xiaohongshu.com/explore", {})
    assert not is_relaxed_home_navigation("https://www.xiaohongshu.com", {"wait_until": "networkidle"})


def test_build_relaxed_home_navigation_kwargs_sets_safer_defaults():
    payload = build_relaxed_home_navigation_kwargs({"timeout": 5_000})

    assert payload["wait_until"] == RELAXED_HOME_WAIT_UNTIL
    assert payload["timeout"] == RELAXED_HOME_TIMEOUT_MS


def test_goto_with_relaxed_home_navigation_retries_with_commit_on_timeout():
    calls: list[dict[str, object]] = []

    async def _run():
        async def fake_goto(page, url, *args, **kwargs):  # noqa: ARG001
            del args
            calls.append({"url": url, **kwargs})
            if len(calls) == 1:
                raise PlaywrightTimeoutError("timed out")
            return "ok"

        return await goto_with_relaxed_home_navigation(
            fake_goto,
            object(),
            "https://www.xiaohongshu.com/",
        )

    result = asyncio.run(_run())

    assert result == "ok"
    assert calls[0]["wait_until"] == RELAXED_HOME_WAIT_UNTIL
    assert calls[0]["timeout"] == RELAXED_HOME_TIMEOUT_MS
    assert calls[1]["wait_until"] == RELAXED_HOME_FALLBACK_WAIT_UNTIL
    assert calls[1]["timeout"] == RELAXED_HOME_TIMEOUT_MS


def test_build_browser_launch_options_prefers_headless_for_probe_without_display():
    payload = build_browser_launch_options(
        method="probe",
        has_display=False,
        prefer_headed=True,
    )

    assert payload["headless"] is True
    assert payload["requires_virtual_display"] is False


def test_build_browser_launch_options_prefers_virtual_display_for_interactive_login_without_display():
    payload = build_browser_launch_options(
        method="phone",
        has_display=False,
        prefer_headed=True,
    )

    assert payload["headless"] is False
    assert payload["requires_virtual_display"] is True


def test_build_browser_launch_options_respects_existing_display():
    payload = build_browser_launch_options(
        method="qr",
        has_display=True,
        prefer_headed=True,
    )

    assert payload["headless"] is False
    assert payload["requires_virtual_display"] is False


def test_normalize_mediacrawler_cli_args_forces_headed_for_interactive_login():
    normalized = normalize_mediacrawler_cli_args(
        [
            "--platform",
            "xhs",
            "--lt",
            "qrcode",
            "--headless",
            "true",
            "--keywords",
            "通勤",
        ]
    )

    assert normalized == [
        "--platform",
        "xhs",
        "--lt",
        "qrcode",
        "--headless",
        "false",
        "--keywords",
        "通勤",
    ]


def test_normalize_mediacrawler_cli_args_appends_headless_false_when_missing():
    normalized = normalize_mediacrawler_cli_args(
        [
            "--platform",
            "xhs",
            "--lt",
            "phone",
        ]
    )

    assert normalized[-2:] == ["--headless", "false"]


def test_should_reexec_with_xvfb_for_interactive_login_without_display():
    assert should_reexec_with_xvfb(
        [
            "--platform",
            "xhs",
            "--lt",
            "qrcode",
        ],
        env={"DISPLAY": "", "WAYLAND_DISPLAY": ""},
        xvfb_run_path="/usr/bin/xvfb-run",
    )


def test_should_not_reexec_with_xvfb_when_display_is_available():
    assert not should_reexec_with_xvfb(
        [
            "--platform",
            "xhs",
            "--lt",
            "qrcode",
        ],
        env={"DISPLAY": ":99", "WAYLAND_DISPLAY": ""},
        xvfb_run_path="/usr/bin/xvfb-run",
    )


def test_apply_shared_login_profile_defaults_prefers_standard_mode():
    class _Config:
        SAVE_LOGIN_STATE = False
        ENABLE_CDP_MODE = True
        ENABLE_IP_PROXY = True
        AUTO_CLOSE_BROWSER = False

    cfg = _Config()

    apply_shared_login_profile_defaults(cfg)

    assert cfg.SAVE_LOGIN_STATE is True
    assert cfg.ENABLE_CDP_MODE is False
    assert cfg.ENABLE_IP_PROXY is False
    assert cfg.AUTO_CLOSE_BROWSER is True


def test_apply_shared_login_profile_defaults_allows_explicit_cdp_opt_in():
    class _Config:
        SAVE_LOGIN_STATE = False
        ENABLE_CDP_MODE = False
        ENABLE_IP_PROXY = True
        AUTO_CLOSE_BROWSER = False

    cfg = _Config()

    apply_shared_login_profile_defaults(cfg, enable_cdp=True)

    assert cfg.SAVE_LOGIN_STATE is True
    assert cfg.ENABLE_CDP_MODE is True
    assert cfg.ENABLE_IP_PROXY is False
    assert cfg.AUTO_CLOSE_BROWSER is True
