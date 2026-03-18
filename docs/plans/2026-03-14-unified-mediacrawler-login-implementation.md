# Unified MediaCrawler Login Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current split QR/phone login orchestration with a MediaCrawler-first unified login controller, login-only runtime, and single Streamlit control panel.

**Architecture:** Introduce one current-state table plus an append-only event table, then replace the QR/phone workers with a single login controller that drives a login-only MediaCrawler runtime. Authentication truth is derived only from the shared browser profile and `pong()` probe result.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, Streamlit, subprocess, pytest, Playwright/MediaCrawler runtime patching

---

### Task 1: Add failing tests for unified login state and controller contracts

**Files:**
- Create: `tests/test_login_controller_service.py`
- Modify: `tests/test_schema.py`
- Modify: `tests/test_init_schema_script.py`

**Step 1: Write the failing tests**

- Add tests that expect:
  - a new `login_runtime_state` row bootstraps with `auth_state=unknown` and `flow_state=idle`
  - a new `login_event` row is appended by controller actions
  - stale `starting/waiting_*` states with missing `child_pid` reconcile back to `idle`
  - `start_qr_login` and `start_phone_login` increment `attempt_id` and `action_nonce`
  - `submit_phone_code` only works while `flow_state=waiting_phone_code`

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_login_controller_service.py tests/test_schema.py tests/test_init_schema_script.py`
Expected: FAIL with missing models/service symbols.

**Step 3: Write minimal implementation**

- Add unified login models
- Add `LoginControllerService` with bootstrap, action, reconcile, and event append helpers
- Update schema initialization

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_login_controller_service.py tests/test_schema.py tests/test_init_schema_script.py`
Expected: PASS

### Task 2: Add failing tests for login-only controller loop

**Files:**
- Create: `tests/test_login_controller.py`
- Create: `src/rednote_spider/login_controller.py`

**Step 1: Write the failing tests**

- Cover:
  - `probe` action updates auth state from runtime result
  - QR attempt enters `waiting_qr_scan` on qr event
  - phone attempt enters `waiting_phone_code`
  - security verification enters `waiting_security_verification`
  - success only occurs after explicit probe-success event
  - stale controller state is reconciled on startup

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_login_controller.py`
Expected: FAIL with missing controller loop/runtime adapters.

**Step 3: Write minimal implementation**

- Implement unified controller runtime loop
- Implement event application and reconcile behavior
- Keep child-process adapter injectable for tests

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_login_controller.py tests/test_login_controller_service.py`
Expected: PASS

### Task 3: Add failing tests for unified login-only runtime

**Files:**
- Create: `tests/test_mediacrawler_login_runtime.py`
- Create: `scripts/run_mediacrawler_login_only.py`
- Modify: `src/rednote_spider/mediacrawler_qr.py`
- Modify: `src/rednote_spider/mediacrawler_phone.py`
- Modify: `src/rednote_spider/mediacrawler_runtime.py`

**Step 1: Write the failing tests**

- Cover:
  - runtime emits structured events for `probe_started`, `probe_result`, `qr_ready`, `waiting_phone_code`, `waiting_security_verification`, `verification_failed`, `authenticated`
  - runtime returns success only after final probe success
  - phone runtime reads submitted SMS codes from unified state adapter instead of file bridge

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_mediacrawler_login_runtime.py tests/test_mediacrawler_phone.py tests/test_mediacrawler_runtime.py tests/test_mediacrawler_qr.py`
Expected: FAIL with missing runtime/event helpers.

**Step 3: Write minimal implementation**

- Add structured event emitter for login-only runtime
- Rework phone patch to support unified SMS code provider callbacks
- Reuse QR rendering helper without coupling to crawl run completion

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_mediacrawler_login_runtime.py tests/test_mediacrawler_phone.py tests/test_mediacrawler_runtime.py tests/test_mediacrawler_qr.py`
Expected: PASS

### Task 4: Replace QR/phone workers and bridge wiring with controller entrypoint

**Files:**
- Modify: `scripts/run_login_qr_worker.py`
- Modify: `src/rednote_spider/config.py`
- Modify: `.env.example`
- Modify: `deploy/supervisor/runtime/rednote-login-qr.conf`
- Delete: `src/rednote_spider/login_qr_worker.py`
- Delete: `src/rednote_spider/login_phone_worker.py`
- Delete: `src/rednote_spider/login_phone_bridge.py`

**Step 1: Write the failing tests**

- Extend controller tests to assert the script entrypoint runs unified controller loop
- Add config tests covering new `LOGIN_RUNTIME_COMMAND`, profile dir, probe interval, and removed QR/phone-specific worker settings

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_login_controller.py tests/test_config.py`
Expected: FAIL with stale config/entrypoint behavior.

**Step 3: Write minimal implementation**

- Point supervisor entry to unified controller
- Replace QR/phone command parsing with one login runtime command
- Remove obsolete bridge/worker dependencies

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_login_controller.py tests/test_config.py`
Expected: PASS

### Task 5: Replace dual Streamlit panels with unified Login Control panel

**Files:**
- Modify: `ui/app.py`
- Modify: `tests/test_ui_app_helpers.py`

**Step 1: Write the failing tests**

- Cover:
  - unified state messages
  - action button enable/disable rules
  - event rendering helpers
  - removal of separate QR/Phone status text helpers

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_ui_app_helpers.py`
Expected: FAIL with missing unified helper functions or mismatched text.

**Step 3: Write minimal implementation**

- Replace split QR/Phone panel with single login control panel
- Display auth state, flow state, qr/security images, probe time, last error, recent events
- Wire buttons to controller actions

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_ui_app_helpers.py tests/test_login_controller_service.py`
Expected: PASS

### Task 6: Full focused verification and regression cleanup

**Files:**
- Modify: `README.md`
- Modify: `USAGE.md`
- Modify: `SCRIPTS_GUIDE.md`

**Step 1: Run focused login suite**

Run: `.venv/bin/python -m pytest -q tests/test_login_controller_service.py tests/test_login_controller.py tests/test_mediacrawler_login_runtime.py tests/test_mediacrawler_qr.py tests/test_mediacrawler_phone.py tests/test_mediacrawler_runtime.py tests/test_ui_app_helpers.py tests/test_config.py tests/test_init_schema_script.py tests/test_schema.py`
Expected: PASS

**Step 2: Run wider regression touching current login area**

Run: `.venv/bin/python -m pytest -q tests/test_run_managed_scheduler_script.py tests/test_send_login_expiry_alert.py tests/test_ui_security.py`
Expected: PASS

**Step 3: Run compile check**

Run: `.venv/bin/python -m compileall -q src scripts ui`
Expected: PASS

**Step 4: Update docs**

- Replace worker/bridge wording with controller/runtime wording
- Document single source of truth and human takeover flow

**Step 5: Optional restart**

Run: `.venv/bin/supervisorctl -c deploy/supervisor/supervisord.conf restart rednote-login-qr rednote-ui`
Expected: services restart cleanly
