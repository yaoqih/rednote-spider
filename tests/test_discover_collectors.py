from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rednote_spider.discover_collectors import CommandKeywordCollector


def test_command_keyword_collector_reads_notes_payload(tmp_path: Path):
    script = tmp_path / "emit_notes.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "keyword = sys.argv[1]",
                "max_notes = int(sys.argv[2])",
                "rows = [",
                "  {'note_id': f'n-{i+1}', 'title': keyword, 'likes': '2', 'comment_count': 3}",
                "  for i in range(max_notes + 2)",
                "]",
                "print(json.dumps({'notes': rows}, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )

    collector = CommandKeywordCollector(f"{sys.executable} {script} \"{{keywords}}\" {{max_notes}}")
    notes, comments_by_note = collector.collect("宠物 美容", 2)

    assert len(notes) == 2
    assert notes[0]["note_id"] == "n-1"
    assert notes[0]["title"] == "宠物 美容"
    assert notes[0]["likes"] == 2
    assert notes[0]["comments_cnt"] == 3
    assert comments_by_note == {}


def test_command_keyword_collector_attaches_top_level_comments(tmp_path: Path):
    script = tmp_path / "emit_notes_and_comments.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "notes = [{'note_id': 'n-1', 'title': 'x'}]",
                "comments = [",
                "  {'note_id': 'n-1', 'comment_id': 'n-1-c1', 'content': '评论1'},",
                "  {'note_id': 'n-1', 'comment_id': 'n-1-c2', 'content': '评论2'},",
                "]",
                "print(json.dumps({'notes': notes, 'comments': comments}, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )

    collector = CommandKeywordCollector(f"{sys.executable} {script} \"{{keywords}}\" {{max_notes}}")
    notes, comments_by_note = collector.collect("租房", 5)

    assert len(notes) == 1
    assert notes[0]["note_id"] == "n-1"
    assert "comments" not in notes[0]
    assert [item["comment_id"] for item in comments_by_note["n-1"]] == ["n-1-c1", "n-1-c2"]


def test_command_keyword_collector_rejects_list_payload(tmp_path: Path):
    script = tmp_path / "emit_list.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "print(json.dumps([{'title': 'x'}, {'note_id': 'n2', 'title': 'y'}], ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )

    collector = CommandKeywordCollector(f"{sys.executable} {script} \"{{keywords}}\" {{max_notes}}")
    with pytest.raises(ValueError, match="payload must be object"):
        collector.collect("通勤 焦虑", 5)


def test_command_keyword_collector_requires_note_id(tmp_path: Path):
    script = tmp_path / "emit_missing_note_id.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "print(json.dumps({'notes': [{'title': 'x'}]}, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )

    collector = CommandKeywordCollector(f"{sys.executable} {script} \"{{keywords}}\" {{max_notes}}")
    with pytest.raises(ValueError, match="missing note_id"):
        collector.collect("通勤 焦虑", 5)


def test_command_keyword_collector_raises_for_missing_template():
    collector = CommandKeywordCollector("")
    with pytest.raises(ValueError, match="command_template is required"):
        collector.collect("kw", 1)


def test_command_keyword_collector_surfaces_command_stderr(tmp_path: Path):
    script = tmp_path / "fail.py"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "print('crawler failed', file=sys.stderr)",
                "raise SystemExit(2)",
            ]
        ),
        encoding="utf-8",
    )

    collector = CommandKeywordCollector(f"{sys.executable} {script} \"{{keywords}}\" {{max_notes}}")
    with pytest.raises(ValueError, match="crawler failed"):
        collector.collect("kw", 1)


def test_command_keyword_collector_rejects_invalid_json_output(tmp_path: Path):
    script = tmp_path / "invalid_json.py"
    script.write_text("print('not-json')", encoding="utf-8")

    collector = CommandKeywordCollector(f"{sys.executable} {script} \"{{keywords}}\" {{max_notes}}")
    with pytest.raises(ValueError, match="not valid JSON"):
        collector.collect("kw", 1)
