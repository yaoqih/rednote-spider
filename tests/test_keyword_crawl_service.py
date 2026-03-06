from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.models import Base, CrawlTask, ProductOpportunity, RawComment, RawNote, TaskStatus
from rednote_spider.services.keyword_crawl_service import KeywordCrawlService


@pytest.fixture()
def session_factory(tmp_path: Path):
    db_path = tmp_path / "test_keyword_crawl.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def _create_task(session: Session, keywords: str) -> CrawlTask:
    task = CrawlTask(keywords=keywords, platform="xhs", status=TaskStatus.pending)
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def _emit_payload_script(tmp_path: Path) -> Path:
    script = tmp_path / "emit_payload.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "keywords = sys.argv[1]",
                "max_notes = int(sys.argv[2])",
                "notes = []",
                "comments = []",
                "for i in range(max_notes):",
                "    note_id = f'cmd-{i + 1}'",
                "    notes.append({'note_id': note_id, 'title': keywords, 'content': '每天都很麻烦', 'author': 'cmd'})",
                "    comments.append({'note_id': note_id, 'comment_id': f'{note_id}-c1', 'content': '总是很麻烦', 'author': 'cc'})",
                "print(json.dumps({'notes': notes, 'comments': comments}, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )
    return script


def test_run_task_with_command_backend(session_factory, tmp_path: Path):
    script = _emit_payload_script(tmp_path)
    command_template = f'{sys.executable} {script} "{{keywords}}" {{max_notes}}'

    with session_factory() as session:
        task = _create_task(session, "通勤 痛点")
        svc = KeywordCrawlService(session)
        result = svc.run_task(
            task_id=task.id,
            max_notes=3,
            backend="command",
            command_template=command_template,
        )

        assert result.backend == "command"
        assert result.note_count == 3
        assert result.notes_upserted == 3
        assert result.comments_upserted == 3

        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.done
        assert persisted.note_count == 3

        notes = session.execute(select(RawNote)).scalars().all()
        comments = session.execute(select(RawComment)).scalars().all()
        assert len(notes) == 3
        assert len(comments) == 3


def test_run_task_with_external_adapter_and_json_dir(
    session_factory,
    tmp_path: Path,
):
    crawler_dir = tmp_path / "mediacrawler_mock"
    crawler_dir.mkdir(parents=True, exist_ok=True)

    out_dir = tmp_path / "crawler_output"
    writer = crawler_dir / "emit_json.py"
    writer.write_text(
        "\n".join(
            [
                "import json",
                "from pathlib import Path",
                f"base = Path({str(out_dir)!r}) / 'xhs' / 'json'",
                "base.mkdir(parents=True, exist_ok=True)",
                "notes = [{'note_id': 'mc-note-1', 'desc': '每天都很麻烦'}]",
                "comments = [{'comment_id': 'mc-comment-1', 'note_id': 'mc-note-1', 'content': '总是很慢'}]",
                "(base / 'search_contents_test.json').write_text(json.dumps(notes, ensure_ascii=False), encoding='utf-8')",
                "(base / 'search_comments_test.json').write_text(json.dumps(comments, ensure_ascii=False), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    adapter_script = Path(__file__).resolve().parents[1] / "scripts" / "run_external_crawler.py"
    command_template = (
        f"{shlex.quote(sys.executable)} {shlex.quote(str(adapter_script))} "
        "--keywords \"{keywords}\" --max-notes {max_notes} "
        f"--source json-dir --json-dir {shlex.quote(str(out_dir / 'xhs' / 'json'))} "
        f"--crawler-cwd {shlex.quote(str(crawler_dir))} "
        f"--crawler-cmd \"{shlex.quote(sys.executable)} emit_json.py\""
    )
    with session_factory() as session:
        task = _create_task(session, "收纳")
        svc = KeywordCrawlService(session)
        result = svc.run_task(
            task_id=task.id,
            max_notes=1,
            backend="command",
            command_template=command_template,
        )

        assert result.backend == "command"
        assert result.note_count == 1

        notes = session.execute(select(RawNote).order_by(RawNote.note_id)).scalars().all()
        comments = session.execute(select(RawComment).order_by(RawComment.comment_id)).scalars().all()
        assert [row.note_id for row in notes] == ["mc-note-1"]
        assert [row.comment_id for row in comments] == ["mc-comment-1"]


def test_run_task_with_command_backend_surfaces_stderr(session_factory, tmp_path: Path):
    script = tmp_path / "fail_command.py"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "print('qrcode not found', file=sys.stderr)",
                "raise SystemExit(2)",
            ]
        ),
        encoding="utf-8",
    )
    command_template = f'{sys.executable} {script} "{{keywords}}" {{max_notes}}'

    with session_factory() as session:
        task = _create_task(session, "收纳")
        svc = KeywordCrawlService(session)
        with pytest.raises(ValueError, match="qrcode not found"):
            svc.run_task(
                task_id=task.id,
                max_notes=2,
                backend="command",
                command_template=command_template,
            )

        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.failed
        assert persisted.error_message is not None
        assert "qrcode not found" in persisted.error_message


