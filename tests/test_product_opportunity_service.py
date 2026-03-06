from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.models import (
    Base,
    CrawlTask,
    CrawlTaskNote,
    OpportunityDecision,
    OpportunityNoteFailure,
    OpportunityNoteIgnored,
    Product,
    ProductAssessment,
    ProductOpportunity,
    ProductStatus,
    RawComment,
    RawNote,
    TaskStatus,
)
from rednote_spider.opportunity_llm import (
    MatchLLMResult,
    NewProductPayload,
    PrescreenLLMResult,
    ScoreDimensions,
    ScoreLLMResult,
)
from rednote_spider.services.product_opportunity_service import ProductOpportunityService


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "test_product_opportunity.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def _create_task(session: Session, *, keywords: str, status: TaskStatus = TaskStatus.done) -> CrawlTask:
    task = CrawlTask(keywords=keywords, platform="xhs", status=status)
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def _create_note_with_comment(session: Session, *, task_id: int, note_id: str, text: str) -> None:
    session.add(
        RawNote(
            task_id=task_id,
            note_id=note_id,
            title=text[:30],
            content=text,
            likes=10,
            comments_cnt=1,
        )
    )
    session.add(
        RawComment(
            note_id=note_id,
            comment_id=f"{note_id}-c1",
            content=text,
            likes=3,
        )
    )
    session.add(CrawlTaskNote(task_id=task_id, note_id=note_id))
    session.commit()


def _dimensions(score: int) -> ScoreDimensions:
    return ScoreDimensions(**{key: score for key in ScoreDimensions.model_fields})


class FakeOpportunityLLM:
    def __init__(self) -> None:
        self.score_product_calls = 0

    def prescreen(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        prescreen_threshold: float,
    ) -> PrescreenLLMResult:
        text = "\n".join(
            [
                str(note.get("title") or ""),
                str(note.get("content") or ""),
                " ".join(str(row.get("content") or "") for row in comments),
            ]
        )
        if "日常" in text or "没有特别问题" in text:
            score = 2.1
            return PrescreenLLMResult(
                pass_prescreen=score >= prescreen_threshold,
                prescreen_score=score,
                reason="low signal",
            )
        score = 4.4
        return PrescreenLLMResult(
            pass_prescreen=score >= prescreen_threshold,
            prescreen_score=score,
            reason="high signal",
        )

    def match_existing(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        existing_products: list[dict[str, Any]],
        match_threshold: float,  # noqa: ARG002
    ) -> MatchLLMResult:
        text = "\n".join(
            [
                str(note.get("title") or ""),
                str(note.get("content") or ""),
                " ".join(str(row.get("content") or "") for row in comments),
            ]
        )
        for product in existing_products:
            if "租房" in text and "租房" in f"{product.get('name', '')}{product.get('short_description', '')}":
                return MatchLLMResult(
                    decision="matched",
                    matched_product_id=int(product["id"]),
                    reason="matched existing",
                )
        return MatchLLMResult(decision="new", reason="no existing product match")

    def design_product(
        self,
        *,
        note: dict[str, Any],  # noqa: ARG002
        comments: list[dict[str, Any]],  # noqa: ARG002
    ) -> NewProductPayload:
        return NewProductPayload(
            name="通勤效率助手",
            short_description="面向通勤场景，解决反复踩坑和时间浪费问题。",
            full_description="产品定位：通勤人群效率工具。变现：模板+订阅。",
        )

    def score_product(
        self,
        *,
        product: dict[str, Any],  # noqa: ARG002
        supporting_notes: list[dict[str, Any]],  # noqa: ARG002
        supporting_comments: list[dict[str, Any]],  # noqa: ARG002
    ) -> ScoreLLMResult:
        self.score_product_calls += 1
        return ScoreLLMResult(
            personal_fit_score=4.1,
            value_score=4.3,
            competition_opportunity_score=3.9,
            self_control_score=4.0,
            total_score=84.0,
            dimensions=_dimensions(4),
            evidence={"source": "fake-llm"},
        )


class CountingOpportunityLLM(FakeOpportunityLLM):
    def __init__(self) -> None:
        super().__init__()
        self.prescreen_calls = 0

    def prescreen(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        prescreen_threshold: float,
    ) -> PrescreenLLMResult:
        self.prescreen_calls += 1
        return super().prescreen(
            note=note,
            comments=comments,
            prescreen_threshold=prescreen_threshold,
        )


