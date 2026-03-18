from __future__ import annotations

from rednote_spider.login_runtime_events import (
    LOGIN_EVENT_PREFIX,
    format_login_runtime_event,
    parse_login_runtime_event,
)


def test_format_login_runtime_event_wraps_payload():
    event = format_login_runtime_event(
        "qr_ready",
        "qr image saved",
        attempt_id=7,
        image_path="/tmp/qr.png",
    )

    assert event.startswith(LOGIN_EVENT_PREFIX)


def test_parse_login_runtime_event_round_trip():
    raw = format_login_runtime_event(
        "waiting_phone_code",
        "sms requested",
        attempt_id=9,
        phone_number="13800138000",
    )

    parsed = parse_login_runtime_event(raw)

    assert parsed == {
        "event_type": "waiting_phone_code",
        "message": "sms requested",
        "attempt_id": 9,
        "payload": {"phone_number": "13800138000"},
    }
