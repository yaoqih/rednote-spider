"""Structured events shared by login controller and login-only runtime."""

from __future__ import annotations

import json

LOGIN_EVENT_PREFIX = "[rednote-login]"


def format_login_runtime_event(event_type: str, message: str = "", **payload) -> str:
    event = {
        "event_type": str(event_type or "").strip(),
        "message": str(message or ""),
        "attempt_id": int(payload.pop("attempt_id", 0) or 0),
        "payload": payload,
    }
    return f"{LOGIN_EVENT_PREFIX}{json.dumps(event, ensure_ascii=False, separators=(',', ':'))}"


def parse_login_runtime_event(line: str) -> dict | None:
    raw = (line or "").strip()
    if not raw.startswith(LOGIN_EVENT_PREFIX):
        return None
    try:
        payload = json.loads(raw[len(LOGIN_EVENT_PREFIX):].strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    event_type = str(payload.get("event_type") or "").strip()
    if not event_type:
        return None
    message = str(payload.get("message") or "")
    attempt_id = int(payload.get("attempt_id") or 0)
    nested_payload = payload.get("payload")
    if not isinstance(nested_payload, dict):
        nested_payload = {}
    return {
        "event_type": event_type,
        "message": message,
        "attempt_id": attempt_id,
        "payload": nested_payload,
    }
