"""Unified login controller state service."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..mediacrawler_phone import normalize_phone_number, normalize_sms_code
from ..models import LoginAuthState, LoginEvent, LoginFlowState, LoginRuntimeState


class LoginControllerService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def get_state(self, platform: str = "xhs") -> LoginRuntimeState:
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            session.commit()
            session.refresh(row)
            return row

    def list_events(self, *, platform: str = "xhs", limit: int = 50) -> list[LoginEvent]:
        with self.session_factory() as session:
            rows = session.execute(
                select(LoginEvent)
                .where(LoginEvent.platform == platform)
                .order_by(LoginEvent.id.desc())
                .limit(max(1, int(limit)))
            ).scalars().all()
            rows.reverse()
            return rows

    def request_probe(self, platform: str = "xhs") -> LoginRuntimeState:
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            row.requested_action = "probe"
            row.action_nonce += 1
            row.flow_state = LoginFlowState.probing
            row.last_error = None
            self._append_event(session, platform=platform, attempt_id=row.attempt_id, event_type="probe_requested", message="probe requested")
            session.commit()
            session.refresh(row)
            return row

    def start_qr_login(self, platform: str = "xhs") -> LoginRuntimeState:
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            row.attempt_id += 1
            row.requested_action = "start_qr"
            row.action_nonce += 1
            row.active_method = "qr"
            row.flow_state = LoginFlowState.starting
            row.last_error = None
            row.qr_image_path = None
            row.security_image_path = None
            row.child_pid = None
            self._append_event(session, platform=platform, attempt_id=row.attempt_id, event_type="qr_login_requested", message="qr login requested")
            session.commit()
            session.refresh(row)
            return row

    def start_phone_login(self, phone_number: str, platform: str = "xhs") -> LoginRuntimeState:
        normalized_phone = normalize_phone_number(phone_number)
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            row.attempt_id += 1
            row.requested_action = "start_phone"
            row.action_nonce += 1
            row.active_method = "phone"
            row.phone_number = normalized_phone
            row.flow_state = LoginFlowState.starting
            row.last_error = None
            row.qr_image_path = None
            row.security_image_path = None
            row.child_pid = None
            row.submitted_sms_code = None
            row.sms_code_nonce = 0
            row.handled_sms_code_nonce = 0
            self._append_event(
                session,
                platform=platform,
                attempt_id=row.attempt_id,
                event_type="phone_login_requested",
                message="phone login requested",
                payload={"phone_number": normalized_phone},
            )
            session.commit()
            session.refresh(row)
            return row

    def cancel_current_attempt(self, platform: str = "xhs") -> LoginRuntimeState:
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            row.requested_action = "cancel"
            row.action_nonce += 1
            row.flow_state = LoginFlowState.idle
            row.last_error = None
            self._append_event(
                session,
                platform=platform,
                attempt_id=row.attempt_id,
                event_type="login_cancel_requested",
                message="login cancel requested",
            )
            session.commit()
            session.refresh(row)
            return row

    def submit_phone_code(self, code: str, platform: str = "xhs") -> LoginRuntimeState:
        normalized_code = normalize_sms_code(code)
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            if row.flow_state != LoginFlowState.waiting_phone_code:
                raise ValueError("sms code can only be submitted while flow_state=waiting_phone_code")
            row.submitted_sms_code = normalized_code
            row.sms_code_nonce += 1
            self._append_event(
                session,
                platform=platform,
                attempt_id=row.attempt_id,
                event_type="sms_code_submitted",
                message="sms code submitted",
                payload={"sms_code_nonce": row.sms_code_nonce},
            )
            session.commit()
            session.refresh(row)
            return row

    def consume_submitted_sms_code(self, *, attempt_id: int, platform: str = "xhs") -> str | None:
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            if row.attempt_id != int(attempt_id):
                session.commit()
                return None
            if row.sms_code_nonce <= row.handled_sms_code_nonce:
                session.commit()
                return None
            code = str(row.submitted_sms_code or "").strip()
            if not code:
                session.commit()
                return None
            row.handled_sms_code_nonce = row.sms_code_nonce
            row.submitted_sms_code = None
            session.commit()
            return code

    def acknowledge_action_started(
        self,
        *,
        action_nonce: int,
        child_pid: int | None,
        controller_pid: int | None,
        platform: str = "xhs",
    ) -> LoginRuntimeState:
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            row.handled_action_nonce = max(row.handled_action_nonce, int(action_nonce))
            row.child_pid = child_pid
            row.controller_pid = controller_pid
            if child_pid is None:
                row.requested_action = None
            session.commit()
            session.refresh(row)
            return row

    def apply_runtime_event(self, event: dict, platform: str = "xhs") -> LoginRuntimeState:
        event_type = str(event.get("event_type") or "").strip()
        message = str(event.get("message") or "").strip()
        attempt_id = int(event.get("attempt_id") or 0)
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            profile_dir = str(payload.get("profile_dir") or "").strip()
            if profile_dir:
                row.profile_dir = profile_dir
            self._append_event(
                session,
                platform=platform,
                attempt_id=attempt_id,
                event_type=event_type or "runtime_event",
                message=message,
                payload=payload,
            )
            if attempt_id > 0 and row.attempt_id not in {0, attempt_id}:
                session.commit()
                session.refresh(row)
                return row

            if event_type == "probe_result":
                ok = bool(payload.get("ok"))
                row.last_probe_ok = ok
                row.last_probe_at = self._parse_dt(payload.get("probed_at")) or datetime.now()
                row.auth_state = LoginAuthState.authenticated if ok else LoginAuthState.unauthenticated
                if row.flow_state == LoginFlowState.probing:
                    row.flow_state = LoginFlowState.idle
                    row.requested_action = None
                    row.child_pid = None
                if ok:
                    row.last_error = None
                elif message:
                    row.last_error = message
            elif event_type == "qr_ready":
                row.flow_state = LoginFlowState.waiting_qr_scan
                row.qr_image_path = str(payload.get("image_path") or "") or row.qr_image_path
                row.security_image_path = None
                row.last_error = None
            elif event_type == "waiting_phone_code":
                row.flow_state = LoginFlowState.waiting_phone_code
                row.last_error = None
            elif event_type == "waiting_security_verification":
                row.flow_state = LoginFlowState.waiting_security_verification
                row.security_image_path = str(payload.get("image_path") or "") or row.security_image_path
                row.last_error = message or row.last_error
            elif event_type == "runtime_context":
                row.profile_dir = str(payload.get("profile_dir") or "") or row.profile_dir
            elif event_type == "need_human_action":
                row.flow_state = LoginFlowState.need_human_action
                row.security_image_path = str(payload.get("image_path") or "") or row.security_image_path
                row.last_error = message or row.last_error
            elif event_type == "verifying":
                row.flow_state = LoginFlowState.verifying
                row.last_error = None
            elif event_type == "invalid_sms_code":
                row.flow_state = LoginFlowState.waiting_phone_code
                row.last_error = message or "invalid sms code"
            elif event_type == "authenticated":
                row.auth_state = LoginAuthState.authenticated
                row.last_probe_ok = True
                row.last_probe_at = self._parse_dt(payload.get("probed_at")) or datetime.now()
                row.flow_state = LoginFlowState.idle
                row.requested_action = None
                row.child_pid = None
                row.last_error = None
            elif event_type in {"authentication_failed", "runtime_failed"}:
                row.auth_state = LoginAuthState.unauthenticated
                row.flow_state = LoginFlowState.failed
                row.requested_action = None
                row.last_error = message or "login attempt failed"
                row.child_pid = None
            session.commit()
            session.refresh(row)
            return row

    def finalize_child_exit(
        self,
        *,
        attempt_id: int,
        returncode: int,
        detail: str = "",
        platform: str = "xhs",
    ) -> LoginRuntimeState:
        active_states = {
            LoginFlowState.starting,
            LoginFlowState.probing,
            LoginFlowState.waiting_qr_scan,
            LoginFlowState.waiting_phone_code,
            LoginFlowState.waiting_security_verification,
            LoginFlowState.verifying,
            LoginFlowState.need_human_action,
        }
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            row.child_pid = None
            # Runtime events are authoritative for terminal success/failure. If the flow already
            # settled to idle, a later wrapper/non-zero exit should not overwrite that outcome.
            if (
                row.attempt_id == int(attempt_id)
                and returncode != 0
                and row.flow_state in active_states
            ):
                row.flow_state = LoginFlowState.failed
                row.last_error = detail or f"login runtime failed exit code {returncode}"
                row.auth_state = LoginAuthState.unauthenticated
            self._append_event(
                session,
                platform=platform,
                attempt_id=attempt_id,
                event_type="runtime_exited",
                message=detail or f"runtime exited code={returncode}",
                payload={"returncode": int(returncode)},
            )
            session.commit()
            session.refresh(row)
            return row

    def reconcile_stale_runtime(
        self,
        *,
        active_child_pids: Iterable[int] | None = None,
        platform: str = "xhs",
    ) -> LoginRuntimeState:
        active = {int(pid) for pid in (active_child_pids or []) if int(pid) > 0}
        stale_states = {
            LoginFlowState.starting,
            LoginFlowState.probing,
            LoginFlowState.waiting_qr_scan,
            LoginFlowState.waiting_phone_code,
            LoginFlowState.waiting_security_verification,
            LoginFlowState.verifying,
            LoginFlowState.need_human_action,
        }
        with self.session_factory() as session:
            row = self._ensure_row(session, platform)
            can_be_stale = row.handled_action_nonce >= row.action_nonce
            if row.flow_state in stale_states and can_be_stale and (row.child_pid or 0) not in active:
                prior_attempt_id = row.attempt_id
                row.flow_state = LoginFlowState.idle
                row.child_pid = None
                row.requested_action = None
                self._append_event(
                    session,
                    platform=platform,
                    attempt_id=prior_attempt_id,
                    event_type="controller_recovered_stale_attempt",
                    message="controller recovered stale attempt",
                )
            session.commit()
            session.refresh(row)
            return row

    def _ensure_row(self, session: Session, platform: str) -> LoginRuntimeState:
        row = session.query(LoginRuntimeState).filter(LoginRuntimeState.platform == platform).one_or_none()
        if row is None:
            row = LoginRuntimeState(platform=platform)
            session.add(row)
            session.flush()
        return row

    def _append_event(
        self,
        session: Session,
        *,
        platform: str,
        attempt_id: int,
        event_type: str,
        message: str,
        payload: dict | None = None,
    ) -> None:
        session.add(
            LoginEvent(
                platform=platform,
                attempt_id=int(attempt_id or 0),
                event_type=(event_type or "runtime_event").strip(),
                message=message or "",
                payload=payload or {},
            )
        )

    def _parse_dt(self, raw: object) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None
