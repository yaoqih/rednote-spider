from __future__ import annotations

import argparse
import os
from io import StringIO
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.models import Base
from rednote_spider.services.login_controller_service import LoginControllerService


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "mediacrawler_login_runtime.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def test_emit_login_runtime_event_writes_structured_line():
    from rednote_spider.mediacrawler_login_runtime import emit_login_runtime_event

    stream = StringIO()
    emit_login_runtime_event(
        "waiting_phone_code",
        "sms requested",
        attempt_id=7,
        stream=stream,
        phone_number="13800138000",
    )

    rendered = stream.getvalue()
    assert rendered.startswith('[rednote-login]{"event_type": "waiting_phone_code"'.replace(": ", ":"))
    assert '"attempt_id":7' in rendered.replace(" ", "")
    assert '"phone_number":"13800138000"' in rendered.replace(" ", "")


def test_consume_sms_code_from_database_uses_unified_controller_state(tmp_path: Path):
    from rednote_spider.mediacrawler_login_runtime import consume_sms_code_from_database

    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    started = service.start_phone_login("13800138000")
    service.apply_runtime_event(
        {
            "event_type": "waiting_phone_code",
            "attempt_id": started.attempt_id,
            "message": "sms requested",
            "payload": {},
        }
    )
    service.submit_phone_code("123456")

    code = consume_sms_code_from_database(
        session_factory=sf,
        attempt_id=started.attempt_id,
        platform="xhs",
    )

    assert code == "123456"
    assert consume_sms_code_from_database(
        session_factory=sf,
        attempt_id=started.attempt_id,
        platform="xhs",
    ) is None


def test_map_phone_stage_event_supports_security_and_retryable_code_failures():
    from rednote_spider.mediacrawler_login_runtime import map_phone_stage_event

    security_event = map_phone_stage_event(
        "need_verify",
        "security verification required",
        image_path="/tmp/security.png",
    )
    invalid_code_event = map_phone_stage_event("invalid_sms_code", "code expired")

    assert security_event == (
        "waiting_security_verification",
        "security verification required",
        {"image_path": "/tmp/security.png"},
    )
    assert invalid_code_event == ("invalid_sms_code", "code expired", {})


def test_build_runtime_config_defaults_to_mediacrawler_root(monkeypatch):
    from rednote_spider.mediacrawler_login_runtime import PROJECT_ROOT, build_runtime_config

    monkeypatch.delenv("REDNOTE_LOGIN_CRAWLER_CWD", raising=False)
    monkeypatch.delenv("LOGIN_RUNTIME_CRAWLER_CWD", raising=False)

    config = build_runtime_config(
        argparse.Namespace(
            platform="xhs",
            method="probe",
            attempt_id=0,
            phone_number="",
        )
    )

    assert config.crawler_cwd == (PROJECT_ROOT.parent / "MediaCrawler").resolve()


def test_build_runtime_config_prefers_explicit_crawler_cwd(monkeypatch, tmp_path: Path):
    from rednote_spider.mediacrawler_login_runtime import build_runtime_config

    monkeypatch.setenv("REDNOTE_LOGIN_CRAWLER_CWD", str(tmp_path))

    config = build_runtime_config(
        argparse.Namespace(
            platform="xhs",
            method="probe",
            attempt_id=0,
            phone_number="",
        )
    )

    assert config.crawler_cwd == tmp_path.resolve()
