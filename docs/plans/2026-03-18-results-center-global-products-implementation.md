# Results Center Global Products Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the Streamlit Results page into a global product overview and a task-scoped diagnostics view without changing schema.

**Architecture:** Keep all data access in `ui/app.py` helper functions. Add one new global product aggregation helper and refactor the Results renderer into small drawing helpers so product state and task state remain explicitly separated.

**Tech Stack:** Python 3.11, Streamlit, SQLAlchemy, pytest

---

### Task 1: Add failing tests for global product overview

**Files:**
- Modify: `tests/test_ui_app_helpers.py`
- Test: `tests/test_ui_app_helpers.py`

**Step 1: Write the failing test**

Add a test that creates multiple tasks, products, assessments, and product opportunities, then asserts a new helper returns global product rows and summary metrics.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_app_helpers.py -k product_overview -v`

Expected: FAIL because `_fetch_product_overview` does not exist yet.

**Step 3: Write minimal implementation**

Add `_fetch_product_overview` in `ui/app.py` and return the expected fields.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_app_helpers.py -k product_overview -v`

Expected: PASS.

### Task 2: Refactor Results page structure

**Files:**
- Modify: `ui/app.py`
- Test: `tests/test_ui_app_helpers.py`

**Step 1: Write the failing test**

Add/extend helper-level assertions for empty product overview behavior and stable sorting if needed.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_app_helpers.py -k product_overview -v`

Expected: FAIL on missing fields or summary shape.

**Step 3: Write minimal implementation**

Split `_draw_pipeline_results` into:

- `_draw_global_product_results`
- `_draw_task_result_view`
- `_draw_pipeline_results`

Use tabs to separate global products and task results. Update task product tab copy to clarify it shows current product state.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui_app_helpers.py -v`

Expected: PASS.

### Task 3: Run regression verification

**Files:**
- Modify: `ui/app.py`
- Test: `tests/test_ui_app_helpers.py`

**Step 1: Run focused regression**

Run: `pytest tests/test_ui_app_helpers.py tests/test_manual_task_pipeline_service.py -v`

Expected: PASS.

**Step 2: Run broader UI-adjacent regression if fast**

Run: `pytest tests/test_manual_task_pipeline_service.py tests/test_ui_app_helpers.py tests/test_scheduler_config_service.py -v`

Expected: PASS.

**Step 3: Review diff**

Run: `git diff -- ui/app.py tests/test_ui_app_helpers.py docs/plans/2026-03-18-results-center-global-products-design.md docs/plans/2026-03-18-results-center-global-products-implementation.md`

Expected: Only Results view redesign and tests.
