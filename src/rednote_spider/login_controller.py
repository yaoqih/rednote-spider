"""Unified login controller loop."""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .login_runtime_events import parse_login_runtime_event
from .mediacrawler_runtime import build_browser_launch_options
from .services.login_controller_service import LoginControllerService


@dataclass
class LoginControllerConfig:
    runtime_python: Path
    runtime_script: Path
    command_cwd: Path
    platform: str = "xhs"
    poll_seconds: int = 2
    controller_pid: int = field(default_factory=os.getpid)


@dataclass
class LoginControllerRuntime:
    process: subprocess.Popen[bytes] | None = None
    active_attempt_id: int = 0
    active_action_nonce: int = 0
    detail_tail: deque[str] = field(default_factory=lambda: deque(maxlen=300))
    event_queue: deque[dict] = field(default_factory=deque)
    pipe_threads: list[threading.Thread] = field(default_factory=list)


def build_controller_config() -> LoginControllerConfig:
    project_root = Path(__file__).resolve().parents[2]
    crawler_cwd_raw = (
        settings.login_runtime_crawler_cwd
        or settings.login_qr_crawler_cwd
        or settings.login_phone_crawler_cwd
        or "../MediaCrawler"
    )
    command_cwd = Path(crawler_cwd_raw).expanduser().resolve()
    default_runtime_python = (command_cwd / ".venv" / "bin" / "python").expanduser()
    runtime_python = (
        Path(settings.login_runtime_python).expanduser()
        if settings.login_runtime_python
        else (default_runtime_python if default_runtime_python.exists() else Path(sys.executable))
    )
    runtime_script = (project_root / "scripts" / "run_mediacrawler_login_only.py").resolve()
    return LoginControllerConfig(
        runtime_python=runtime_python,
        runtime_script=runtime_script,
        command_cwd=command_cwd,
        platform="xhs",
        poll_seconds=max(1, int(settings.login_controller_poll_seconds)),
    )


