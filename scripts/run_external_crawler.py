#!/usr/bin/env python3
"""External crawler adapter for command backend.

This script normalizes third-party crawler output into:
{
  "notes": [...],
  "comments": [...],
}
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

LOGIN_EVENT_PREFIX = "[rednote-login]"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize external crawler JSON payload")
    parser.add_argument("--keywords", required=True)
    parser.add_argument("--max-notes", type=int, default=20)
    parser.add_argument(
        "--source",
        choices=["json-file", "json-dir"],
        required=True,
        help="Input source type",
    )
    parser.add_argument("--json-file", default="")
    parser.add_argument("--json-dir", default="")
    parser.add_argument(
        "--crawler-cmd",
        default="",
        help="Optional command executed before parsing. Supports {keywords} and {max_notes}.",
    )
    parser.add_argument(
        "--crawler-cwd",
        default="",
        help="Optional working directory for --crawler-cmd (useful for running external repos like MediaCrawler).",
    )
    parser.add_argument(
        "--crawler-timeout-seconds",
        type=int,
        default=600,
        help="Timeout for --crawler-cmd in seconds; set <=0 to disable timeout.",
    )
    parser.add_argument(
        "--probe-cmd",
        default="",
        help="Optional auth probe command executed before --crawler-cmd. If it emits probe_result ok=false, crawl fails fast.",
    )
    parser.add_argument(
        "--probe-timeout-seconds",
        type=int,
        default=60,
        help="Timeout for --probe-cmd in seconds; set <=0 to disable timeout.",
    )
    return parser


def _run_command(
    command_template: str,
    *,
    keywords: str,
    max_notes: int,
    crawler_cwd: str = "",
    timeout_seconds: int = 600,
) -> tuple[int, bool, str]:
    template = command_template.strip()
    if not template:
        return 0, False, ""
    cwd = resolve_crawler_cwd(crawler_cwd)
    command = template.format(keywords=keywords, max_notes=max_notes)
    process = subprocess.Popen(
        shlex.split(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        cwd=str(cwd) if cwd else None,
        env=_build_child_env(),
    )
    detail_tail: deque[str] = deque(maxlen=300)

    def _forward_stream(pipe: Any) -> None:
        if pipe is None:
            return
        try:
            for raw in iter(lambda: pipe.read(1024), b""):
                chunk = decode_stream(raw)
                if not chunk:
                    continue
                detail_tail.append(chunk)
                print(chunk, end="", file=sys.stderr)
                sys.stderr.flush()
        finally:
            pipe.close()

    threads = [
        threading.Thread(target=_forward_stream, args=(process.stdout,), daemon=True),
        threading.Thread(target=_forward_stream, args=(process.stderr,), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        process.wait(timeout=timeout_seconds if timeout_seconds > 0 else None)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
    finally:
        process.wait()
        for thread in threads:
            thread.join()

    detail = "".join(detail_tail).strip()
    return int(process.returncode or 0), timed_out, detail


def maybe_run_external_command(
    command_template: str,
    *,
    keywords: str,
    max_notes: int,
    crawler_cwd: str = "",
    timeout_seconds: int = 600,
) -> None:
    returncode, timed_out, detail = _run_command(
        command_template,
        keywords=keywords,
        max_notes=max_notes,
        crawler_cwd=crawler_cwd,
        timeout_seconds=timeout_seconds,
    )
    if timed_out:
        suffix = f": {detail[:500]}" if detail else ""
        raise RuntimeError(
            f"crawler command timed out after {timeout_seconds}s{suffix}. "
            "可能卡在扫码/验证码，或外部爬虫仍在长时间执行。"
        )
    if returncode != 0:
        if not detail:
            detail = f"exit code {returncode}"
        raise RuntimeError(f"crawler command failed: {detail}")


def parse_login_runtime_event(line: str) -> dict[str, Any] | None:
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
    nested_payload = payload.get("payload")
    if not isinstance(nested_payload, dict):
        nested_payload = {}
    return {
        "event_type": event_type,
        "message": str(payload.get("message") or ""),
        "attempt_id": int(payload.get("attempt_id") or 0),
        "payload": nested_payload,
    }


def _extract_probe_result(detail: str) -> tuple[bool | None, dict[str, Any] | None]:
    latest_event: dict[str, Any] | None = None
    for raw_line in str(detail or "").splitlines():
        event = parse_login_runtime_event(raw_line)
        if event is None:
            continue
        if event.get("event_type") != "probe_result":
            continue
        latest_event = event
    if latest_event is None:
        return None, None
    payload = latest_event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    return bool(payload.get("ok")), latest_event


def maybe_run_probe_command(
    command_template: str,
    *,
    keywords: str,
    max_notes: int,
    crawler_cwd: str = "",
    timeout_seconds: int = 60,
) -> None:
    template = command_template.strip()
    if not template:
        return
    returncode, timed_out, detail = _run_command(
        template,
        keywords=keywords,
        max_notes=max_notes,
        crawler_cwd=crawler_cwd,
        timeout_seconds=timeout_seconds,
    )
    if timed_out:
        suffix = f": {detail[:500]}" if detail else ""
        raise RuntimeError(f"login probe timed out after {timeout_seconds}s{suffix}")
    if returncode != 0:
        if not detail:
            detail = f"exit code {returncode}"
        raise RuntimeError(f"login probe failed: {detail}")

    ok, event = _extract_probe_result(detail)
    if ok is not False:
        return

    message = str((event or {}).get("message") or "").strip() or "probe reported unauthenticated session"
    payload = (event or {}).get("payload")
    if not isinstance(payload, dict):
        payload = {}
    profile_dir = str(payload.get("profile_dir") or "").strip()
    suffix = f" profile_dir={profile_dir}" if profile_dir else ""
    raise RuntimeError(f"login_required: {message}{suffix}")


def _build_child_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    return env


def resolve_crawler_cwd(raw_value: str) -> Path | None:
    token = raw_value.strip()
    if not token:
        return None
    path = Path(token).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"crawler cwd not found: {path}")
    return path


def resolve_source_path(raw_value: str, *, crawler_cwd: str = "", expected_kind: str) -> Path:
    token = raw_value.strip()
    if not token:
        raise ValueError(f"source path is required for {expected_kind}")

    raw_path = Path(token).expanduser()
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path.resolve())
    else:
        candidates.append(raw_path.resolve())
        crawler_root = resolve_crawler_cwd(crawler_cwd)
        if crawler_root is not None:
            candidates.append((crawler_root / raw_path).resolve())

    for candidate in candidates:
        if not candidate.exists():
            continue
        if expected_kind == "file" and candidate.is_file():
            return candidate
        if expected_kind == "dir" and candidate.is_dir():
            return candidate

    target = candidates[-1] if candidates else raw_path.resolve()
    raise ValueError(f"json {expected_kind} not found: {target}")


def load_source_payload(
    args: argparse.Namespace,
    *,
    generated_after: float | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    if args.source == "json-file":
        if not args.json_file.strip():
            raise ValueError("--json-file is required when --source=json-file")
        path = resolve_source_path(args.json_file, crawler_cwd=args.crawler_cwd, expected_kind="file")
        return json.loads(path.read_text(encoding="utf-8"))

    if args.source == "json-dir":
        if not args.json_dir.strip():
            raise ValueError("--json-dir is required when --source=json-dir")
        base = resolve_source_path(args.json_dir, crawler_cwd=args.crawler_cwd, expected_kind="dir")
        notes: list[dict[str, Any]] = []
        comments: list[dict[str, Any]] = []
        for file in sorted(base.rglob("*.json")):
            if generated_after is not None:
                try:
                    if file.stat().st_mtime + 1.0 < generated_after:
                        continue
                except OSError:
                    continue
            try:
                payload = json.loads(file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            collect_dir_payload(payload, source_file=file, notes=notes, comments=comments)

        if not notes and not comments:
            raise ValueError(f"json dir has no usable records: {base}")
        return {"notes": notes, "comments": comments}

    raise ValueError(f"unsupported source: {args.source}")


def collect_dir_payload(
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    source_file: Path,
    notes: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> None:
    if isinstance(payload, dict):
        if isinstance(payload.get("notes"), list) or isinstance(payload.get("comments"), list):
            for row in payload.get("notes", []):
                if isinstance(row, dict):
                    notes.append(row)
            for row in payload.get("comments", []):
                if isinstance(row, dict):
                    comments.append(row)
            return
        classify_row(payload, file_name=source_file.name, notes=notes, comments=comments)
        return

    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                classify_row(row, file_name=source_file.name, notes=notes, comments=comments)


def classify_row(
    row: dict[str, Any],
    *,
    file_name: str,
    notes: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> None:
    file_type = infer_type_by_file_name(file_name)
    row_type = file_type or infer_type_by_row(row)
    if row_type == "comment":
        comments.append(row)
    else:
        notes.append(row)


def infer_type_by_file_name(file_name: str) -> str | None:
    token = file_name.lower()
    if "comment" in token:
        return "comment"
    if "content" in token or "note" in token:
        return "note"
    return None


def infer_type_by_row(row: dict[str, Any]) -> str:
    if any(key in row for key in ("comment_id", "parent_comment_id", "sub_comment_count")):
        return "comment"
    if "note_id" in row and "create_time" in row and "title" not in row and "desc" not in row:
        return "comment"
    if "note_id" in row and any(
        key in row for key in ("title", "desc", "note_url", "xsec_token", "source_keyword", "type")
    ):
        return "note"
    return "note"


def normalize_payload(
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    max_notes: int,
) -> dict[str, list[dict[str, Any]]]:
    notes: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []

    if isinstance(payload, dict) and isinstance(payload.get("notes"), list):
        for idx, row in enumerate(payload["notes"]):
            if not isinstance(row, dict):
                continue
            note, nested_comments = normalize_note(row, index=idx)
            notes.append(note)
            comments.extend(normalize_comment_rows(nested_comments, note_id=note["note_id"]))
        raw_comments = payload.get("comments")
        if isinstance(raw_comments, list):
            comments.extend(normalize_comment_rows(raw_comments, note_id=None))
    elif isinstance(payload, list):
        for idx, row in enumerate(payload):
            if not isinstance(row, dict):
                continue
            note, nested_comments = normalize_note(row, index=idx)
            notes.append(note)
            comments.extend(normalize_comment_rows(nested_comments, note_id=note["note_id"]))
    elif isinstance(payload, dict):
        note, nested_comments = normalize_note(payload, index=0)
        notes.append(note)
        comments.extend(normalize_comment_rows(nested_comments, note_id=note["note_id"]))
    else:
        raise ValueError("payload must be JSON object or array")

    notes = notes[:max_notes]
    note_ids = {n["note_id"] for n in notes}
    comments = [c for c in comments if c.get("note_id") in note_ids]

    # Deduplicate comment_id to avoid downstream unique conflicts.
    dedup: dict[str, dict[str, Any]] = {}
    for row in comments:
        dedup[row["comment_id"]] = row
    return {"notes": notes, "comments": list(dedup.values())}


def normalize_note(row: dict[str, Any], *, index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    note_id = str(
        first_non_empty(
            row,
            ["note_id", "noteId", "id", "aweme_id", "item_id"],
            default="",
        )
    ).strip()
    if not note_id:
        raise ValueError(f"notes[{index}].note_id is required")

    note = {
        "note_id": note_id,
        "title": first_non_empty(row, ["title", "note_title", "name"], default=""),
        "content": first_non_empty(row, ["content", "desc", "text"], default=""),
        "author": first_non_empty(
            row,
            ["author", "nickname", "user_name", "user"],
            default="",
        ),
        "likes": safe_int(first_non_empty(row, ["likes", "like_count", "liked_count"], default=0)),
        "comments_cnt": safe_int(first_non_empty(row, ["comments_cnt", "comment_count"], default=0)),
        "collected_cnt": safe_int(first_non_empty(row, ["collected_cnt", "collect_count"], default=0)),
        "share_cnt": safe_int(first_non_empty(row, ["share_cnt", "share_count"], default=0)),
        "note_url": first_non_empty(row, ["note_url", "url", "link"], default=""),
        "created_at": first_non_empty(row, ["created_at", "create_time", "publish_time"], default=None),
    }
    nested_comments = []
    for key in ("comments", "comment_list", "commentList"):
        value = row.get(key)
        if isinstance(value, list):
            nested_comments = [v for v in value if isinstance(v, dict)]
            break
    return note, nested_comments


def normalize_comment_rows(rows: list[dict[str, Any]], *, note_id: str | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        c_note_id = str(
            first_non_empty(row, ["note_id", "noteId"], default=note_id or "")
        ).strip()
        if not c_note_id:
            continue

        comment_id = str(first_non_empty(row, ["comment_id", "commentId", "id"], default="")).strip()
        if not comment_id:
            raise ValueError(f"comments[{idx}].comment_id is required")

        normalized.append(
            {
                "note_id": c_note_id,
                "comment_id": comment_id,
                "content": first_non_empty(row, ["content", "text", "desc"], default=""),
                "author": first_non_empty(row, ["author", "nickname", "user_name", "user"], default=""),
                "likes": safe_int(first_non_empty(row, ["likes", "like_count", "liked_count"], default=0)),
                "parent_id": first_non_empty(row, ["parent_id", "parentId", "reply_to"], default=None),
            }
        )
    return normalized


def first_non_empty(data: dict[str, Any], keys: list[str], *, default: Any) -> Any:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def decode_stream(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("utf-8", errors="replace")


def main() -> None:
    args = build_parser().parse_args()
    if args.max_notes < 1:
        raise SystemExit("--max-notes must be >= 1")

    try:
        run_started_at: float | None = None
        maybe_run_probe_command(
            args.probe_cmd,
            keywords=args.keywords,
            max_notes=args.max_notes,
            crawler_cwd=args.crawler_cwd,
            timeout_seconds=int(args.probe_timeout_seconds),
        )
        if args.crawler_cmd.strip():
            run_started_at = time.time()
        maybe_run_external_command(
            args.crawler_cmd,
            keywords=args.keywords,
            max_notes=args.max_notes,
            crawler_cwd=args.crawler_cwd,
            timeout_seconds=int(args.crawler_timeout_seconds),
        )
        payload = load_source_payload(args, generated_after=run_started_at if args.source == "json-dir" else None)
        normalized = normalize_payload(payload, max_notes=args.max_notes)
        print(json.dumps(normalized, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
