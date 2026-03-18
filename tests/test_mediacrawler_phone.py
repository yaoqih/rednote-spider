from __future__ import annotations

import asyncio

from rednote_spider.mediacrawler_phone import (
    _click_first_visible,
    determine_phone_login_mode,
    is_security_verification_context,
    _wait_for_first_selector,
    format_phone_stage_marker,
    normalize_phone_number,
    normalize_sms_code,
    parse_phone_stage_marker,
)


def test_normalize_phone_number_accepts_mainland_variants():
    assert normalize_phone_number("+86 138-0013-8000") == "13800138000"


def test_normalize_sms_code_requires_six_digits():
    assert normalize_sms_code(" 123456 ") == "123456"

    try:
        normalize_sms_code("1234")
    except ValueError as exc:
        assert "6 digits" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected invalid sms code to raise")


def test_phone_stage_marker_round_trip():
    marker = format_phone_stage_marker("waiting_code", "sms sent")

    parsed = parse_phone_stage_marker(marker)

    assert parsed == {"stage": "waiting_code", "message": "sms sent"}


class _FakeHandle:
    def __init__(self):
        self.click_count = 0

    async def click(self):
        self.click_count += 1


class _FakeRoot:
    def __init__(self, matches: dict[str, _FakeHandle] | None = None):
        self.matches = matches or {}
        self.calls: list[str] = []

    async def wait_for_selector(self, selector: str, timeout: int = 0):
        del timeout
        self.calls.append(selector)
        handle = self.matches.get(selector)
        if handle is None:
            raise RuntimeError(f"missing selector: {selector}")
        return handle


def test_wait_for_first_selector_searches_all_roots():
    frame_handle = _FakeHandle()
    page = _FakeRoot()
    frame = _FakeRoot({"input[placeholder*='手机号']": frame_handle})

    handle, selector, root = asyncio.run(
        _wait_for_first_selector(
            [page, frame],
            [
                "div.login-container",
                "input[placeholder*='手机号']",
            ],
            timeout_ms=10,
        )
    )

    assert handle is frame_handle
    assert selector == "input[placeholder*='手机号']"
    assert root is frame


def test_click_first_visible_uses_fallback_selector():
    handle = _FakeHandle()
    page = _FakeRoot({"xpath=//button[contains(normalize-space(), '登录')]": handle})

    clicked_handle, selector, root = asyncio.run(
        _click_first_visible(
            [page],
            [
                "xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button",
                "xpath=//button[contains(normalize-space(), '登录')]",
            ],
            timeout_ms=10,
        )
    )

    assert clicked_handle is handle
    assert selector == "xpath=//button[contains(normalize-space(), '登录')]"
    assert root is page
    assert handle.click_count == 1


def test_is_security_verification_context_detects_xhs_security_page():
    assert is_security_verification_context(
        title="Security Verification",
        body_text="Scan with logged-in REDNote APP for account security.",
        frame_urls=[
            "https://www.xiaohongshu.com/website-login/captcha?verifyType=124",
        ],
    )


def test_determine_phone_login_mode_supports_continue_then_code_flow():
    assert (
        determine_phone_login_mode(
            send_button=None,
            sms_code_input=None,
            submit_button=None,
            continue_button=object(),
        )
        == "continue_then_code"
    )


def test_determine_phone_login_mode_supports_direct_sms_flow():
    assert (
        determine_phone_login_mode(
            send_button=object(),
            sms_code_input=object(),
            submit_button=None,
            continue_button=None,
        )
        == "direct_sms"
    )