def _build_child_env(*, attempt_id: int, database_url: str, platform: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["DATABASE_URL"] = database_url
    env["REDNOTE_LOGIN_DATABASE_URL"] = database_url
    env["REDNOTE_LOGIN_PLATFORM"] = platform
    env["REDNOTE_LOGIN_ATTEMPT_ID"] = str(int(attempt_id))
    return env


def resolve_runtime_prefer_headed(*, method: str, xvfb_run_path: str | None = None) -> bool:
    launch_options = build_browser_launch_options(method=method)
    if launch_options["headless"]:
        return False
    if not launch_options["requires_virtual_display"]:
        return True
    return bool(xvfb_run_path)


def build_runtime_command(
    config: LoginControllerConfig,
    *,
    method: str,
    attempt_id: int,
    phone_number: str = "",
) -> list[str]:
    args = [
        str(config.runtime_python),
        str(config.runtime_script),
        "--platform",
        config.platform,
        "--method",
        method,
        "--attempt-id",
        str(int(attempt_id)),
    ]
    if phone_number:
        args.extend(["--phone-number", phone_number])

    xvfb_run = shutil.which("xvfb-run")
    launch_options = build_browser_launch_options(method=method)
    if launch_options["requires_virtual_display"] and xvfb_run:
        return [xvfb_run, "-a", *args]
    return args


def _decode_stream(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace") if raw else ""


def _start_forwarding_threads(process: subprocess.Popen[bytes], runtime: LoginControllerRuntime) -> list[threading.Thread]:
    def _forward_stream(pipe) -> None:
        if pipe is None:
            return
        try:
            for raw in iter(pipe.readline, b""):
                line = _decode_stream(raw)
                if not line:
                    continue
                runtime.detail_tail.append(line)
                event = parse_login_runtime_event(line)
                if event is not None:
                    runtime.event_queue.append(event)
                print(line, end="", file=sys.stderr)
                sys.stderr.flush()
        finally:
            pipe.close()

    threads = [
        threading.Thread(target=_forward_stream, args=(process.stdout,), daemon=True),
        threading.Thread(target=_forward_stream, args=(process.stderr,), daemon=True),
    ]
    for thread in threads:
        thread.start()
    return threads


def _start_process(
    config: LoginControllerConfig,
    runtime: LoginControllerRuntime,
    *,
    method: str,
    attempt_id: int,
    phone_number: str = "",
) -> subprocess.Popen[bytes]:
    args = build_runtime_command(
        config,
        method=method,
        attempt_id=attempt_id,
        phone_number=phone_number,
    )
    env = _build_child_env(attempt_id=attempt_id, database_url=settings.database_url, platform=config.platform)
    env["REDNOTE_LOGIN_CRAWLER_CWD"] = str(config.command_cwd)
    env["REDNOTE_LOGIN_PREFER_HEADED"] = "1" if resolve_runtime_prefer_headed(method=method, xvfb_run_path=shutil.which("xvfb-run")) else "0"
    process = subprocess.Popen(
        args,
        cwd=str(config.command_cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=(os.name == "posix"),
    )
    runtime.detail_tail.clear()
    runtime.pipe_threads = _start_forwarding_threads(process, runtime)
    return process


def stop_login_controller_runtime(runtime: LoginControllerRuntime) -> None:
    process = runtime.process
    if process is not None and process.poll() is None:
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)
    for thread in runtime.pipe_threads:
        thread.join(timeout=1)
    runtime.process = None
    runtime.pipe_threads = []
    runtime.event_queue.clear()
    runtime.detail_tail.clear()
    runtime.active_attempt_id = 0
    runtime.active_action_nonce = 0


def _join_pipe_threads(runtime: LoginControllerRuntime, *, timeout: float = 0.5) -> None:
    for thread in runtime.pipe_threads:
        thread.join(timeout=timeout)


def _apply_runtime_events(service: LoginControllerService, runtime: LoginControllerRuntime, *, platform: str) -> None:
    while runtime.event_queue:
        service.apply_runtime_event(runtime.event_queue.popleft(), platform=platform)


def run_login_controller_iteration(
    session_factory: sessionmaker[Session],
    runtime: LoginControllerRuntime,
    config: LoginControllerConfig,
) -> LoginControllerRuntime:
    service = LoginControllerService(session_factory)
    process = runtime.process
    if process is not None and process.poll() is not None:
        _join_pipe_threads(runtime)

    row = service.get_state(platform=config.platform)
    if runtime.process is not None and row.action_nonce > row.handled_action_nonce:
        detail = f"preempted by requested_action={row.requested_action or ''}".strip()
        service.finalize_child_exit(
            attempt_id=runtime.active_attempt_id,
            returncode=0,
            detail=detail,
            platform=config.platform,
        )
        stop_login_controller_runtime(runtime)
        row = service.get_state(platform=config.platform)

    _apply_runtime_events(service, runtime, platform=config.platform)
    process = runtime.process
    if process is not None and process.poll() is not None:
        detail = "".join(runtime.detail_tail).strip()
        service.finalize_child_exit(
            attempt_id=runtime.active_attempt_id,
            returncode=int(process.returncode or 0),
            detail=detail[:500],
            platform=config.platform,
        )
        stop_login_controller_runtime(runtime)
        row = service.get_state(platform=config.platform)

    service.reconcile_stale_runtime(
        active_child_pids=[runtime.process.pid] if runtime.process is not None and runtime.process.poll() is None else [],
        platform=config.platform,
    )
    row = service.get_state(platform=config.platform)

    if runtime.process is None and row.action_nonce > row.handled_action_nonce:
        method = ""
        if row.requested_action == "probe":
            method = "probe"
        elif row.requested_action == "start_qr":
            method = "qr"
        elif row.requested_action == "start_phone":
            method = "phone"
        elif row.requested_action == "cancel":
            service.acknowledge_action_started(
                action_nonce=row.action_nonce,
                child_pid=None,
                controller_pid=config.controller_pid,
                platform=config.platform,
            )
            row = service.get_state(platform=config.platform)
        if method:
            try:
                runtime.process = _start_process(
                    config,
                    runtime,
                    method=method,
                    attempt_id=int(row.attempt_id),
                    phone_number=row.phone_number or "",
                )
                runtime.active_attempt_id = int(row.attempt_id)
                runtime.active_action_nonce = int(row.action_nonce)
                service.acknowledge_action_started(
                    action_nonce=row.action_nonce,
                    child_pid=runtime.process.pid,
                    controller_pid=config.controller_pid,
                    platform=config.platform,
                )
            except Exception as exc:  # noqa: BLE001
                service.finalize_child_exit(
                    attempt_id=int(row.attempt_id),
                    returncode=1,
                    detail=str(exc),
                    platform=config.platform,
                )
                stop_login_controller_runtime(runtime)
        elif row.requested_action == "cancel":
            service.acknowledge_action_started(
                action_nonce=row.action_nonce,
                child_pid=None,
                controller_pid=config.controller_pid,
                platform=config.platform,
            )

    return runtime