def test_process_task_creates_new_product_for_high_signal(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="通勤")
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-create-1",
            text="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
        )

        llm = FakeOpportunityLLM()
        service = ProductOpportunityService(session, llm=llm)
        summary = service.process_task(task.id, prescreen_threshold=2.5, match_threshold=0.3)

        assert summary.notes_scanned == 1
        assert summary.created == 1
        assert summary.matched == 0
        assert summary.ignored == 0

        product = session.execute(select(Product)).scalar_one()
        assert product.status == ProductStatus.active
        assert "效率助手" in product.name

        opp = session.execute(select(ProductOpportunity)).scalar_one()
        assert opp.decision == OpportunityDecision.created
        assert opp.product_id == product.id
        assert opp.total_score > 0
        assert opp.scores["score_scope"] == "product"
        assert opp.evidence["scoring_scope"] == "product"
        assert "note_excerpt" not in opp.evidence
        assert "comment_samples" not in opp.evidence
        assert opp.evidence["decision_trace"]["note_id"] == "n-create-1"
        assert opp.evidence["mapped_product_id"] == product.id

        assessment = session.execute(select(ProductAssessment)).scalar_one()
        assert assessment.product_id == product.id
        assert llm.score_product_calls == 1


def test_process_task_matches_existing_product(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="租房")
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-match-1",
            text="租房信息太不透明，找房每天反复踩坑很焦虑，求推荐靠谱工具",
        )

        session.add(
            Product(
                name="租房效率助手",
                short_description="面向租房场景，解决信息不透明和踩坑问题",
                full_description="full",
                status=ProductStatus.active,
            )
        )
        session.commit()

        llm = FakeOpportunityLLM()
        service = ProductOpportunityService(session, llm=llm)
        summary = service.process_task(task.id, prescreen_threshold=2.0, match_threshold=0.15)

        assert summary.matched == 1
        assert summary.created == 0

        opp = session.execute(select(ProductOpportunity)).scalar_one()
        assert opp.decision == OpportunityDecision.matched
        assert opp.product_id is not None
        assert llm.score_product_calls == 1


def test_process_task_ignores_low_signal(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="记录")
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-ignore-1",
            text="今天记录一下日常，没有特别问题",
        )

        llm = FakeOpportunityLLM()
        service = ProductOpportunityService(session, llm=llm)
        summary = service.process_task(task.id, prescreen_threshold=4.5, match_threshold=0.3)

        assert summary.ignored == 1
        assert summary.matched == 0
        assert summary.created == 0

        opportunities = session.execute(select(ProductOpportunity)).scalars().all()
        assert opportunities == []
        ignored_rows = session.execute(select(OpportunityNoteIgnored)).scalars().all()
        assert len(ignored_rows) == 1
        assert ignored_rows[0].task_id == task.id
        assert ignored_rows[0].note_id == "n-ignore-1"
        assert ignored_rows[0].prescreen_threshold == 4.5
        assert "low signal" in ignored_rows[0].reason
        assert session.execute(select(Product)).scalars().all() == []
        assert session.execute(select(ProductAssessment)).scalars().all() == []
        assert llm.score_product_calls == 0


def test_process_task_reads_notes_via_task_link_when_note_reused(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        first_task = _create_task(session, keywords="通勤A")
        second_task = _create_task(session, keywords="通勤B")

        _create_note_with_comment(
            session,
            task_id=first_task.id,
            note_id="n-shared-1",
            text="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
        )
        session.add(CrawlTaskNote(task_id=second_task.id, note_id="n-shared-1"))
        session.commit()

        llm = FakeOpportunityLLM()
        service = ProductOpportunityService(session, llm=llm)
        summary = service.process_task(second_task.id, prescreen_threshold=2.0, match_threshold=0.3)

        assert summary.notes_scanned == 1
        assert summary.created == 1
        opp = session.execute(
            select(ProductOpportunity).where(
                ProductOpportunity.task_id == second_task.id,
                ProductOpportunity.note_id == "n-shared-1",
            )
        ).scalar_one()
        assert opp.decision == OpportunityDecision.created
        assert llm.score_product_calls == 1


def test_process_task_scores_same_product_once_for_multiple_notes(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="租房")
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-match-a",
            text="租房信息太不透明，找房每天反复踩坑很焦虑，求推荐靠谱工具",
        )
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-match-b",
            text="租房时总被坑，希望有靠谱工具",
        )

        session.add(
            Product(
                name="租房效率助手",
                short_description="面向租房场景，解决信息不透明和踩坑问题",
                full_description="full",
                status=ProductStatus.active,
            )
        )
        session.commit()

        llm = FakeOpportunityLLM()
        summary = ProductOpportunityService(session, llm=llm).process_task(
            task.id,
            prescreen_threshold=2.0,
            match_threshold=0.15,
        )

        assert summary.matched == 2
        assert llm.score_product_calls == 1
        assert session.execute(select(ProductAssessment)).scalars().all() != []


