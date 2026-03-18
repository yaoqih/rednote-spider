from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.models import Base, LoginAuthState, LoginFlowState
from rednote_spider.services.login_controller_service import LoginControllerService


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "login_controller.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def test_login_controller_service_bootstraps_runtime_state(tmp_path: Path):
    sf = _session_factory(tmp_path)

    row = LoginControllerService(sf).get_state()

    assert row.platform == "xhs"
    assert row.auth_state == LoginAuthState.unknown
    assert row.flow_state == LoginFlowState.idle
    assert row.attempt_id == 0
    assert row.action_nonce == 0
    assert row.handled_action_nonce == 0


def test_login_controller_service_tracks_qr_phone_and_probe_actions(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)

    probe = service.request_probe()
    assert probe.requested_action == "probe"
    assert probe.flow_state == LoginFlowState.probing
    assert probe.action_nonce == 1

    qr = service.start_qr_login()
    assert qr.requested_action == "start_qr"
    assert qr.active_method == "qr"
    assert qr.flow_state == LoginFlowState.starting
    assert qr.attempt_id == 1
    assert qr.action_nonce == 2

    phone = service.start_phone_login("+86 138-0013-8000")
    assert phone.requested_action == "start_phone"
    assert phone.active_method == "phone"
    assert phone.phone_number == "13800138000"
    assert phone.flow_state == LoginFlowState.starting
    assert phone.attempt_id == 2
    assert phone.action_nonce == 3


def test_login_controller_service_requires_waiting_phone_code_before_submit(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    service.start_phone_login("13800138000")

    try:
        service.submit_phone_code("123456")
    except ValueError as exc:
        assert "waiting_phone_code" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected submit_phone_code to reject non waiting state")


def test_login_controller_service_consumes_sms_code_once(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    started = service.start_phone_login("13800138000")
    service.apply_runtime_event(
        {
            "event_type": "waiting_phone_code",
            "attempt_id": started.attempt_id,
            "message": "sms code requested",
            "payload": {},
        }
    )

    submitted = service.submit_phone_code("123456")
    assert submitted.sms_code_nonce == 1

    consumed = service.consume_submitted_sms_code(attempt_id=started.attempt_id)
    assert consumed == "123456"
    assert service.consume_submitted_sms_code(attempt_id=started.attempt_id) is None


def test_login_controller_service_reconciles_stale_runtime_state(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    started = service.start_qr_login()
    service.acknowledge_action_started(
        action_nonce=started.action_nonce,
        child_pid=4321,
        controller_pid=999,
    )
    service.apply_runtime_event(
        {
            "event_type": "qr_ready",
            "attempt_id": started.attempt_id,
            "message": "qr ready",
            "payload": {"image_path": "/tmp/qr.png"},
        }
    )

    reconciled = service.reconcile_stale_runtime(active_child_pids={})

    assert reconciled.flow_state == LoginFlowState.idle
    assert reconciled.child_pid is None
    events = service.list_events(limit=10)
    assert any(item.event_type == "controller_recovered_stale_attempt" for item in events)


def test_login_controller_service_applies_probe_and_authenticated_events(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    started = service.start_qr_login()
    service.acknowledge_action_started(
        action_nonce=started.action_nonce,
        child_pid=4321,
        controller_pid=999,
    )

    probing = service.apply_runtime_event(
        {
            "event_type": "probe_result",
            "attempt_id": started.attempt_id,
            "message": "not logged in",
            "payload": {"ok": False, "probed_at": datetime.now().isoformat()},
        }
    )
    assert probing.auth_state == LoginAuthState.unauthenticated

    authed = service.apply_runtime_event(
        {
            "event_type": "authenticated",
            "attempt_id": started.attempt_id,
            "message": "probe success",
            "payload": {"ok": True, "probed_at": datetime.now().isoformat()},
        }
    )
    assert authed.auth_state == LoginAuthState.authenticated
    assert authed.flow_state == LoginFlowState.idle


def test_login_controller_service_finalize_child_exit_keeps_authenticated_terminal_state(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    started = service.start_qr_login()
    service.acknowledge_action_started(
        action_nonce=started.action_nonce,
        child_pid=4321,
        controller_pid=999,
    )
    service.apply_runtime_event(
        {
            "event_type": "authenticated",
            "attempt_id": started.attempt_id,
            "message": "login success",
            "payload": {"ok": True, "probed_at": datetime.now().isoformat()},
        }
    )

    row = service.finalize_child_exit(
        attempt_id=started.attempt_id,
        returncode=1,
        detail="wrapper exited 1 after auth success",
    )

    assert row.auth_state == LoginAuthState.authenticated
    assert row.flow_state == LoginFlowState.idle
    assert row.last_error is None


def test_login_controller_service_probe_completion_clears_requested_action_and_updates_profile(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    service.request_probe()

    row = service.apply_runtime_event(
        {
            "event_type": "probe_result",
            "attempt_id": 0,
            "message": "already logged in",
            "payload": {
                "ok": True,
                "probed_at": datetime.now().isoformat(),
                "profile_dir": "/tmp/xhs-profile",
            },
        }
    )

    assert row.auth_state == LoginAuthState.authenticated
    assert row.flow_state == LoginFlowState.idle
    assert row.requested_action is None
    assert row.profile_dir == "/tmp/xhs-profile"
