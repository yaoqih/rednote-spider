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
import shlex
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


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
    return parser


def maybe_run_external_command(
    command_template: str,
    *,
    keywords: str,
    max_notes: int,
    crawler_cwd: str = "",
    timeout_seconds: int = 600,
) -> None:
    template = command_template.strip()
    if not template:
        return
    cwd = resolve_crawler_cwd(crawler_cwd)
    command = template.format(keywords=keywords, max_notes=max_notes)
    process = subprocess.Popen(
        shlex.split(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        cwd=str(cwd) if cwd else None,
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
    if timed_out:
        suffix = f": {detail[:500]}" if detail else ""
        raise RuntimeError(
            f"crawler command timed out after {timeout_seconds}s{suffix}. "
            "可能卡在扫码/验证码，或外部爬虫仍在长时间执行。"
        )
    if process.returncode != 0:
        if not detail:
            detail = f"exit code {process.returncode}"
        raise RuntimeError(f"crawler command failed: {detail}")


def resolve_crawler_cwd(raw_value: str) -> Path | None:
    token = raw_value.strip()
    if not token:
        return None
    path = Path(token).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"crawler cwd not found: {path}")
    return path


def load_source_payload(
    args: argparse.Namespace,
    *,
    generated_after: float | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    if args.source == "json-file":
        if not args.json_file.strip():
            raise ValueError("--json-file is required when --source=json-file")
        path = Path(args.json_file).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"json file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    if args.source == "json-dir":
        if not args.json_dir.strip():
            raise ValueError("--json-dir is required when --source=json-dir")
        base = Path(args.json_dir).expanduser().resolve()
        if not base.exists() or not base.is_dir():
            raise ValueError(f"json dir not found: {base}")
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
