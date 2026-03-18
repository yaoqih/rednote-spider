"""Shared command template runner for crawl/discover command backends."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import threading
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
    process = subprocess.Popen(
        shlex.split(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def _consume_stdout() -> None:
        pipe = process.stdout
        if pipe is None:
            return
        try:
            for raw in iter(lambda: pipe.read(4096), b""):
                if raw:
                    stdout_chunks.append(raw)
        finally:
            pipe.close()

    def _consume_stderr() -> None:
        pipe = process.stderr
        if pipe is None:
            return
        try:
            for raw in iter(lambda: pipe.read(4096), b""):
                if not raw:
                    continue
                stderr_chunks.append(raw)
                text = _decode_stream(raw)
                if text:
                    print(text, end="", file=sys.stderr)
                    sys.stderr.flush()
        finally:
            pipe.close()

    stdout_thread = threading.Thread(target=_consume_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_consume_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
    finally:
        process.wait()
        stdout_thread.join()
        stderr_thread.join()

    stdout_value = b"".join(stdout_chunks)
    stderr_value = b"".join(stderr_chunks)

    if timed_out:
        detail = (_decode_stream(stderr_value) or _decode_stream(stdout_value)).strip()
        suffix = f": {detail[:400]}" if detail else ""
        timeout_hint = f"{timeout}s" if timeout else "configured limit"
        raise ValueError(
            f"{error_prefix}: command timed out after {timeout_hint}{suffix}. "
            "可能卡在扫码/验证码，或爬虫执行时间过长。"
        )

    if process.returncode != 0:
        detail = (_decode_stream(stderr_value) or _decode_stream(stdout_value)).strip()
        if not detail:
            detail = f"exit code {process.returncode}"
        raise ValueError(f"{error_prefix}: {detail}")

    try:
        return json.loads(_decode_stream(stdout_value))
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
