# Streamlit Supervisor + Discover Controls Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 `ui/app.py` 以可托管 server 方式运行，并把 discover 循环的启停与循环间隔搬到页面可配置。

**Architecture:** 保留 Streamlit 作为 UI 层，新增数据库驱动的 scheduler 配置表与服务层。后台 discover/opportunity 常驻循环改为 Python daemon 每轮读取数据库配置，这样 supervisor 不需要重启就能吃到页面改动。UI 侧继续复用现有 watchlist 管理，并补一个更直观的 scheduler 控制区与 supervisor 部署脚本。

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, Streamlit, Supervisor, pytest

---

### Task 1: Add failing tests for scheduler config persistence

**Files:**
- Create: `tests/test_scheduler_config_service.py`
- Modify: `tests/test_config.py`

**Step 1: Write the failing test**
- 覆盖默认 discover/opportunity 配置自动补齐、更新 interval/enabled、非法 mode/interval 校验。

**Step 2: Run test to verify it fails**
Run: `PYTHONPATH=src pytest -q tests/test_scheduler_config_service.py tests/test_config.py`
Expected: FAIL，因为服务与新配置项尚不存在。

**Step 3: Write minimal implementation**
- 新增 scheduler config model / service / settings 字段。

**Step 4: Run test to verify it passes**
Run: `PYTHONPATH=src pytest -q tests/test_scheduler_config_service.py tests/test_config.py`
Expected: PASS

### Task 2: Add failing tests for managed scheduler daemon

**Files:**
- Create: `tests/test_run_managed_scheduler_script.py`

**Step 1: Write the failing test**
- 覆盖 discover disabled 时跳过执行；enabled 时执行一轮并落库。

**Step 2: Run test to verify it fails**
Run: `PYTHONPATH=src pytest -q tests/test_run_managed_scheduler_script.py`
Expected: FAIL，因为脚本尚不存在。

**Step 3: Write minimal implementation**
- 新增 `scripts/run_managed_scheduler.py`，discover/opportunity 每轮读库配置；支持 `--once` 便于验证。
- 让 `scripts/run_scheduled_loop.sh` 对 discover/opportunity 走新脚本。

**Step 4: Run test to verify it passes**
Run: `PYTHONPATH=src pytest -q tests/test_run_managed_scheduler_script.py`
Expected: PASS

### Task 3: Add UI and deployment support

**Files:**
- Modify: `ui/app.py`
- Create: `scripts/run_ui_server.sh`
- Modify: `scripts/start_app.sh`
- Create: `deploy/supervisor/supervisor-ui.conf`

**Step 1: Write the failing test**
- 不为 Streamlit 页面本身补 UI 自动化，改为依赖前两层自动化测试与编译验证。

**Step 2: Write minimal implementation**
- 页面新增 discover scheduler 控制区。
- watchlist 编辑体验从“手输 ID”优化为“选择现有关键词并编辑”。
- 提供 supervisor 可直接托管的 UI 启动脚本和模板。

**Step 3: Run targeted verification**
Run: `python -m compileall -q src scripts ui`
Expected: PASS

### Task 4: Update docs and run regression

**Files:**
- Modify: `README.md`
- Modify: `USAGE.md`
- Modify: `SCHEDULER_GUIDE.md`

**Step 1: Update docs**
- 说明 UI supervisor 部署、数据库驱动的 discover 配置、页面配置入口。

**Step 2: Run regression**
Run: `PYTHONPATH=src pytest -q tests/test_scheduler_config_service.py tests/test_run_managed_scheduler_script.py tests/test_discover_service.py tests/test_run_discover_cycle_script.py tests/test_schema.py tests/test_init_schema_script.py tests/test_config.py`
Expected: PASS
