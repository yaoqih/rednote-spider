from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine

from rednote_spider.models import Base


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "verify_live_crawl.py"


def test_verify_live_crawl_script_with_command_backend(tmp_path: Path):
    db_path = tmp_path / "verify_live_crawl.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    emit_script = tmp_path / "emit_payload.py"
    emit_script.write_text(
        "\n".join(
            [
                "import json, sys",
                "max_notes = int(sys.argv[2])",
                "notes = []",
                "comments = []",
                "for i in range(max_notes):",
                "    note_id = f'v-{i+1}'",
                "    notes.append({'note_id': note_id, 'title': 'x', 'content': 'y'})",
                "    comments.append({'note_id': note_id, 'comment_id': f'{note_id}-c1', 'content': 'c'})",
                "print(json.dumps({'notes': notes, 'comments': comments}, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--database-url",
            f"sqlite:///{db_path}",
            "--keywords",
            "通勤 焦虑",
            "--max-notes",
            "3",
            "--command-template",
            f"{sys.executable} {emit_script} \"{{keywords}}\" {{max_notes}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["backend"] == "command"
    assert payload["task_status"] == "done"
    assert payload["task_note_count"] == 3
    assert payload["ingested_note_count"] == 3
    assert payload["ingested_comment_count"] == 3


def test_verify_live_crawl_script_requires_command_template(tmp_path: Path):
    db_path = tmp_path / "verify_live_crawl_requires_command_template.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--database-url",
            f"sqlite:///{db_path}",
            "--max-notes",
            "1",
            "--command-template",
            "",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "--command-template is required" in result.stderr


def test_verify_live_crawl_script_reports_missing_schema(tmp_path: Path):
    db_path = tmp_path / "verify_live_crawl_no_schema.db"
    db_path.touch()

    emit_script = tmp_path / "emit_payload.py"
    emit_script.write_text(
        "import json; print(json.dumps({'notes': [], 'comments': []}))",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_script_path()),
            "--database-url",
            f"sqlite:///{db_path}",
            "--max-notes",
            "1",
            "--command-template",
            f"{sys.executable} {emit_script} \"{{keywords}}\" {{max_notes}}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "database schema is not initialized" in result.stderr
