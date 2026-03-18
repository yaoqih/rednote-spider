from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.login_controller import (
    LoginControllerConfig,
    LoginControllerRuntime,
    build_runtime_command,
    resolve_runtime_prefer_headed,
    build_controller_config,
    run_login_controller_iteration,
    stop_login_controller_runtime,
)
from rednote_spider.models import Base, LoginAuthState, LoginFlowState
from rednote_spider.services.login_controller_service import LoginControllerService


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "login_controller_loop.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def _emit_event_script(tmp_path: Path, lines: list[str], *, exit_code: int = 0, sleep_seconds: float = 0.0) -> Path:
    script = tmp_path / f"emit_{abs(hash(tuple(lines)))}.py"
    body = [
        "from __future__ import annotations",
        "import sys",
        "import time",
    ]
    for line in lines:
        body.append(f"print({line!r}, file=sys.stderr, flush=True)")
    if sleep_seconds > 0:
        body.append(f"time.sleep({sleep_seconds!r})")
    body.append(f"raise SystemExit({exit_code})")
    script.write_text("\n".join(body), encoding="utf-8")
    return script


def _emit_method_dispatch_script(
    tmp_path: Path,
    *,
    by_method: dict[str, list[str]],
    sleep_seconds: float = 0.0,
    default_exit_code: int = 0,
) -> Path:
    script = tmp_path / "dispatch_runtime.py"
    body = [
        "from __future__ import annotations",
        "import sys",
        "import time",
        "args = sys.argv[1:]",
        "method = args[args.index('--method') + 1] if '--method' in args else ''",
        f"sleep_seconds = {sleep_seconds!r}",
        "lines_by_method = {",
    ]
    for method, lines in by_method.items():
        body.append(f"    {method!r}: {lines!r},")
    body.extend(
        [
            "}",
            "for line in lines_by_method.get(method, []):",
            "    print(line, file=sys.stderr, flush=True)",
            "if sleep_seconds > 0:",
            "    time.sleep(sleep_seconds)",
            f"raise SystemExit({default_exit_code})",
        ]
    )
    script.write_text("\n".join(body), encoding="utf-8")
    return script