class CaptureScoreSupportLLM(FakeOpportunityLLM):
    def __init__(self) -> None:
        super().__init__()
        self.supporting_note_sizes: list[int] = []

    def score_product(
        self,
        *,
        product: dict[str, Any],  # noqa: ARG002
        supporting_notes: list[dict[str, Any]],
        supporting_comments: list[dict[str, Any]],  # noqa: ARG002
    ) -> ScoreLLMResult:
        self.supporting_note_sizes.append(len(supporting_notes))
        return super().score_product(
            product=product,
            supporting_notes=supporting_notes,
            supporting_comments=supporting_comments,
        )


def test_process_task_scores_with_historical_supporting_notes(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        session.add(
            Product(
                name="租房效率助手",
                short_description="面向租房场景，解决信息不透明和踩坑问题",
                full_description="full",
                status=ProductStatus.active,
            )
        )
        session.commit()

        first_task = _create_task(session, keywords="租房-首批")
        _create_note_with_comment(
            session,
            task_id=first_task.id,
            note_id="n-his-1",
            text="租房信息太不透明，找房每天反复踩坑很焦虑，求推荐靠谱工具",
        )
        _create_note_with_comment(
            session,
            task_id=first_task.id,
            note_id="n-his-2",
            text="租房总踩坑，求推荐高效查房工具",
        )

        llm = CaptureScoreSupportLLM()
        first_summary = ProductOpportunityService(session, llm=llm).process_task(
            first_task.id,
            prescreen_threshold=2.0,
            match_threshold=0.15,
        )
        assert first_summary.matched == 2
        assert llm.supporting_note_sizes[-1] == 2

        second_task = _create_task(session, keywords="租房-增量")
        _create_note_with_comment(
            session,
            task_id=second_task.id,
            note_id="n-his-3",
            text="租房合同细节总看不懂，求推荐工具",
        )
        second_summary = ProductOpportunityService(session, llm=llm).process_task(
            second_task.id,
            prescreen_threshold=2.0,
            match_threshold=0.15,
        )
        assert second_summary.matched == 1
        # Below 2x threshold: should reuse cached product assessment and skip score_product.
        assert llm.supporting_note_sizes[-1] == 2
        assert llm.score_product_calls == 1
        second_opp = session.execute(
            select(ProductOpportunity).where(ProductOpportunity.note_id == "n-his-3")
        ).scalar_one()
        assert second_opp.scores["score_origin"] == "cached_assessment"


class RegenerateOnGrowthLLM(FakeOpportunityLLM):
    def __init__(self) -> None:
        super().__init__()
        self.design_product_calls = 0

    def match_existing(
        self,
        *,
        note: dict[str, Any],  # noqa: ARG002
        comments: list[dict[str, Any]],  # noqa: ARG002
        existing_products: list[dict[str, Any]],
        match_threshold: float,  # noqa: ARG002
    ) -> MatchLLMResult:
        if existing_products:
            return MatchLLMResult(
                decision="matched",
                matched_product_id=int(existing_products[0]["id"]),
                reason="always match first product",
            )
        return MatchLLMResult(decision="new", reason="create initial product")

    def design_product(
        self,
        *,
        note: dict[str, Any],  # noqa: ARG002
        comments: list[dict[str, Any]],  # noqa: ARG002
    ) -> NewProductPayload:
        self.design_product_calls += 1
        if self.design_product_calls == 1:
            return NewProductPayload(
                name="通勤效率助手初版",
                short_description="初版描述",
                full_description="初版全量描述",
            )
        return NewProductPayload(
            name="通勤效率助手重生成",
            short_description="重生成描述",
            full_description="重生成全量描述",
        )


def test_product_regenerates_when_linked_note_count_doubles(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        first_task = _create_task(session, keywords="通勤-第一批")
        _create_note_with_comment(
            session,
            task_id=first_task.id,
            note_id="n-regen-1",
            text="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
        )

        llm = RegenerateOnGrowthLLM()
        first_summary = ProductOpportunityService(session, llm=llm).process_task(
            first_task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )
        assert first_summary.created == 1
        assert llm.design_product_calls == 1
        assert llm.score_product_calls == 1

        product = session.execute(select(Product)).scalar_one()
        assert product.name == "通勤效率助手初版"
        first_assessment = session.execute(
            select(ProductAssessment).where(ProductAssessment.product_id == product.id)
        ).scalar_one()
        first_lifecycle = first_assessment.evidence["product_lifecycle"]
        assert first_lifecycle["linked_note_count"] == 1
        assert first_lifecycle["generation_note_count"] == 1
        assert first_lifecycle["next_regenerate_at_linked_notes"] == 2
        assert first_lifecycle["regenerated_this_round"] is False

        second_task = _create_task(session, keywords="通勤-第二批")
        _create_note_with_comment(
            session,
            task_id=second_task.id,
            note_id="n-regen-2",
            text="通勤计划总是失效，希望有更好工具",
        )
        second_summary = ProductOpportunityService(session, llm=llm).process_task(
            second_task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )
        assert second_summary.matched == 1
        assert llm.design_product_calls == 2
        assert llm.score_product_calls == 2

        session.refresh(product)
        assert product.name == "通勤效率助手重生成"
        second_assessment = session.execute(
            select(ProductAssessment).where(ProductAssessment.product_id == product.id)
        ).scalar_one()
        second_lifecycle = second_assessment.evidence["product_lifecycle"]
        assert second_lifecycle["linked_note_count"] == 2
        assert second_lifecycle["generation_note_count"] == 2
        assert second_lifecycle["next_regenerate_at_linked_notes"] == 4
        assert second_lifecycle["regenerated_this_round"] is True

        third_task = _create_task(session, keywords="通勤-第三批")
        _create_note_with_comment(
            session,
            task_id=third_task.id,
            note_id="n-regen-3",
            text="通勤场景下想要更稳定的执行提醒",
        )
        third_summary = ProductOpportunityService(session, llm=llm).process_task(
            third_task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )
        assert third_summary.matched == 1
        assert llm.design_product_calls == 2
        assert llm.score_product_calls == 2
        third_opp = session.execute(
            select(ProductOpportunity).where(ProductOpportunity.note_id == "n-regen-3")
        ).scalar_one()
        assert third_opp.scores["score_origin"] == "cached_assessment"

        third_assessment = session.execute(
            select(ProductAssessment).where(ProductAssessment.product_id == product.id)
        ).scalar_one()
        third_lifecycle = third_assessment.evidence["product_lifecycle"]
        assert third_lifecycle["linked_note_count"] == 2
        assert third_lifecycle["generation_note_count"] == 2
        assert third_lifecycle["next_regenerate_at_linked_notes"] == 4
        assert third_lifecycle["regenerated_this_round"] is True


def test_generation_note_count_does_not_read_legacy_root_field(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        first_task = _create_task(session, keywords="通勤-首轮")
        _create_note_with_comment(
            session,
            task_id=first_task.id,
            note_id="n-legacy-root-1",
            text="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
        )

        llm = RegenerateOnGrowthLLM()
        first_summary = ProductOpportunityService(session, llm=llm).process_task(
            first_task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )
        assert first_summary.created == 1
        assert llm.design_product_calls == 1

        product = session.execute(select(Product)).scalar_one()
        assessment = session.execute(
            select(ProductAssessment).where(ProductAssessment.product_id == product.id)
        ).scalar_one()

        # Non-backward-compatible contract: only product_lifecycle.generation_note_count is valid.
        assessment.evidence = {"generation_note_count": 99}
        session.commit()

        second_task = _create_task(session, keywords="通勤-增量")
        _create_note_with_comment(
            session,
            task_id=second_task.id,
            note_id="n-legacy-root-2",
            text="通勤计划总是失效，希望有更好工具",
        )
        second_summary = ProductOpportunityService(session, llm=llm).process_task(
            second_task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )
        assert second_summary.matched == 1
        assert llm.design_product_calls == 2

        updated_assessment = session.execute(
            select(ProductAssessment).where(ProductAssessment.product_id == product.id)
        ).scalar_one()
        lifecycle = updated_assessment.evidence["product_lifecycle"]
        assert lifecycle["generation_note_count"] == 2
        assert lifecycle["regenerated_this_round"] is True


def test_process_recent_done_tasks_only_scans_done(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        done_task = _create_task(session, keywords="通勤", status=TaskStatus.done)
        pending_task = _create_task(session, keywords="租房", status=TaskStatus.pending)

        _create_note_with_comment(
            session,
            task_id=done_task.id,
            note_id="n-done-1",
            text="通勤每天都很麻烦，求推荐更高效方案",
        )
        _create_note_with_comment(
            session,
            task_id=pending_task.id,
            note_id="n-pending-1",
            text="租房太贵",
        )

        service = ProductOpportunityService(session, llm=FakeOpportunityLLM())
        summary = service.process_recent_done_tasks(limit=10, prescreen_threshold=2.0, match_threshold=0.3)

        assert summary.tasks_scanned == 1
        opp_notes = session.execute(
            select(ProductOpportunity.note_id).order_by(ProductOpportunity.note_id)
        ).scalars().all()
        assert opp_notes == ["n-done-1"]


def test_process_recent_done_tasks_respects_failure_backoff(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="退避", status=TaskStatus.done)
        task.note_count = 1
        session.commit()
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-backoff-1",
            text="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
        )
        session.add(
            OpportunityNoteFailure(
                task_id=task.id,
                note_id="n-backoff-1",
                stage="prescreen",
                error_message="transient failure",
                retry_count=2,
                updated_at=datetime.now(),
            )
        )
        session.commit()

        llm = CountingOpportunityLLM()
        first = ProductOpportunityService(session, llm=llm).process_recent_done_tasks(
            limit=10,
            prescreen_threshold=2.0,
            match_threshold=0.3,
            retry_backoff_base_minutes=10,
            retry_backoff_max_minutes=60,
        )
        assert first.tasks_scanned == 0
        assert llm.prescreen_calls == 0

        failure_row = session.execute(select(OpportunityNoteFailure)).scalar_one()
        failure_row.updated_at = datetime.now() - timedelta(minutes=25)
        session.commit()

        second = ProductOpportunityService(session, llm=llm).process_recent_done_tasks(
            limit=10,
            prescreen_threshold=2.0,
            match_threshold=0.3,
            retry_backoff_base_minutes=10,
            retry_backoff_max_minutes=60,
        )
        assert second.tasks_scanned == 1
        assert second.created == 1
        assert llm.prescreen_calls == 1


def test_done_task_without_failures_runs_once_then_is_idempotent(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="幂等-无失败", status=TaskStatus.done)
        task.note_count = 1
        session.commit()
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-noop-1",
            text="今天记录一下日常，没有特别问题",
        )

        llm = CountingOpportunityLLM()
        summary = ProductOpportunityService(session, llm=llm).process_task(
            task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )

        assert summary.tasks_scanned == 1
        assert summary.notes_scanned == 1
        assert summary.ignored == 0
        assert summary.matched == 0
        assert summary.created == 1
        assert summary.failed == 0
        assert llm.prescreen_calls == 1
        assert len(session.execute(select(ProductOpportunity)).scalars().all()) == 1

        second = ProductOpportunityService(session, llm=llm).process_task(
            task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )
        assert second.tasks_scanned == 1
        assert second.notes_scanned == 0
        assert second.created == 0
        assert second.failed == 0
        assert llm.prescreen_calls == 1


def test_done_task_retries_only_failed_notes(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="幂等-失败重试", status=TaskStatus.done)
        task.note_count = 2
        session.commit()
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-failed-retry",
            text="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
        )
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-should-skip",
            text="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
        )
        session.add(
            OpportunityNoteFailure(
                task_id=task.id,
                note_id="n-failed-retry",
                stage="prescreen",
                error_message="transient failure",
                retry_count=1,
            )
        )
        session.commit()

        llm = CountingOpportunityLLM()
        summary = ProductOpportunityService(session, llm=llm).process_task(
            task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )

        assert summary.tasks_scanned == 1
        assert summary.notes_scanned == 1
        assert summary.created == 1
        assert summary.failed == 0
        assert llm.prescreen_calls == 1

        opportunities = session.execute(select(ProductOpportunity).order_by(ProductOpportunity.note_id)).scalars().all()
        assert len(opportunities) == 1
        assert opportunities[0].note_id == "n-failed-retry"
        assert session.execute(select(OpportunityNoteFailure)).scalars().all() == []


