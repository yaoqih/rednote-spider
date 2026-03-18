from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.models import Base, CrawlTask, Product, ProductOpportunity, ProductStatus, TaskStatus
from rednote_spider.opportunity_llm import (
    MatchLLMResult,
    NewProductPayload,
    PrescreenLLMResult,
    ScoreDimensions,
    ScoreLLMResult,
)
from rednote_spider.services.manual_task_pipeline_service import ManualTaskPipelineService


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "test_manual_task_pipeline.db"
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
                "    note_id = f'pipeline-{i + 1}'",
                "    notes.append({'note_id': note_id, 'title': keywords, 'content': '通勤很麻烦，想要工具', 'author': 'cmd'})",
                "    comments.append({'note_id': note_id, 'comment_id': f'{note_id}-c1', 'content': '求推荐解决方案', 'author': 'cc'})",
                "print(json.dumps({'notes': notes, 'comments': comments}, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _dimensions(score: int) -> ScoreDimensions:
    return ScoreDimensions(**{key: score for key in ScoreDimensions.model_fields})


class FakeOpportunityLLM:
    def prescreen(
        self,
        *,
        note: dict[str, Any],  # noqa: ARG002
        comments: list[dict[str, Any]],  # noqa: ARG002
        prescreen_threshold: float,
    ) -> PrescreenLLMResult:
        score = 4.5
        return PrescreenLLMResult(
            pass_prescreen=score >= prescreen_threshold,
            prescreen_score=score,
            reason="high signal",
        )

    def match_existing(
        self,
        *,
        note: dict[str, Any],  # noqa: ARG002
        comments: list[dict[str, Any]],  # noqa: ARG002
        existing_products: list[dict[str, Any]],  # noqa: ARG002
        match_threshold: float,  # noqa: ARG002
    ) -> MatchLLMResult:
        return MatchLLMResult(decision="new", reason="always create in test")

    def design_product(
        self,
        *,
        note: dict[str, Any],  # noqa: ARG002
        comments: list[dict[str, Any]],  # noqa: ARG002
    ) -> NewProductPayload:
        return NewProductPayload(
            name="通勤助手",
            short_description="解决通勤场景重复痛点。",
            full_description="测试用新产品。",
        )

    def score_product(
        self,
        *,
        product: dict[str, Any],  # noqa: ARG002
        supporting_notes: list[dict[str, Any]],  # noqa: ARG002
        supporting_comments: list[dict[str, Any]],  # noqa: ARG002
    ) -> ScoreLLMResult:
        return ScoreLLMResult(
            personal_fit_score=4.2,
            value_score=4.3,
            competition_opportunity_score=4.0,
            self_control_score=4.1,
            total_score=86.0,
            dimensions=_dimensions(4),
            evidence={"source": "fake"},
        )


def test_manual_task_pipeline_service_runs_crawl_and_opportunity(tmp_path: Path):
    session_factory = _session_factory(tmp_path)
    script = _emit_payload_script(tmp_path)
    command_template = f'{sys.executable} {script} "{{keywords}}" {{max_notes}}'

    with session_factory() as session:
        task = _create_task(session, "通勤 求推荐")

    result = ManualTaskPipelineService(session_factory, llm=FakeOpportunityLLM()).run(
        task_id=task.id,
        max_notes=2,
        backend="command",
        command_template=command_template,
    )

    assert result.crawl.task_id == task.id
    assert result.crawl.note_count == 2
    assert result.opportunity.notes_scanned == 2
    assert result.opportunity.created == 2
    assert result.opportunity.matched == 0
    assert result.opportunity.ignored == 0
    assert result.opportunity.failed == 0

    with session_factory() as session:
        persisted = session.get(CrawlTask, task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.done

        rows = session.execute(select(ProductOpportunity).where(ProductOpportunity.task_id == task.id)).scalars().all()
        assert len(rows) == 2
        assert all(row.decision.value == "created" for row in rows)
        assert all(row.total_score > 0 for row in rows)
        products = session.execute(select(Product).order_by(Product.id.asc())).scalars().all()
        assert len(products) == 2
        assert all(row.status == ProductStatus.active for row in products)
