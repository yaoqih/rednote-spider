# Terminal QR Login Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let scheduled MediaCrawler QR-code login print a scannable terminal QR and save a PNG inside `rednote-spider`.

**Architecture:** Add a local wrapper script that runs inside the MediaCrawler `uv` environment, monkey-patches MediaCrawler's QR display hook, and delegates to its existing `main.py`. Keep QR decoding/rendering logic inside `rednote-spider` so it is testable without editing the external `/root/MediaCrawler` checkout.

**Tech Stack:** Python 3.11+, Pillow, pytest, existing MediaCrawler `uv run` command flow.

---

### Task 1: QR helper module

**Files:**
- Create: `src/rednote_spider/mediacrawler_qr.py`
- Test: `tests/test_mediacrawler_qr.py`

**Step 1: Write the failing test**

Add tests that expect a helper to:
- accept base64/data-URL QR payloads
- save a deterministic PNG file
- print block-character QR art plus the saved file path

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_mediacrawler_qr.py -q`
Expected: FAIL because the helper module does not exist yet.

**Step 3: Write minimal implementation**

Implement helper functions to decode QR image bytes, render terminal block art, save PNG, and emit both artifacts.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_mediacrawler_qr.py -q`
Expected: PASS.

### Task 2: MediaCrawler wrapper script

**Files:**
- Create: `scripts/run_mediacrawler_with_terminal_qr.py`

**Step 1: Write the failing test**

Reuse helper tests as the contract; wrapper stays thin and delegates to the tested helper.

**Step 2: Write minimal implementation**

Create a wrapper that:
- adds `rednote-spider/src` to `sys.path`
- monkey-patches MediaCrawler `show_qrcode`
- prints QR to terminal and saves PNG under `logs/login_qr`
- executes MediaCrawler `main.py` with original arguments intact

**Step 3: Run targeted verification**

Run: `python scripts/run_mediacrawler_with_terminal_qr.py --help` from MediaCrawler cwd if needed, or validate via local import tests.

### Task 3: Default command template wiring

**Files:**
- Modify: `.env`
- Modify: `.env.example`

**Step 1: Update command template**

Replace direct `uv run main.py ...` with `uv run ../rednote-spider/scripts/run_mediacrawler_with_terminal_qr.py ...` so scheduled discover flow uses the wrapper by default.

**Step 2: Run regression checks**

Run: `pytest tests/test_mediacrawler_qr.py tests/test_run_external_crawler_script.py -q`
Expected: PASS.
