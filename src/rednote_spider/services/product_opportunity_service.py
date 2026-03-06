"""Staged LLM-driven product opportunity pipeline."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..models import (
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
from ..opportunity_llm import (
    MatchLLMResult,
    NewProductPayload,
    OpportunityLLM,
    PrescreenLLMResult,
    ScoreLLMResult,
    build_opportunity_llm,
)
from ..observability import get_logger


logger = get_logger(__name__)


@dataclass(slots=True)
class OpportunityRunSummary:
    tasks_scanned: int = 0
    notes_scanned: int = 0
    ignored: int = 0
    matched: int = 0
    created: int = 0
    failed: int = 0


@dataclass(slots=True)
class PendingOpportunity:
    note: RawNote
    comments: list[RawComment]
    decision: OpportunityDecision
    product_id: int
    prescreen: PrescreenLLMResult
    match: MatchLLMResult | None


@dataclass(slots=True)
class OpportunityScoreSnapshot:
    personal_fit_score: float
    value_score: float
    competition_opportunity_score: float
    self_control_score: float
    total_score: float
    dimensions: dict[str, Any]
    evidence: dict[str, Any]
    score_origin: str


class ProductOpportunityService:
    """Evaluate note/comment signals through staged LLM calls and persist decisions."""

    def __init__(self, session: Session, llm: OpportunityLLM | None = None):
        self.session = session
        self.llm = llm or build_opportunity_llm()
        self._failure_table_disabled = False

    def process_recent_done_tasks(
        self,
        *,
        limit: int = 20,
        prescreen_threshold: float = 3.2,
        match_threshold: float = 0.26,
        retry_backoff_base_minutes: int = 5,
        retry_backoff_max_minutes: int = 720,
    ) -> OpportunityRunSummary:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if retry_backoff_base_minutes < 1:
            raise ValueError("retry_backoff_base_minutes must be >= 1")
        if retry_backoff_max_minutes < 1:
            raise ValueError("retry_backoff_max_minutes must be >= 1")

        summary = OpportunityRunSummary()
        now = datetime.now()
        stmt: Select[tuple[CrawlTask]] = (
            select(CrawlTask)
            .where(CrawlTask.status == TaskStatus.done)
            .order_by(CrawlTask.created_at.desc(), CrawlTask.id.desc())
            .limit(limit)
        )
        tasks = self.session.execute(stmt).scalars().all()
        for task in tasks:
            if not self._is_task_due_for_retry(
                task_id=int(task.id),
                now=now,
                retry_backoff_base_minutes=retry_backoff_base_minutes,
                retry_backoff_max_minutes=retry_backoff_max_minutes,
            ):
                continue
            summary.tasks_scanned += 1
            partial = self.process_task(
                task.id,
                prescreen_threshold=prescreen_threshold,
                match_threshold=match_threshold,
            )
            summary.notes_scanned += partial.notes_scanned
            summary.ignored += partial.ignored
            summary.matched += partial.matched
            summary.created += partial.created
            summary.failed += partial.failed
        return summary

    def process_task(
        self,
        task_id: int,
        *,
        prescreen_threshold: float = 3.2,
        match_threshold: float = 0.26,
    ) -> OpportunityRunSummary:
        task = self.session.get(CrawlTask, task_id)
        if task is None:
            raise ValueError(f"task_id={task_id} not found")

        completed_task = task.status == TaskStatus.done and int(task.note_count or 0) > 0
        failed_note_ids = self._list_failed_note_ids(task_id=task_id)
        task_note_ids = self._list_task_note_ids(task_id=task_id)
        processed_note_ids = self._list_processed_note_ids(task_id=task_id)
        eligible_note_ids: set[str] | None = None
        if completed_task:
            if failed_note_ids:
                eligible_note_ids = failed_note_ids
            else:
                eligible_note_ids = task_note_ids - processed_note_ids
                if not eligible_note_ids:
                    return OpportunityRunSummary(tasks_scanned=1)

        note_stmt = (
            select(RawNote)
            .join(CrawlTaskNote, CrawlTaskNote.note_id == RawNote.note_id)
            .where(CrawlTaskNote.task_id == task_id)
        )
        if eligible_note_ids is not None:
            note_stmt = note_stmt.where(RawNote.note_id.in_(eligible_note_ids))
        notes = self.session.execute(
            note_stmt.order_by(CrawlTaskNote.id.asc(), RawNote.id.asc())
        ).scalars().all()
        comments: list[RawComment] = []
        if notes:
            comments = self.session.execute(
                select(RawComment)
                .where(RawComment.note_id.in_([n.note_id for n in notes]))
                .order_by(RawComment.id.asc())
            ).scalars().all()
        comments_by_note: dict[str, list[RawComment]] = {}
        for row in comments:
            comments_by_note.setdefault(row.note_id, []).append(row)

        summary = OpportunityRunSummary(tasks_scanned=1)
        pending: list[PendingOpportunity] = []
        for note in notes:
            if self._is_already_processed(task_id=task_id, note_id=note.note_id):
                self._clear_note_failure(task_id=task_id, note_id=note.note_id)
                continue

            summary.notes_scanned += 1
            try:
                with self.session.begin_nested():
                    note_payload = self._serialize_note(note)
                    comment_payload = self._serialize_comments(comments_by_note.get(note.note_id, []))

                    prescreen = self._llm_call_with_retry(
                        "prescreen",
                        lambda: self.llm.prescreen(
                            note=note_payload,
                            comments=comment_payload,
                            prescreen_threshold=prescreen_threshold,
                        ),
                    )

                    match: MatchLLMResult | None = None
                    designed: NewProductPayload | None = None
                    matched_product: Product | None = None
                    decision = OpportunityDecision.ignored
                    product_id: int | None = None

                    if prescreen.pass_prescreen:
                        product_catalog = self._list_active_products()
                        match = self._llm_call_with_retry(
                            "match_existing",
                            lambda: self.llm.match_existing(
                                note=note_payload,
                                comments=comment_payload,
                                existing_products=product_catalog,
                                match_threshold=match_threshold,
                            ),
                        )
                        if match.decision == "matched":
                            matched_product = self.session.get(Product, match.matched_product_id)
                            if matched_product is None:
                                raise ValueError(f"llm matched unknown product_id={match.matched_product_id}")
                            if matched_product.status != ProductStatus.active:
                                raise ValueError(f"llm matched inactive product_id={match.matched_product_id}")
                            decision = OpportunityDecision.matched
                            product_id = int(matched_product.id)
                        else:
                            designed = self._llm_call_with_retry(
                                "design_product",
                                lambda: self.llm.design_product(
                                    note=note_payload,
                                    comments=comment_payload,
                                ),
                            )
                            created = self._create_product_from_llm(note=note, payload=designed)
                            decision = OpportunityDecision.created
                            product_id = int(created.id)

                    if decision == OpportunityDecision.ignored:
                        self._upsert_ignored_note(
                            task_id=task_id,
                            note_id=note.note_id,
                            prescreen_score=float(prescreen.prescreen_score),
                            prescreen_threshold=float(prescreen_threshold),
                            reason=prescreen.reason,
                        )
                        summary.ignored += 1
                        self._clear_note_failure(task_id=task_id, note_id=note.note_id)
                        continue

                    if product_id is None:
                        raise ValueError("product_id is required when decision is matched/created")
                    pending.append(
                        PendingOpportunity(
                            note=note,
                            comments=comments_by_note.get(note.note_id, []),
                            decision=decision,
                            product_id=product_id,
                            prescreen=prescreen,
                            match=match,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                summary.failed += 1
                logger.warning(
                    "product_opportunity_note_failed",
                    extra={
                        "event": "product_opportunity_note_failed",
                        "task_id": task_id,
                        "note_id": note.note_id,
                        "error": str(exc),
                    },
                )
                self._record_note_failure(
                    task_id=task_id,
                    note_id=note.note_id,
                    stage=self._infer_failure_stage(str(exc)),
                    error_message=str(exc),
                )
                continue

        product_scores, failed_products = self._score_products(pending)
        for row in pending:
            if row.product_id in failed_products:
                summary.failed += 1
                logger.warning(
                    "product_opportunity_score_skipped_note",
                    extra={
                        "event": "product_opportunity_score_skipped_note",
                        "task_id": task_id,
                        "note_id": row.note.note_id,
                        "product_id": row.product_id,
                        "error": failed_products[row.product_id],
                    },
                )
                self._record_note_failure(
                    task_id=task_id,
                    note_id=row.note.note_id,
                    stage="score_product",
                    error_message=failed_products[row.product_id],
                )
                continue

            score = product_scores[row.product_id]
            self._persist_opportunity(
                task_id=task_id,
                note=row.note,
                decision=row.decision,
                product_id=row.product_id,
                prescreen=row.prescreen,
                match=row.match,
                score=score,
            )
            self._clear_note_failure(task_id=task_id, note_id=row.note.note_id)

            if row.decision == OpportunityDecision.matched:
                summary.matched += 1
            else:
                summary.created += 1

        self.session.commit()
        return summary

    def _is_already_processed(self, *, task_id: int, note_id: str) -> bool:
        mapped_row = self.session.execute(
            select(ProductOpportunity.id).where(
                ProductOpportunity.task_id == task_id,
                ProductOpportunity.note_id == note_id,
            )
        ).scalar_one_or_none()
        if mapped_row is not None:
            return True
        ignored_row = self.session.execute(
            select(OpportunityNoteIgnored.id).where(
                OpportunityNoteIgnored.task_id == task_id,
                OpportunityNoteIgnored.note_id == note_id,
            )
        ).scalar_one_or_none()
        return ignored_row is not None

    def _list_task_note_ids(self, *, task_id: int) -> set[str]:
        rows = self.session.execute(
            select(CrawlTaskNote.note_id).where(CrawlTaskNote.task_id == task_id)
        ).scalars().all()
        return {str(note_id) for note_id in rows if str(note_id).strip()}

    def _list_processed_note_ids(self, *, task_id: int) -> set[str]:
        mapped_rows = self.session.execute(
            select(ProductOpportunity.note_id).where(ProductOpportunity.task_id == task_id)
        ).scalars().all()
        ignored_rows = self.session.execute(
            select(OpportunityNoteIgnored.note_id).where(OpportunityNoteIgnored.task_id == task_id)
        ).scalars().all()
        tokens = [*mapped_rows, *ignored_rows]
        return {str(note_id) for note_id in tokens if str(note_id).strip()}

    def _list_failed_note_ids(self, *, task_id: int) -> set[str]:
        rows = self.session.execute(
            select(OpportunityNoteFailure.note_id).where(OpportunityNoteFailure.task_id == task_id)
        ).scalars().all()
        return {str(note_id) for note_id in rows if str(note_id).strip()}

    def _is_task_due_for_retry(
        self,
        *,
        task_id: int,
        now: datetime,
        retry_backoff_base_minutes: int,
        retry_backoff_max_minutes: int,
    ) -> bool:
        failures = self.session.execute(
            select(OpportunityNoteFailure.retry_count, OpportunityNoteFailure.updated_at).where(
                OpportunityNoteFailure.task_id == task_id
            )
        ).all()
        if not failures:
            return True

        due_at: datetime | None = None
        for retry_count, updated_at in failures:
            if updated_at is None:
                return True
            backoff_minutes = self._retry_backoff_minutes(
                retry_count=int(retry_count),
                retry_backoff_base_minutes=retry_backoff_base_minutes,
                retry_backoff_max_minutes=retry_backoff_max_minutes,
            )
            item_due = updated_at + timedelta(minutes=backoff_minutes)
            if due_at is None or item_due > due_at:
                due_at = item_due
        if due_at is None:
            return True
        return now >= due_at

    @staticmethod
    def _retry_backoff_minutes(
        *,
        retry_count: int,
        retry_backoff_base_minutes: int,
        retry_backoff_max_minutes: int,
    ) -> int:
        normalized_retry = max(int(retry_count), 1)
        raw_minutes = retry_backoff_base_minutes * (2 ** (normalized_retry - 1))
        return min(raw_minutes, retry_backoff_max_minutes)

    def _record_note_failure(
        self,
        *,
        task_id: int,
        note_id: str,
        stage: str,
        error_message: str,
    ) -> None:
        if self._failure_table_disabled:
            return
        try:
            with self.session.begin_nested():
                row = self.session.execute(
                    select(OpportunityNoteFailure).where(
                        OpportunityNoteFailure.task_id == task_id,
                        OpportunityNoteFailure.note_id == note_id,
                    )
                ).scalar_one_or_none()
                if row is None:
                    self.session.add(
                        OpportunityNoteFailure(
                            task_id=task_id,
                            note_id=note_id,
                            stage=stage[:64] or "note_pipeline",
                            error_message=(error_message or "")[:4000],
                            retry_count=1,
                        )
                    )
                    return
                row.stage = stage[:64] or "note_pipeline"
                row.error_message = (error_message or "")[:4000]
                row.retry_count = int(row.retry_count) + 1
        except SQLAlchemyError as exc:
            self._failure_table_disabled = True
            logger.warning(
                "opportunity_note_failure_table_unavailable",
                extra={
                    "event": "opportunity_note_failure_table_unavailable",
                    "task_id": task_id,
                    "note_id": note_id,
                    "error": str(exc),
                },
            )

    def _clear_note_failure(self, *, task_id: int, note_id: str) -> None:
        if self._failure_table_disabled:
            return
        try:
            with self.session.begin_nested():
                row = self.session.execute(
                    select(OpportunityNoteFailure).where(
                        OpportunityNoteFailure.task_id == task_id,
                        OpportunityNoteFailure.note_id == note_id,
                    )
                ).scalar_one_or_none()
                if row is not None:
                    self.session.delete(row)
        except SQLAlchemyError as exc:
            self._failure_table_disabled = True
            logger.warning(
                "opportunity_note_failure_table_unavailable",
                extra={
                    "event": "opportunity_note_failure_table_unavailable",
                    "task_id": task_id,
                    "note_id": note_id,
                    "error": str(exc),
                },
            )

    @staticmethod
    def _infer_failure_stage(error_message: str) -> str:
        token = str(error_message or "").split(" failed after", 1)[0].strip().lower()
        known = {"prescreen", "match_existing", "design_product", "score_product"}
        if token in known:
            return token
        return "note_pipeline"

    def _persist_opportunity(
        self,
        *,
        task_id: int,
        note: RawNote,
        decision: OpportunityDecision,
        product_id: int | None,
        prescreen: PrescreenLLMResult,
        match: MatchLLMResult | None,
        score: OpportunityScoreSnapshot,
    ) -> None:
        score_pack = {
            "prescreen_score": float(prescreen.prescreen_score),
            "personal_fit_score": float(score.personal_fit_score),
            "value_score": float(score.value_score),
            "competition_opportunity_score": float(score.competition_opportunity_score),
            "self_control_score": float(score.self_control_score),
            "total_score": float(score.total_score),
            "dimensions": score.dimensions,
            "score_scope": "product",
            "score_origin": score.score_origin,
        }
        evidence = {
            "scoring_scope": "product",
            "mapped_product_id": int(product_id) if product_id is not None else None,
            "decision_trace": {
                "task_id": int(task_id),
                "note_id": note.note_id,
                "prescreen_reason": prescreen.reason,
                "match_reason": match.reason if match else "",
            },
            "product_evidence": score.evidence,
            "score_origin": score.score_origin,
        }
        row = ProductOpportunity(
            task_id=task_id,
            note_id=note.note_id,
            decision=decision,
            product_id=product_id,
            prescreen_score=score_pack["prescreen_score"],
            value_score=score_pack["value_score"],
            competition_opportunity_score=score_pack["competition_opportunity_score"],
            self_control_score=score_pack["self_control_score"],
            total_score=score_pack["total_score"],
            scores=score_pack,
            evidence=evidence,
        )
        self.session.add(row)
        self._delete_ignored_note(task_id=task_id, note_id=note.note_id)

    def _upsert_ignored_note(
        self,
        *,
        task_id: int,
        note_id: str,
        prescreen_score: float,
        prescreen_threshold: float,
        reason: str,
    ) -> None:
        row = self.session.execute(
            select(OpportunityNoteIgnored).where(
                OpportunityNoteIgnored.task_id == task_id,
                OpportunityNoteIgnored.note_id == note_id,
            )
        ).scalar_one_or_none()
        if row is None:
            self.session.add(
                OpportunityNoteIgnored(
                    task_id=task_id,
                    note_id=note_id,
                    prescreen_score=float(prescreen_score),
                    prescreen_threshold=float(prescreen_threshold),
                    reason=(reason or "")[:4000],
                )
            )
            return
        row.prescreen_score = float(prescreen_score)
        row.prescreen_threshold = float(prescreen_threshold)
        row.reason = (reason or "")[:4000]

    def _delete_ignored_note(self, *, task_id: int, note_id: str) -> None:
        row = self.session.execute(
            select(OpportunityNoteIgnored).where(
                OpportunityNoteIgnored.task_id == task_id,
                OpportunityNoteIgnored.note_id == note_id,
            )
        ).scalar_one_or_none()
        if row is not None:
            self.session.delete(row)

    def _score_products(
        self,
        pending: list[PendingOpportunity],
    ) -> tuple[dict[int, OpportunityScoreSnapshot], dict[int, str]]:
        if not pending:
            return {}, {}

        grouped: dict[int, list[PendingOpportunity]] = defaultdict(list)
        for row in pending:
            grouped[row.product_id].append(row)

        result: dict[int, OpportunityScoreSnapshot] = {}
        failed: dict[int, str] = {}
        for product_id, rows in grouped.items():
            try:
                with self.session.begin_nested():
                    product = self.session.get(Product, product_id)
                    if product is None:
                        raise ValueError(f"product_id={product_id} not found for scoring")

                    existing_linked_notes = self._count_product_linked_notes(product_id)
                    note_count_after = int(existing_linked_notes + len(rows))
                    assessment = self.session.execute(
                        select(ProductAssessment).where(ProductAssessment.product_id == product_id)
                    ).scalar_one_or_none()
                    generation_note_count = (
                        note_count_after
                        if assessment is None
                        else self._extract_generation_note_count(
                            assessment=assessment,
                            fallback=max(existing_linked_notes, 1),
                        )
                    )
                    should_regenerate = (
                        assessment is not None
                        and generation_note_count > 0
                        and note_count_after >= generation_note_count * 2
                    )
                    should_score = assessment is None or should_regenerate

                    # Keep previous assessment score when not reaching regenerate threshold.
                    if not should_score:
                        result[product_id] = self._score_snapshot_from_assessment(assessment)
                        continue

                    pending_supporting_notes = [
                        self._build_supporting_note_payload(
                            note=item.note,
                            prescreen=item.prescreen,
                            match=item.match,
                        )
                        for item in rows[:40]
                    ]
                    pending_supporting_comments = self._build_supporting_comments(rows)
                    historical_notes, historical_comments = self._load_existing_product_support(
                        product_id=product_id,
                        exclude_note_ids={item.note.note_id for item in rows},
                    )
                    supporting_notes = self._merge_supporting_notes(
                        pending_notes=pending_supporting_notes,
                        historical_notes=historical_notes,
                        limit=40,
                    )
                    supporting_comments = self._merge_supporting_comments(
                        pending_comments=pending_supporting_comments,
                        historical_comments=historical_comments,
                        limit=80,
                    )

                    regenerated_this_round = False
                    if should_regenerate:
                        redesigned = self._llm_call_with_retry(
                            "design_product",
                            lambda: self.llm.design_product(
                                note=self._build_regeneration_note_payload(
                                    product=product,
                                    supporting_notes=supporting_notes,
                                ),
                                comments=supporting_comments[:20],
                            ),
                        )
                        self._refresh_product_from_llm(product=product, payload=redesigned)
                        generation_note_count = note_count_after
                        regenerated_this_round = True

                    score = self._llm_call_with_retry(
                        "score_product",
                        lambda: self.llm.score_product(
                            product=self._serialize_product(product),
                            supporting_notes=supporting_notes,
                            supporting_comments=supporting_comments,
                        ),
                    )
                    self._upsert_product_assessment(
                        product_id=product_id,
                        row=assessment,
                        score=score,
                        supporting_notes=supporting_notes,
                        linked_note_count=note_count_after,
                        generation_note_count=generation_note_count,
                        regenerated_this_round=regenerated_this_round,
                    )
                    result[product_id] = self._score_snapshot_from_llm(
                        score,
                        score_origin="triggered_refresh" if should_regenerate else "initial_assessment",
                    )
            except Exception as exc:  # noqa: BLE001
                failed[product_id] = str(exc)
                logger.warning(
                    "product_opportunity_product_score_failed",
                    extra={
                        "event": "product_opportunity_product_score_failed",
                        "product_id": product_id,
                        "notes_count": len(rows),
                        "error": str(exc),
                    },
                )
        return result, failed

    def _upsert_product_assessment(
        self,
        *,
        product_id: int,
        row: ProductAssessment | None,
        score: ScoreLLMResult,
        supporting_notes: list[dict],
        linked_note_count: int,
        generation_note_count: int,
        regenerated_this_round: bool,
    ) -> None:
        score_pack = {
            "personal_fit_score": float(score.personal_fit_score),
            "value_score": float(score.value_score),
            "competition_opportunity_score": float(score.competition_opportunity_score),
            "self_control_score": float(score.self_control_score),
            "total_score": float(score.total_score),
            "dimensions": score.dimensions.model_dump(),
        }
        evidence = {
            "llm_evidence": score.evidence,
            "supporting_note_samples": [
                {
                    "note_id": n.get("note_id", ""),
                    "title": n.get("title", ""),
                    "prescreen_score": n.get("prescreen_score", 0),
                }
                for n in supporting_notes[:5]
            ],
            "product_lifecycle": {
                "linked_note_count": int(linked_note_count),
                "generation_note_count": int(max(generation_note_count, 1)),
                "next_regenerate_at_linked_notes": int(max(generation_note_count, 1) * 2),
                "regenerated_this_round": bool(regenerated_this_round),
            },
        }
        if row is None:
            row = ProductAssessment(
                product_id=product_id,
                personal_fit_score=score_pack["personal_fit_score"],
                value_score=score_pack["value_score"],
                competition_opportunity_score=score_pack["competition_opportunity_score"],
                self_control_score=score_pack["self_control_score"],
                total_score=score_pack["total_score"],
                scores=score_pack,
                evidence=evidence,
            )
            self.session.add(row)
            return

        row.personal_fit_score = score_pack["personal_fit_score"]
        row.value_score = score_pack["value_score"]
        row.competition_opportunity_score = score_pack["competition_opportunity_score"]
        row.self_control_score = score_pack["self_control_score"]
        row.total_score = score_pack["total_score"]
        row.scores = score_pack
        row.evidence = evidence

    @staticmethod
    def _score_snapshot_from_llm(
        score: ScoreLLMResult,
        *,
        score_origin: str,
    ) -> OpportunityScoreSnapshot:
        return OpportunityScoreSnapshot(
            personal_fit_score=float(score.personal_fit_score),
            value_score=float(score.value_score),
            competition_opportunity_score=float(score.competition_opportunity_score),
            self_control_score=float(score.self_control_score),
            total_score=float(score.total_score),
            dimensions=score.dimensions.model_dump(),
            evidence=score.evidence if isinstance(score.evidence, dict) else {},
            score_origin=score_origin,
        )

    @staticmethod
    def _score_snapshot_from_assessment(assessment: ProductAssessment) -> OpportunityScoreSnapshot:
        score_payload = assessment.scores if isinstance(assessment.scores, dict) else {}
        dimensions = score_payload.get("dimensions") if isinstance(score_payload, dict) else {}
        if not isinstance(dimensions, dict):
            dimensions = {}
        raw_evidence = assessment.evidence if isinstance(assessment.evidence, dict) else {}
        llm_evidence = raw_evidence.get("llm_evidence") if isinstance(raw_evidence, dict) else {}
        evidence = llm_evidence if isinstance(llm_evidence, dict) else {}
        return OpportunityScoreSnapshot(
            personal_fit_score=float(assessment.personal_fit_score),
            value_score=float(assessment.value_score),
            competition_opportunity_score=float(assessment.competition_opportunity_score),
            self_control_score=float(assessment.self_control_score),
            total_score=float(assessment.total_score),
            dimensions=dimensions,
            evidence=evidence,
            score_origin="cached_assessment",
        )

    def _create_product_from_llm(self, *, note: RawNote, payload: NewProductPayload) -> Product:
        name = self._next_available_product_name(payload.name.strip())
        row = Product(
            name=name,
            short_description=payload.short_description.strip(),
            full_description=payload.full_description.strip(),
            status=ProductStatus.active,
            source_keyword=(note.title or "")[:255] or None,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _refresh_product_from_llm(self, *, product: Product, payload: NewProductPayload) -> None:
        desired_name = payload.name.strip() or product.name
        if desired_name != product.name:
            product.name = self._next_available_product_name(
                desired_name,
                exclude_product_id=int(product.id),
            )
        product.short_description = payload.short_description.strip() or product.short_description
        product.full_description = payload.full_description.strip() or product.full_description

    def _next_available_product_name(self, base_name: str, *, exclude_product_id: int | None = None) -> str:
        seed = base_name or "新产品"
        rows = self.session.execute(
            select(Product.id, Product.name).where(Product.name.like(f"{seed}%"))
        ).all()
        existing = {
            name
            for row_id, name in rows
            if exclude_product_id is None or int(row_id) != int(exclude_product_id)
        }
        if seed not in existing:
            return seed
        suffix = 2
        while True:
            candidate = f"{seed}-{suffix}"
            if candidate not in existing:
                return candidate
            suffix += 1

    def _count_product_linked_notes(self, product_id: int) -> int:
        rows = self.session.execute(
            select(ProductOpportunity.note_id)
            .where(ProductOpportunity.product_id == product_id)
            .distinct()
        ).scalars().all()
        return len(rows)

    def _load_existing_product_support(
        self,
        *,
        product_id: int,
        exclude_note_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        note_rows = self.session.execute(
            select(ProductOpportunity, RawNote)
            .join(RawNote, RawNote.note_id == ProductOpportunity.note_id)
            .where(ProductOpportunity.product_id == product_id)
            .order_by(ProductOpportunity.created_at.desc(), ProductOpportunity.id.desc())
        ).all()

        supporting_notes: list[dict[str, Any]] = []
        selected_note_ids: list[str] = []
        seen_note_ids: set[str] = set()
        for opp, note in note_rows:
            note_id = note.note_id
            if note_id in exclude_note_ids or note_id in seen_note_ids:
                continue
            evidence = opp.evidence if isinstance(opp.evidence, dict) else {}
            decision_trace = evidence.get("decision_trace") if isinstance(evidence, dict) else {}
            trace = decision_trace if isinstance(decision_trace, dict) else {}
            supporting_notes.append(
                {
                    "note_id": note_id,
                    "title": note.title or "",
                    "content_excerpt": (note.content or "")[:500],
                    "likes": note.likes,
                    "comments_cnt": note.comments_cnt,
                    "collected_cnt": note.collected_cnt,
                    "share_cnt": note.share_cnt,
                    "prescreen_score": float(opp.prescreen_score),
                    "prescreen_reason": str(trace.get("prescreen_reason") or ""),
                    "match_reason": str(trace.get("match_reason") or ""),
                }
            )
            seen_note_ids.add(note_id)
            selected_note_ids.append(note_id)
            if len(supporting_notes) >= 40:
                break

        supporting_comments: list[dict[str, Any]] = []
        if selected_note_ids:
            comment_rows = self.session.execute(
                select(RawComment)
                .where(RawComment.note_id.in_(selected_note_ids))
                .order_by(RawComment.id.asc())
            ).scalars().all()
            for comment in comment_rows:
                supporting_comments.append(
                    {
                        "note_id": comment.note_id,
                        "comment_id": comment.comment_id,
                        "content": comment.content or "",
                        "author": comment.author or "",
                        "likes": comment.likes,
                        "parent_id": comment.parent_id or "",
                    }
                )
                if len(supporting_comments) >= 80:
                    break

        return supporting_notes, supporting_comments

    @staticmethod
    def _merge_supporting_notes(
        *,
        pending_notes: list[dict[str, Any]],
        historical_notes: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in pending_notes + historical_notes:
            note_id = str(row.get("note_id") or "")
            if not note_id or note_id in seen:
                continue
            merged.append(row)
            seen.add(note_id)
            if len(merged) >= limit:
                break
        return merged

    @staticmethod
    def _merge_supporting_comments(
        *,
        pending_comments: list[dict[str, Any]],
        historical_comments: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in pending_comments + historical_comments:
            comment_id = str(row.get("comment_id") or "")
            if not comment_id or comment_id in seen:
                continue
            merged.append(row)
            seen.add(comment_id)
            if len(merged) >= limit:
                break
        return merged

    def _extract_generation_note_count(
        self,
        *,
        assessment: ProductAssessment,
        fallback: int,
    ) -> int:
        evidence = assessment.evidence if isinstance(assessment.evidence, dict) else {}
        lifecycle = evidence.get("product_lifecycle") if isinstance(evidence, dict) else {}
        if isinstance(lifecycle, dict):
            value = lifecycle.get("generation_note_count")
            if isinstance(value, (int, float)) and int(value) > 0:
                return int(value)
        return max(int(fallback), 1)

    @staticmethod
    def _build_regeneration_note_payload(
        *,
        product: Product,
        supporting_notes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snippets: list[str] = []
        for row in supporting_notes[:12]:
            title = str(row.get("title") or "").strip()
            excerpt = str(row.get("content_excerpt") or "").strip()
            if title:
                snippets.append(f"标题：{title}")
            if excerpt:
                snippets.append(f"内容：{excerpt[:160]}")
        merged_content = "\n".join(snippets)
        return {
            "note_id": f"regen-product-{product.id}",
            "title": f"{product.name} 需求聚合重生成",
            "content": merged_content,
            "author": "system",
            "likes": 0,
            "comments_cnt": 0,
            "collected_cnt": 0,
            "share_cnt": 0,
            "note_url": "",
        }

    def _list_active_products(self) -> list[dict]:
        rows = self.session.execute(
            select(Product).where(Product.status == ProductStatus.active).order_by(Product.id.asc())
        ).scalars().all()
        return [self._serialize_product_summary(row) for row in rows]

    @staticmethod
    def _serialize_note(note: RawNote) -> dict:
        return {
            "note_id": note.note_id,
            "title": note.title or "",
            "content": note.content or "",
            "author": note.author or "",
            "likes": note.likes,
            "comments_cnt": note.comments_cnt,
            "collected_cnt": note.collected_cnt,
            "share_cnt": note.share_cnt,
            "note_url": note.note_url or "",
        }

    @staticmethod
    def _serialize_comments(comments: list[RawComment]) -> list[dict]:
        return [
            {
                "comment_id": row.comment_id,
                "content": row.content or "",
                "author": row.author or "",
                "likes": row.likes,
                "parent_id": row.parent_id or "",
            }
            for row in comments[:20]
        ]

    def _build_supporting_comments(self, rows: list[PendingOpportunity]) -> list[dict]:
        output: list[dict] = []
        for row in rows:
            for comment in row.comments[:20]:
                output.append(
                    {
                        "note_id": row.note.note_id,
                        "comment_id": comment.comment_id,
                        "content": comment.content or "",
                        "author": comment.author or "",
                        "likes": comment.likes,
                        "parent_id": comment.parent_id or "",
                    }
                )
                if len(output) >= 80:
                    return output
        return output

    @staticmethod
    def _build_supporting_note_payload(
        *,
        note: RawNote,
        prescreen: PrescreenLLMResult,
        match: MatchLLMResult | None,
    ) -> dict:
        return {
            "note_id": note.note_id,
            "title": note.title or "",
            "content_excerpt": (note.content or "")[:500],
            "likes": note.likes,
            "comments_cnt": note.comments_cnt,
            "collected_cnt": note.collected_cnt,
            "share_cnt": note.share_cnt,
            "prescreen_score": float(prescreen.prescreen_score),
            "prescreen_reason": prescreen.reason,
            "match_reason": match.reason if match else "",
        }

    @staticmethod
    def _serialize_product(product: Product) -> dict:
        return {
            "id": int(product.id),
            "name": product.name,
            "short_description": product.short_description,
            "full_description": product.full_description,
        }

    @staticmethod
    def _serialize_product_summary(product: Product) -> dict:
        return {
            "id": int(product.id),
            "name": product.name,
            "short_description": product.short_description,
        }

    @staticmethod
    def _llm_call_with_retry(call_name: str, fn, *, max_attempts: int = 3):  # noqa: ANN001
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < max_attempts:
                    sleep(min(4, 2 ** (attempt - 1)))
        raise ValueError(f"{call_name} failed after {max_attempts} attempts: {last_exc}")
