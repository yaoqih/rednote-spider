# Streamlit Login QR Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 给当前 Streamlit 控制台增加二维码登录面板，并提供后台 worker 支持手动刷新和超时自动刷新。

**Architecture:** 通过 SQLAlchemy 新增二维码会话表与服务层，把二维码生成/刷新动作从 UI 中拆到独立 worker 进程。UI 仅轮询数据库状态并展示图片路径和剩余有效期，worker 负责启动二维码命令、检测新 PNG、处理超时和自动续期。

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, Streamlit, Supervisor, pytest

---

### Task 1: Add failing tests for QR session service
**Files:**
- Create: `tests/test_login_qr_service.py`
- Modify: `tests/test_schema.py`

**Step 1: Write the failing test**
- 覆盖默认会话初始化、手动 refresh 请求、状态迁移、过期时间写入。

**Step 2: Run test to verify it fails**
Run: `.venv/bin/python -m pytest -q tests/test_login_qr_service.py tests/test_schema.py`
Expected: FAIL，因为表与服务尚不存在。

**Step 3: Write minimal implementation**
- 新增 `login_qr_session` model 与 `LoginQrService`。

**Step 4: Run test to verify it passes**
Run: `.venv/bin/python -m pytest -q tests/test_login_qr_service.py tests/test_schema.py`
Expected: PASS

### Task 2: Add failing tests for QR worker command parsing
**Files:**
- Create: `tests/test_run_login_qr_worker.py`

**Step 1: Write the failing test**
- 覆盖 refresh 请求触发生成、识别新 PNG、状态变成 `waiting_scan`、超时后过期。

**Step 2: Run test to verify it fails**
Run: `.venv/bin/python -m pytest -q tests/test_run_login_qr_worker.py`
Expected: FAIL，因为 worker 脚本尚不存在。

**Step 3: Write minimal implementation**
- 新增 `scripts/run_login_qr_worker.py`。
- 通过可配置命令运行二维码流程并写数据库状态。

**Step 4: Run test to verify it passes**
Run: `.venv/bin/python -m pytest -q tests/test_run_login_qr_worker.py`
Expected: PASS

### Task 3: Add UI QR panel and deployment wiring
**Files:**
- Modify: `ui/app.py`
- Modify: `src/rednote_spider/config.py`
- Create: `deploy/supervisor/runtime/rednote-login-qr.conf`
- Modify: `deploy/supervisor/supervisord.conf`

**Step 1: Implement UI**
- 新增 `Login QR` 面板，展示状态、二维码图片、倒计时、手动刷新按钮。

**Step 2: Implement deployment wiring**
- supervisor 接管 `rednote-login-qr`。

**Step 3: Run targeted verification**
Run: `.venv/bin/python -m compileall -q src scripts ui`
Expected: PASS

### Task 4: Restart services and verify access
**Files:**
- Modify: `.env`

**Step 1: Configure runtime command/env**
- 设置二维码 worker 命令与超时。

**Step 2: Run regression and restart**
Run: `.venv/bin/python -m pytest -q tests/test_login_qr_service.py tests/test_run_login_qr_worker.py tests/test_mediacrawler_qr.py tests/test_config.py && .venv/bin/supervisorctl -c deploy/supervisor/supervisord.conf restart rednote-login-qr rednote-ui`
Expected: PASS + RUNNING