def test_login_controller_iteration_processes_probe_request(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    service.request_probe()
    script = _emit_event_script(
        tmp_path,
        [
            '[rednote-login]{"event_type":"probe_result","attempt_id":0,"message":"not logged in","payload":{"ok":false}}',
        ],
    )
    runtime = LoginControllerRuntime()
    config = LoginControllerConfig(
        runtime_python=Path(sys.executable),
        runtime_script=script,
        command_cwd=tmp_path,
        platform="xhs",
    )

    try:
        runtime = run_login_controller_iteration(sf, runtime, config)
        for _ in range(20):
            runtime = run_login_controller_iteration(sf, runtime, config)
            if runtime.process is None:
                break
            time.sleep(0.05)
        row = service.get_state()
        assert row.auth_state == LoginAuthState.unauthenticated
        assert row.flow_state == LoginFlowState.idle
    finally:
        stop_login_controller_runtime(runtime)


def test_login_controller_probe_completion_does_not_emit_stale_recovery(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    service.request_probe()
    script = _emit_event_script(
        tmp_path,
        [
            '[rednote-login]{"event_type":"probe_result","attempt_id":0,"message":"not logged in","payload":{"ok":false}}',
        ],
        sleep_seconds=0.05,
    )
    runtime = LoginControllerRuntime()
    config = LoginControllerConfig(
        runtime_python=Path(sys.executable),
        runtime_script=script,
        command_cwd=tmp_path,
        platform="xhs",
    )

    try:
        runtime = run_login_controller_iteration(sf, runtime, config)
        for _ in range(20):
            runtime = run_login_controller_iteration(sf, runtime, config)
            if runtime.process is None:
                break
            time.sleep(0.05)
        event_types = [item.event_type for item in service.list_events(limit=20)]
        assert "controller_recovered_stale_attempt" not in event_types
    finally:
        stop_login_controller_runtime(runtime)


def test_login_controller_iteration_processes_qr_flow(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    started = service.start_qr_login()
    script = _emit_event_script(
        tmp_path,
        [
            f'[rednote-login]{{"event_type":"qr_ready","attempt_id":{started.attempt_id},"message":"qr ready","payload":{{"image_path":"/tmp/qr.png"}}}}',
            f'[rednote-login]{{"event_type":"authenticated","attempt_id":{started.attempt_id},"message":"logged in","payload":{{"ok":true}}}}',
        ],
        sleep_seconds=0.05,
    )
    runtime = LoginControllerRuntime()
    config = LoginControllerConfig(
        runtime_python=Path(sys.executable),
        runtime_script=script,
        command_cwd=tmp_path,
        platform="xhs",
    )

    try:
        runtime = run_login_controller_iteration(sf, runtime, config)
        for _ in range(20):
            runtime = run_login_controller_iteration(sf, runtime, config)
            if runtime.process is None:
                break
            time.sleep(0.05)
        row = service.get_state()
        assert row.auth_state == LoginAuthState.authenticated
        assert row.flow_state == LoginFlowState.idle
        assert row.qr_image_path == "/tmp/qr.png"
    finally:
        stop_login_controller_runtime(runtime)


def test_login_controller_iteration_processes_phone_waiting_code(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    started = service.start_phone_login("13800138000")
    script = _emit_event_script(
        tmp_path,
        [
            f'[rednote-login]{{"event_type":"waiting_phone_code","attempt_id":{started.attempt_id},"message":"sms requested","payload":{{}}}}',
        ],
        sleep_seconds=60.0,
    )
    runtime = LoginControllerRuntime()
    config = LoginControllerConfig(
        runtime_python=Path(sys.executable),
        runtime_script=script,
        command_cwd=tmp_path,
        platform="xhs",
    )

    try:
        runtime = run_login_controller_iteration(sf, runtime, config)
        for _ in range(20):
            runtime = run_login_controller_iteration(sf, runtime, config)
            if service.get_state().flow_state == LoginFlowState.waiting_phone_code:
                break
            time.sleep(0.05)
        row = service.get_state()
        assert row.flow_state == LoginFlowState.waiting_phone_code
        assert row.phone_number == "13800138000"
    finally:
        stop_login_controller_runtime(runtime)


def test_login_controller_iteration_can_preempt_active_attempt_with_probe(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    started = service.start_phone_login("13800138000")
    script = _emit_method_dispatch_script(
        tmp_path,
        by_method={
            "phone": [
                f'[rednote-login]{{"event_type":"waiting_phone_code","attempt_id":{started.attempt_id},"message":"sms requested","payload":{{}}}}',
            ],
            "probe": [
                '[rednote-login]{"event_type":"probe_result","attempt_id":0,"message":"not logged in","payload":{"ok":false}}',
            ],
        },
        sleep_seconds=60.0,
    )
    runtime = LoginControllerRuntime()
    config = LoginControllerConfig(
        runtime_python=Path(sys.executable),
        runtime_script=script,
        command_cwd=tmp_path,
        platform="xhs",
    )

    try:
        runtime = run_login_controller_iteration(sf, runtime, config)
        for _ in range(20):
            runtime = run_login_controller_iteration(sf, runtime, config)
            if service.get_state().flow_state == LoginFlowState.waiting_phone_code:
                break
            time.sleep(0.05)

        service.request_probe()
        for _ in range(40):
            runtime = run_login_controller_iteration(sf, runtime, config)
            row = service.get_state()
            if runtime.process is None and row.flow_state == LoginFlowState.idle:
                break
            time.sleep(0.05)

        row = service.get_state()
        assert row.auth_state == LoginAuthState.unauthenticated
        assert row.flow_state == LoginFlowState.idle
        assert row.requested_action is None
    finally:
        stop_login_controller_runtime(runtime)


def test_login_controller_iteration_acknowledges_cancel_without_active_child(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = LoginControllerService(sf)
    service.start_qr_login()
    service.cancel_current_attempt()
    script = _emit_event_script(tmp_path, [])
    runtime = LoginControllerRuntime()
    config = LoginControllerConfig(
        runtime_python=Path(sys.executable),
        runtime_script=script,
        command_cwd=tmp_path,
        platform="xhs",
    )

    try:
        runtime = run_login_controller_iteration(sf, runtime, config)
        row = service.get_state()
        assert runtime.process is None
        assert row.flow_state == LoginFlowState.idle
        assert row.requested_action is None
        assert row.handled_action_nonce == row.action_nonce
    finally:
        stop_login_controller_runtime(runtime)


def test_build_controller_config_points_to_existing_login_runtime_script():
    config = build_controller_config()

    assert config.runtime_script.name == "run_mediacrawler_login_only.py"
    assert config.runtime_script.exists()


def test_build_controller_config_prefers_mediacrawler_venv_python(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rednote_spider.login_controller.settings.login_runtime_python", "")
    monkeypatch.setattr("rednote_spider.login_controller.settings.login_runtime_crawler_cwd", "")
    monkeypatch.setattr("rednote_spider.login_controller.settings.login_qr_crawler_cwd", "")
    monkeypatch.setattr("rednote_spider.login_controller.settings.login_phone_crawler_cwd", "")

    config = build_controller_config()

    assert config.runtime_python == (config.command_cwd / ".venv" / "bin" / "python")


def test_build_runtime_command_uses_xvfb_for_interactive_login_without_display(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("rednote_spider.login_controller.shutil.which", lambda name: "/usr/bin/xvfb-run" if name == "xvfb-run" else None)
    config = LoginControllerConfig(
        runtime_python=Path("/tmp/python"),
        runtime_script=Path("/tmp/runtime.py"),
        command_cwd=Path("/tmp"),
        platform="xhs",
    )

    command = build_runtime_command(config, method="phone", attempt_id=9, phone_number="13800138000")

    assert command[:2] == ["/usr/bin/xvfb-run", "-a"]
    assert command[-2:] == ["--phone-number", "13800138000"]


def test_build_runtime_command_keeps_probe_direct_without_display(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("rednote_spider.login_controller.shutil.which", lambda name: "/usr/bin/xvfb-run" if name == "xvfb-run" else None)
    config = LoginControllerConfig(
        runtime_python=Path("/tmp/python"),
        runtime_script=Path("/tmp/runtime.py"),
        command_cwd=Path("/tmp"),
        platform="xhs",
    )

    command = build_runtime_command(config, method="probe", attempt_id=0)

    assert command[0] == "/tmp/python"
    assert "xvfb-run" not in command[0]


def test_resolve_runtime_prefer_headed_falls_back_without_display_or_xvfb(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    assert resolve_runtime_prefer_headed(method="qr", xvfb_run_path=None) is False
    assert resolve_runtime_prefer_headed(method="phone", xvfb_run_path=None) is False
    assert resolve_runtime_prefer_headed(method="probe", xvfb_run_path=None) is False