class FailOneNoteLLM(FakeOpportunityLLM):
    def prescreen(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        prescreen_threshold: float,
    ) -> PrescreenLLMResult:
        if note.get("note_id") == "n-fail-note":
            raise RuntimeError("simulated prescreen failure")
        return super().prescreen(
            note=note,
            comments=comments,
            prescreen_threshold=prescreen_threshold,
        )


def test_process_task_continues_when_one_note_fails(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="容错")
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-fail-note",
            text="通勤需求，但这条会触发失败",
        )
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-ok-note",
            text="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
        )

        llm = FailOneNoteLLM()
        summary = ProductOpportunityService(session, llm=llm).process_task(
            task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )

        assert summary.notes_scanned == 2
        assert summary.failed == 1
        assert summary.created == 1
        assert summary.matched == 0
        assert summary.ignored == 0

        opportunities = session.execute(select(ProductOpportunity)).scalars().all()
        assert len(opportunities) == 1
        assert opportunities[0].note_id == "n-ok-note"
        failures = session.execute(select(OpportunityNoteFailure)).scalars().all()
        assert len(failures) == 1
        assert failures[0].note_id == "n-fail-note"
        assert failures[0].stage == "prescreen"


class FailOneProductScoreLLM(FakeOpportunityLLM):
    def design_product(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> NewProductPayload:
        note_id = str(note.get("note_id") or "")
        if note_id == "n-score-fail":
            return NewProductPayload(
                name="评分失败产品",
                short_description="评分会失败",
                full_description="用于测试单产品评分失败时不影响整批。",
            )
        return NewProductPayload(
            name="评分成功产品",
            short_description="评分会成功",
            full_description="用于测试单产品评分失败时不影响整批。",
        )

    def score_product(
        self,
        *,
        product: dict[str, Any],
        supporting_notes: list[dict[str, Any]],
        supporting_comments: list[dict[str, Any]],
    ) -> ScoreLLMResult:
        if "失败" in str(product.get("name") or ""):
            raise RuntimeError("simulated score failure")
        return super().score_product(
            product=product,
            supporting_notes=supporting_notes,
            supporting_comments=supporting_comments,
        )


def test_process_task_continues_when_one_product_score_fails(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="评分容错")
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-score-fail",
            text="通勤上班族每天都很焦虑，求推荐模板工具",
        )
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-score-ok",
            text="租房信息太不透明，找房每天反复踩坑很焦虑，求推荐靠谱工具",
        )

        llm = FailOneProductScoreLLM()
        summary = ProductOpportunityService(session, llm=llm).process_task(
            task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )

        assert summary.notes_scanned == 2
        assert summary.failed == 1
        assert summary.created == 1

        assert len(session.execute(select(Product)).scalars().all()) == 2
        assert len(session.execute(select(ProductAssessment)).scalars().all()) == 1
        opportunities = session.execute(select(ProductOpportunity)).scalars().all()
        assert len(opportunities) == 1
        assert opportunities[0].note_id == "n-score-ok"
        failures = session.execute(select(OpportunityNoteFailure)).scalars().all()
        assert len(failures) == 1
        assert failures[0].note_id == "n-score-fail"
        assert failures[0].stage == "score_product"


