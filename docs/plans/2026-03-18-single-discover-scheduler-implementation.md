# Single Discover Scheduler Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Collapse separate opportunity scheduling into the discover scheduler while keeping opportunity retry behavior.

**Architecture:** Keep `ProductOpportunityService` intact and let the managed discover scheduler orchestrate it after each discover cycle. Remove standalone opportunity scheduling from config exposure, CLI exposure, UI exposure, and supervisor exposure, but keep manual opportunity repair tooling.

**Tech Stack:** Python 3.11, SQLAlchemy, Streamlit, pytest

---

### Task 1: Add failing scheduler tests

**Files:**
- Modify: `tests/test_run_managed_scheduler_script.py`
- Modify: `tests/test_scheduler_config_service.py`
- Modify: `tests/test_ui_app_helpers.py`

**Step 1: Write the failing tests**

Add tests that assert:

- discover mode runs opportunity after discover
- CLI rejects `--mode opportunity`
- scheduler config listing/UI helper only surfaces discover

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_run_managed_scheduler_script.py tests/test_scheduler_config_service.py tests/test_ui_app_helpers.py -v`

Expected: FAIL because code still supports a standalone opportunity scheduler.

**Step 3: Write minimal implementation**

Update scheduler/config/UI code to satisfy the new behavior.

**Step 4: Run tests to verify they pass**

Run the same pytest command and confirm PASS.

### Task 2: Implement runtime and config changes

**Files:**
- Modify: `scripts/run_managed_scheduler.py`
- Modify: `src/rednote_spider/services/scheduler_config_service.py`
- Modify: `ui/app.py`
- Modify/Delete: `deploy/supervisor/runtime/rednote-opportunity.conf`

**Step 1: Change discover orchestration**

Make discover mode return a composite summary:

- `discover`
- `opportunity`

using `ProductOpportunityService.process_recent_done_tasks(...)`.

**Step 2: Remove standalone opportunity mode exposure**

Limit supported scheduler modes and UI selectors to discover only.

**Step 3: Update deployment artifacts**

Remove separate supervisor/runtime config for opportunity scheduler.

**Step 4: Run targeted tests**

Run: `.venv/bin/pytest tests/test_run_managed_scheduler_script.py tests/test_scheduler_config_service.py tests/test_ui_app_helpers.py -v`

Expected: PASS.

### Task 3: Regression verification and integration

**Files:**
- Modify: docs/plans/2026-03-18-single-discover-scheduler-design.md
- Modify: docs/plans/2026-03-18-single-discover-scheduler-implementation.md

**Step 1: Run broader regressions**

Run:

- `.venv/bin/pytest tests/test_discover_service.py tests/test_manual_task_pipeline_service.py tests/test_run_managed_scheduler_script.py tests/test_scheduler_config_service.py tests/test_ui_app_helpers.py -v`
- `.venv/bin/pytest -q`

Expected: PASS.

**Step 2: Commit**

Run:

```bash
git add relevant-files
git commit -m "refactor: fold opportunity scheduling into discover"
```

**Step 3: Push**

Run:

```bash
git push origin main
```
