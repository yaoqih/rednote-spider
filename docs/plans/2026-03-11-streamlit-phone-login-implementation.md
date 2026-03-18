# Streamlit Phone Login Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 给 Streamlit 增加可用的小红书手机号验证码登录入口，并让现有后台登录 worker 支持 phone 流程。

**Architecture:** 保留现有二维码登录表和 worker，不重命名旧模块；新增 phone session/service/worker，并在现有登录包装脚本里注入 MediaCrawler 手机号登录补丁。验证码通过 rednote-spider 数据库跨进程共享，不再依赖 MediaCrawler 的本地内存 cache。

**Tech Stack:** Python 3.12, SQLAlchemy, Streamlit, pytest, subprocess, Playwright(MediaCrawler runtime patch)

---

### Task 1: Phone Session Model And Service

**Files:**
- Modify: `src/rednote_spider/models.py`
- Create: `src/rednote_spider/services/login_phone_service.py`
- Modify: `scripts/init_schema.py`
- Test: `tests/test_login_phone_service.py`
- Test: `tests/test_init_schema_script.py`

**Step 1: Write the failing test**

新增测试覆盖默认会话初始化、开始登录、进入等待验证码、提交验证码、消费验证码、成功/失败状态。

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_login_phone_service.py`
Expected: FAIL with missing model/service symbols.

**Step 3: Write minimal implementation**

新增 `LoginPhoneSession` 模型与 `LoginPhoneStatus`，实现 `LoginPhoneService`。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_login_phone_service.py tests/test_init_schema_script.py`
Expected: PASS

### Task 2: Phone Worker Runtime

**Files:**
- Create: `src/rednote_spider/login_phone_worker.py`
- Modify: `scripts/run_login_qr_worker.py`
- Test: `tests/test_login_phone_worker.py`

**Step 1: Write the failing test**

覆盖启动手机号登录命令、读取阶段标记、验证码提交后成功、失败日志回写。

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_login_phone_worker.py`
Expected: FAIL with missing worker module/functions.

**Step 3: Write minimal implementation**

实现 phone worker iteration、子进程 stdout/stderr tail、阶段标记解析和 runtime 清理，并在现有 worker 脚本里一并轮询。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_login_phone_worker.py tests/test_login_qr_worker.py`
Expected: PASS

### Task 3: MediaCrawler Runtime Patch

**Files:**
- Create: `src/rednote_spider/mediacrawler_phone.py`
- Modify: `scripts/run_mediacrawler_with_terminal_qr.py`
- Modify: `src/rednote_spider/config.py`
- Test: `tests/test_mediacrawler_phone.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

覆盖阶段标记解析、手机号环境变量读取、配置项加载。

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_mediacrawler_phone.py tests/test_config.py`
Expected: FAIL with missing config or patch helpers.

**Step 3: Write minimal implementation**

实现 MediaCrawler 运行时 phone patch，并把现有包装脚本扩展成同时支持 QR/phone。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_mediacrawler_phone.py tests/test_config.py`
Expected: PASS

### Task 4: Streamlit Entry

**Files:**
- Modify: `ui/app.py`
- Test: `tests/test_ui_app_helpers.py`

**Step 1: Write the failing test**

补 phone 状态文案与 helper 测试。

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_ui_app_helpers.py`
Expected: FAIL with missing phone helper or mismatched text.

**Step 3: Write minimal implementation**

在 Login QR tab 中增加 Phone Login 区块，提供手机号输入、开始登录、验证码输入、提交按钮、状态与错误展示。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_ui_app_helpers.py`
Expected: PASS

### Task 5: Full Verification

**Files:**
- Modify: `.env` (如需补 `LOGIN_PHONE_*`)

**Step 1: Run focused suite**

Run: `.venv/bin/python -m pytest -q tests/test_login_phone_service.py tests/test_login_phone_worker.py tests/test_mediacrawler_phone.py tests/test_ui_app_helpers.py tests/test_config.py tests/test_init_schema_script.py`
Expected: PASS

**Step 2: Run wider regression touching login area**

Run: `.venv/bin/python -m pytest -q tests/test_login_qr_service.py tests/test_login_qr_worker.py tests/test_mediacrawler_qr.py`
Expected: PASS

**Step 3: Rollout check**

Run: `.venv/bin/python scripts/init_schema.py`
Expected: `schema_init=ok`

**Step 4: Optional runtime restart**

Run: `supervisorctl -c deploy/supervisor/supervisord.conf restart rednote-login-qr rednote-ui`
Expected: services restarted cleanly