def test_run_task_rejects_non_command_backend(session_factory):
    with session_factory() as session:
        task = _create_task(session, "租房")
        svc = KeywordCrawlService(session)
        with pytest.raises(ValueError, match="only command backend"):
            svc.run_task(task_id=task.id, max_notes=2, backend="unknown", command_template="python -c \"print(1)\"")

        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.failed


def test_run_task_rejects_list_payload_contract(session_factory, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "rednote_spider.services.keyword_crawl_service.run_command_template_json",
        lambda **_: [{"note_id": "legacy-note-1", "title": "legacy"}],
    )

    with session_factory() as session:
        task = _create_task(session, "租房")
        svc = KeywordCrawlService(session)
        with pytest.raises(ValueError, match="object with notes/comments"):
            svc.run_task(
                task_id=task.id,
                max_notes=2,
                backend="command",
                command_template="python -c \"print(1)\"",
            )

        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.failed


def test_run_task_rejects_comment_without_comment_id(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "rednote_spider.services.keyword_crawl_service.run_command_template_json",
        lambda **_: {
            "notes": [{"note_id": "n-1", "title": "通勤痛点"}],
            "comments": [{"note_id": "n-1", "content": "缺 comment_id"}],
        },
    )

    with session_factory() as session:
        task = _create_task(session, "通勤")
        svc = KeywordCrawlService(session)
        with pytest.raises(ValueError, match="comment_id is required"):
            svc.run_task(
                task_id=task.id,
                max_notes=2,
                backend="command",
                command_template="python -c \"print(1)\"",
            )

        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.failed


def test_run_task_marks_failed_when_ingest_payload_invalid(session_factory):
    with session_factory() as session:
        task = _create_task(session, "收纳")
        svc = KeywordCrawlService(session)
        svc._collect_payload = lambda **_: ([{"title": "missing-note-id"}], {})

        with pytest.raises(ValueError, match="note_id"):
            svc.run_task(task_id=task.id, max_notes=1, backend="command", command_template="python -c \"print(1)\"")

        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.failed


def test_run_task_rolls_back_failed_session_and_records_error_message(session_factory):
    with session_factory() as session:
        task = _create_task(session, "收纳")
        svc = KeywordCrawlService(session)
        svc._collect_payload = lambda **_: ([{"note_id": "n1"}], {})

        def broken_ingest(*, task_id: int, notes: list[dict]):  # noqa: ARG001
            # Force a DB NOT NULL error during flush so Session enters failed state.
            session.add(RawNote(task_id=None, note_id="broken-note"))  # type: ignore[arg-type]
            session.flush()
            raise AssertionError("unreachable")

        svc.ingest.ingest_notes = broken_ingest  # type: ignore[method-assign]

        with pytest.raises(Exception):
            svc.run_task(task_id=task.id, max_notes=1, backend="command", command_template="python -c \"print(1)\"")

        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.failed
        assert persisted.error_message is not None


def test_run_task_only_crawls_and_ingests_without_opportunity_stage(session_factory, tmp_path: Path):
    script = _emit_payload_script(tmp_path)
    command_template = f'{sys.executable} {script} "{{keywords}}" {{max_notes}}'

    with session_factory() as session:
        task = _create_task(session, "通勤 痛点")
        svc = KeywordCrawlService(session)
        result = svc.run_task(
            task_id=task.id,
            max_notes=2,
            backend="command",
            command_template=command_template,
        )

        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.done
        assert result.note_count == 2
        assert session.execute(select(ProductOpportunity)).scalars().all() == []
