from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "run_external_crawler.py"


def test_script_json_file_source_normalizes_payload(tmp_path: Path):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "notes": [
                    {
                        "id": "n1",
                        "title": "标题1",
                        "desc": "内容1",
                        "comments": [{"id": "c1", "text": "评论1"}],
                    },
                    {
                        "note_id": "n2",
                        "content": "内容2",
                        "comment_list": [{"comment_id": "c2", "content": "评论2"}],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--keywords",
            "收纳",
            "--max-notes",
            "10",
            "--source",
            "json-file",
            "--json-file",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    note_ids = [row["note_id"] for row in payload["notes"]]
    comment_ids = [row["comment_id"] for row in payload["comments"]]
    assert note_ids == ["n1", "n2"]
    assert "c1" in comment_ids
    assert "c2" in comment_ids


def test_script_json_file_source_requires_note_id(tmp_path: Path):
    source = tmp_path / "source_missing_note_id.json"
    source.write_text(
        json.dumps(
            {
                "notes": [
                    {
                        "title": "没有 note_id",
                        "desc": "内容1",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--keywords",
            "收纳",
            "--max-notes",
            "10",
            "--source",
            "json-file",
            "--json-file",
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "notes[0].note_id is required" in result.stderr


def test_script_json_file_source_requires_comment_id(tmp_path: Path):
    source = tmp_path / "source_missing_comment_id.json"
    source.write_text(
        json.dumps(
            {
                "notes": [
                    {
                        "note_id": "n1",
                        "title": "标题1",
                        "comments": [{"text": "缺 comment_id"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--keywords",
            "收纳",
            "--max-notes",
            "10",
            "--source",
            "json-file",
            "--json-file",
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "comments[0].comment_id is required" in result.stderr


def test_script_json_dir_source_runs_crawler_cmd_in_cwd(tmp_path: Path):
    crawler_dir = tmp_path / "mock_crawler_repo"
    crawler_dir.mkdir(parents=True, exist_ok=True)

    writer = crawler_dir / "emit_payload.py"
    writer.write_text(
        "\n".join(
            [
                "import json",
                "from pathlib import Path",
                "out = Path('generated/xhs/json')",
                "out.mkdir(parents=True, exist_ok=True)",
                "payload = [{'id': 'mc-1', 'desc': '每天都很麻烦', 'comments': [{'id': 'mc-1-c1', 'text': '太慢了'}]}]",
                "target = out / 'search_contents_test.json'",
                "target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--keywords",
            "通勤",
            "--max-notes",
            "5",
            "--source",
            "json-dir",
            "--json-dir",
            str(crawler_dir / "generated" / "xhs" / "json"),
            "--crawler-cwd",
            str(crawler_dir),
            "--crawler-cmd",
            f"{sys.executable} emit_payload.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert [row["note_id"] for row in payload["notes"]] == ["mc-1"]
    assert [row["comment_id"] for row in payload["comments"]] == ["mc-1-c1"]


def test_script_reports_crawler_cmd_failure(tmp_path: Path):
    failing = tmp_path / "fail.py"
    failing.write_text(
        "\n".join(
            [
                "import sys",
                "print('qrcode not found', file=sys.stderr)",
                "raise SystemExit(2)",
            ]
        ),
        encoding="utf-8",
    )

    source = tmp_path / "source.json"
    source.write_text("[]", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--keywords",
            "通勤",
            "--max-notes",
            "5",
            "--source",
            "json-file",
            "--json-file",
            str(source),
            "--crawler-cmd",
            f"{sys.executable} {failing}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "crawler command failed: qrcode not found" in result.stderr


def test_script_reports_crawler_cmd_failure_with_non_utf8_stderr(tmp_path: Path):
    failing = tmp_path / "fail_non_utf8.py"
    failing.write_text(
        "\n".join(
            [
                "import sys",
                "sys.stderr.buffer.write(b'\\xff\\xfe')",
                "sys.stderr.write('boom')",
                "raise SystemExit(2)",
            ]
        ),
        encoding="utf-8",
    )

    source = tmp_path / "source.json"
    source.write_text("[]", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--keywords",
            "通勤",
            "--max-notes",
            "5",
            "--source",
            "json-file",
            "--json-file",
            str(source),
            "--crawler-cmd",
            f"{sys.executable} {failing}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "crawler command failed:" in result.stderr
    assert "boom" in result.stderr


def test_script_reports_crawler_cmd_timeout(tmp_path: Path):
    slow = tmp_path / "slow.py"
    slow.write_text(
        "\n".join(
            [
                "import time",
                "time.sleep(3)",
            ]
        ),
        encoding="utf-8",
    )

    source = tmp_path / "source.json"
    source.write_text("[]", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--keywords",
            "通勤",
            "--max-notes",
            "5",
            "--source",
            "json-file",
            "--json-file",
            str(source),
            "--crawler-cmd",
            f"{sys.executable} {slow}",
            "--crawler-timeout-seconds",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "crawler command timed out after 1s" in result.stderr


def test_script_json_dir_splits_contents_and_comments(tmp_path: Path):
    json_dir = tmp_path / "xhs" / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    (json_dir / "search_contents_2026-02-21.json").write_text(
        json.dumps(
            [
                {
                    "note_id": "xhs-note-1",
                    "title": "标题",
                    "desc": "内容",
                    "nickname": "作者A",
                    "liked_count": 3,
                    "comment_count": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (json_dir / "search_comments_2026-02-21.json").write_text(
        json.dumps(
            [
                {
                    "comment_id": "xhs-c-1",
                    "note_id": "xhs-note-1",
                    "content": "评论内容",
                    "nickname": "评论者A",
                    "like_count": 2,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--keywords",
            "收纳",
            "--max-notes",
            "10",
            "--source",
            "json-dir",
            "--json-dir",
            str(json_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert [row["note_id"] for row in payload["notes"]] == ["xhs-note-1"]
    assert [row["comment_id"] for row in payload["comments"]] == ["xhs-c-1"]


def test_script_json_dir_with_crawler_cmd_ignores_stale_files(tmp_path: Path):
    crawler_dir = tmp_path / "crawler"
    json_dir = tmp_path / "crawler_output" / "xhs" / "json"
    crawler_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    old_content = json_dir / "search_contents_old.json"
    old_comment = json_dir / "search_comments_old.json"
    old_content.write_text(
        json.dumps([{"note_id": "old-note", "desc": "旧内容"}], ensure_ascii=False),
        encoding="utf-8",
    )
    old_comment.write_text(
        json.dumps([{"comment_id": "old-c", "note_id": "old-note", "content": "旧评论"}], ensure_ascii=False),
        encoding="utf-8",
    )
    old_ts = time.time() - 7200
    os.utime(old_content, (old_ts, old_ts))
    os.utime(old_comment, (old_ts, old_ts))

    writer = crawler_dir / "emit_payload.py"
    writer.write_text(
        "\n".join(
            [
                "import json",
                "from pathlib import Path",
                f"base = Path({str(json_dir)!r})",
                "base.mkdir(parents=True, exist_ok=True)",
                "(base / 'search_contents_new.json').write_text(json.dumps([{'note_id': 'new-note', 'desc': '新内容'}], ensure_ascii=False), encoding='utf-8')",
                "(base / 'search_comments_new.json').write_text(json.dumps([{'comment_id': 'new-c', 'note_id': 'new-note', 'content': '新评论'}], ensure_ascii=False), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--keywords",
            "通勤",
            "--max-notes",
            "5",
            "--source",
            "json-dir",
            "--json-dir",
            str(json_dir),
            "--crawler-cwd",
            str(crawler_dir),
            "--crawler-cmd",
            f"{sys.executable} emit_payload.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert [row["note_id"] for row in payload["notes"]] == ["new-note"]
    assert [row["comment_id"] for row in payload["comments"]] == ["new-c"]
