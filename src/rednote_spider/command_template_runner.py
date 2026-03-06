"""Shared command template runner for crawl/discover command backends."""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any

from .config import settings


def run_command_template_json(
    *,
    command_template: str,
    keywords: str,
    max_notes: int,
    error_prefix: str,
    timeout_seconds: int | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    template = (command_template or "").strip()
    if not template:
        raise ValueError("command_template is required")
    if timeout_seconds is None:
        timeout_seconds = max(int(settings.crawl_command_timeout_seconds), 1)
    timeout: int | None = int(timeout_seconds) if int(timeout_seconds) > 0 else None

    command = template.format(keywords=keywords, max_notes=max_notes)
    try:
        proc = subprocess.run(
            shlex.split(command),
            check=True,
            capture_output=True,
            text=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _decode_stream(exc.stderr) or _decode_stream(exc.stdout)
        detail = detail.strip()
        suffix = f": {detail[:400]}" if detail else ""
        timeout_hint = f"{timeout}s" if timeout else "configured limit"
        raise ValueError(
            f"{error_prefix}: command timed out after {timeout_hint}{suffix}. "
            "可能卡在扫码/验证码，或爬虫执行时间过长。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = _decode_stream(exc.stderr) or _decode_stream(exc.stdout)
        detail = detail.strip()
        if not detail:
            detail = str(exc)
        raise ValueError(f"{error_prefix}: {detail}") from exc

    try:
        return json.loads(_decode_stream(proc.stdout))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{error_prefix} output is not valid JSON: {exc}") from exc


def _decode_stream(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("utf-8", errors="replace")