class FailOnceThenRecoverLLM(FakeOpportunityLLM):
    def __init__(self) -> None:
        super().__init__()
        self.fail_remaining = 3

    def prescreen(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        prescreen_threshold: float,
    ) -> PrescreenLLMResult:
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise RuntimeError("transient prescreen failure")
        return super().prescreen(
            note=note,
            comments=comments,
            prescreen_threshold=prescreen_threshold,
        )


def test_process_task_clears_failure_after_later_success(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task = _create_task(session, keywords="恢复测试")
        _create_note_with_comment(
            session,
            task_id=task.id,
            note_id="n-retry-ok",
            text="通勤上班族每天都很焦虑，求推荐模板工具，反复踩坑，太麻烦了",
        )

        transient_llm = FailOnceThenRecoverLLM()
        first = ProductOpportunityService(session, llm=transient_llm).process_task(
            task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )
        assert first.failed == 1
        failures = session.execute(select(OpportunityNoteFailure)).scalars().all()
        assert len(failures) == 1
        assert failures[0].note_id == "n-retry-ok"

        second = ProductOpportunityService(session, llm=FakeOpportunityLLM()).process_task(
            task.id,
            prescreen_threshold=2.0,
            match_threshold=0.3,
        )
        assert second.created == 1
        assert second.failed == 0
        assert session.execute(select(OpportunityNoteFailure)).scalars().all() == []
