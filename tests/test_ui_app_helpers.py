from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.models import (
    Base,
    CrawlTask,
    OpportunityDecision,
    Product,
    ProductAssessment,
    ProductOpportunity,
    ProductStatus,
    RawNote,
    SchedulerRuntimeConfig,
    TaskStatus,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.app import (
    _fetch_product_overview,
    _fetch_scheduler_configs,
    _load_login_panel_state,
    _login_action_enabled,
    _login_auth_state_message,
    _login_flow_state_message,
    _resolve_result_products_top_n,
    _scheduler_service_supports_note_limit,
)


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "ui_helpers.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def _create_task(session: Session, keywords: str) -> CrawlTask:
    task = CrawlTask(keywords=keywords, platform="xhs", status=TaskStatus.done, note_count=2)
    session.add(task)
    session.flush()
    return task


def _create_note(session: Session, *, task_id: int, note_id: str, title: str) -> RawNote:
    note = RawNote(
        task_id=task_id,
        note_id=note_id,
        title=title,
        content=f"{title} content",
        author="tester",
    )
    session.add(note)
    session.flush()
    return note


def test_fetch_scheduler_configs_exposes_discover_note_limit(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        session.add(
            SchedulerRuntimeConfig(
                mode="discover",
                enabled=True,
                loop_interval_seconds=120,
                note_limit=11,
            )
        )
        session.add(
            SchedulerRuntimeConfig(
                mode="opportunity",
                enabled=False,
                loop_interval_seconds=300,
            )
        )
        session.commit()

    rows = _fetch_scheduler_configs(sf)

    assert rows[0]["mode"] == "discover"
    assert rows[0]["enabled"] is True
    assert rows[0]["loop_interval_seconds"] == 120
    assert rows[0]["note_limit"] == 11
    assert len(rows) == 1


def test_fetch_product_overview_returns_global_product_summary_and_distinct_linked_notes(tmp_path: Path):
    sf = _session_factory(tmp_path)
    with sf() as session:
        task_a = _create_task(session, "通勤")
        task_b = _create_task(session, "副业")

        _create_note(session, task_id=task_a.id, note_id="note-1", title="通勤助手")
        _create_note(session, task_id=task_a.id, note_id="note-2", title="效率工具")
        _create_note(session, task_id=task_b.id, note_id="note-3", title="副业工具")

        product_a = Product(
            name="通勤助手",
            short_description="帮助通勤用户降本提效。",
            full_description="desc-a",
            status=ProductStatus.active,
            source_keyword="通勤",
        )
        product_b = Product(
            name="副业助手",
            short_description="帮助副业探索。",
            full_description="desc-b",
            status=ProductStatus.active,
            source_keyword="副业",
        )
        product_c = Product(
            name="历史产品",
            short_description="历史归档。",
            full_description="desc-c",
            status=ProductStatus.archived,
            source_keyword="历史",
        )
        session.add_all([product_a, product_b, product_c])
        session.flush()

        session.add(
            ProductAssessment(
                product_id=product_a.id,
                personal_fit_score=4.6,
                value_score=4.5,
                competition_opportunity_score=4.2,
                self_control_score=4.4,
                total_score=91.0,
                scores={"dimensions": {"personal_fit_score": 4.6}},
                evidence={
                    "llm_evidence": {"summary": "high confidence"},
                    "product_lifecycle": {
                        "linked_note_count": 2,
                        "generation_note_count": 2,
                        "next_regenerate_at_linked_notes": 4,
                        "regenerated_this_round": False,
                    },
                },
            )
        )
        session.add(
            ProductAssessment(
                product_id=product_b.id,
                personal_fit_score=3.8,
                value_score=3.9,
                competition_opportunity_score=3.7,
                self_control_score=3.6,
                total_score=78.0,
                scores={"dimensions": {"personal_fit_score": 3.8}},
                evidence={
                    "llm_evidence": {"summary": "medium confidence"},
                    "product_lifecycle": {
                        "linked_note_count": 1,
                        "generation_note_count": 1,
                        "next_regenerate_at_linked_notes": 2,
                        "regenerated_this_round": True,
                    },
                },
            )
        )
        session.add_all(
            [
                ProductOpportunity(
                    task_id=task_a.id,
                    note_id="note-1",
                    decision=OpportunityDecision.created,
                    product_id=product_a.id,
                    prescreen_score=4.5,
                    value_score=4.5,
                    competition_opportunity_score=4.4,
                    self_control_score=4.3,
                    total_score=91.0,
                    scores={},
                    evidence={},
                ),
                ProductOpportunity(
                    task_id=task_a.id,
                    note_id="note-2",
                    decision=OpportunityDecision.matched,
                    product_id=product_a.id,
                    prescreen_score=4.2,
                    value_score=4.1,
                    competition_opportunity_score=4.0,
                    self_control_score=4.3,
                    total_score=88.0,
                    scores={},
                    evidence={},
                ),
                ProductOpportunity(
                    task_id=task_b.id,
                    note_id="note-1",
                    decision=OpportunityDecision.matched,
                    product_id=product_a.id,
                    prescreen_score=4.3,
                    value_score=4.2,
                    competition_opportunity_score=4.1,
                    self_control_score=4.0,
                    total_score=87.0,
                    scores={},
                    evidence={},
                ),
                ProductOpportunity(
                    task_id=task_b.id,
                    note_id="note-3",
                    decision=OpportunityDecision.created,
                    product_id=product_b.id,
                    prescreen_score=4.0,
                    value_score=3.9,
                    competition_opportunity_score=3.8,
                    self_control_score=3.7,
                    total_score=78.0,
                    scores={},
                    evidence={},
                ),
            ]
        )
        session.commit()

    payload = _fetch_product_overview(sf)

    assert payload["summary"]["total_products"] == 3
    assert payload["summary"]["active_products"] == 2
    assert payload["summary"]["assessed_products"] == 2
    assert payload["summary"]["total_linked_notes"] == 3
    assert payload["summary"]["matched_notes"] == 2
    assert payload["summary"]["created_notes"] == 2

    rows = payload["product_rows"]
    assert [row["name"] for row in rows] == ["通勤助手", "副业助手", "历史产品"]
    assert rows[0]["linked_notes"] == 2
    assert rows[0]["matched_notes"] == 2
    assert rows[0]["created_notes"] == 1
    assert rows[0]["generation_note_count"] == 2
    assert rows[0]["next_regenerate_at_linked_notes"] == 4
    assert rows[1]["linked_notes"] == 1
    assert rows[2]["linked_notes"] == 0
    assert rows[2]["total_score"] is None


def test_fetch_product_overview_returns_empty_shape_for_empty_catalog(tmp_path: Path):
    sf = _session_factory(tmp_path)

    payload = _fetch_product_overview(sf)

    assert payload["summary"] == {
        "total_products": 0,
        "active_products": 0,
        "assessed_products": 0,
        "total_linked_notes": 0,
        "matched_notes": 0,
        "created_notes": 0,
    }
    assert payload["product_rows"] == []


def test_fetch_scheduler_configs_handles_legacy_rows_without_note_limit(monkeypatch):
    legacy_row = SimpleNamespace(
        mode="discover",
        enabled=True,
        loop_interval_seconds=120,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    class FakeSchedulerConfigService:
        def __init__(self, factory):  # noqa: ARG002
            pass

        def list_configs(self):
            return [legacy_row]

    monkeypatch.setattr("ui.app.SchedulerConfigService", FakeSchedulerConfigService)

    rows = _fetch_scheduler_configs(None)

    assert rows[0]["mode"] == "discover"
    assert rows[0]["note_limit"] is None


def test_scheduler_service_supports_note_limit_detects_legacy_signature():
    class LegacyService:
        def set_config(self, mode, *, enabled, loop_interval_seconds):  # noqa: ARG002
            return None

    class CurrentService:
        def set_config(self, mode, *, enabled, loop_interval_seconds, note_limit=None):  # noqa: ARG002
            return None

    assert _scheduler_service_supports_note_limit(LegacyService) is False
    assert _scheduler_service_supports_note_limit(CurrentService) is True


def test_resolve_result_products_top_n_handles_single_product_without_slider_error():
    assert _resolve_result_products_top_n(1) == 1


def test_resolve_result_products_top_n_caps_default_to_available_products():
    assert _resolve_result_products_top_n(3, desired=20) == 3


def test_login_auth_state_message_explains_authenticated():
    message = _login_auth_state_message("authenticated")

    assert "已登录" in message


def test_login_flow_state_message_explains_waiting_phone_code():
    message = _login_flow_state_message("waiting_phone_code")

    assert "验证码" in message
    assert "等待" in message


def test_login_action_enabled_only_allows_sms_submit_during_waiting_phone_code():
    assert _login_action_enabled("waiting_phone_code", "submit_sms_code") is True
    assert _login_action_enabled("starting", "submit_sms_code") is False
    assert _login_action_enabled("idle", "start_qr") is True
    assert _login_action_enabled("waiting_qr_scan", "start_qr") is False


def test_load_login_panel_state_returns_missing_schema_hint_on_programming_error(monkeypatch):
    class BrokenLoginControllerService:
        def __init__(self, factory):  # noqa: ARG002
            pass

        def get_state(self):
            raise ProgrammingError("SELECT ...", {}, Exception('relation "login_runtime_state" does not exist'))

    monkeypatch.setattr("ui.app.LoginControllerService", BrokenLoginControllerService)

    payload = _load_login_panel_state(None)

    assert payload["ok"] is False
    assert "login_runtime_state" in payload["error"]
    assert "init_schema.py" in payload["hint"]
