from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "send_login_expiry_alert.py"
    spec = importlib.util.spec_from_file_location("send_login_expiry_alert", script_path)
    if spec is None or spec.loader is None:
        raise AssertionError("failed to load send_login_expiry_alert.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_classify_scheduler_issue_prioritizes_browser_unavailable_over_generic_qr_timeout_hint():
    mod = _load_module()
    log_text = "\n".join(
        [
            "discover command failed: command timed out after 600s",
            "2026-03-11 04:31:57 MediaCrawler INFO (core.py:74) - [XiaoHongShuCrawler] Launching browser using CDP mode",
            "2026-03-11 04:31:57 MediaCrawler ERROR (cdp_browser.py:132) - [CDPBrowserManager] CDP browser launch failed: No available browser found. Please ensure Chrome or Edge browser is installed.",
            "可能卡在扫码/验证码，或爬虫执行时间过长。",
        ]
    )

    issue = mod.classify_scheduler_issue(log_text)

    assert issue is not None
    assert issue.code == "browser_unavailable"
    assert issue.attach_qr is False
    assert "Chrome" in "\n".join(issue.evidence)


def test_classify_scheduler_issue_detects_login_expiry_and_builds_actionable_body():
    mod = _load_module()
    log_text = "\n".join(
        [
            "waiting for scan code login",
            "login state result: false",
            "QR_CODE_IMAGE_PATH=/tmp/xhs-login.png",
        ]
    )

    issue = mod.classify_scheduler_issue(log_text)

    assert issue is not None
    assert issue.code == "login_expired"
    assert issue.attach_qr is True

    body = mod.build_body(mode="discover", now_ts=1234567890, log_text=log_text, issue=issue)
    assert "故障分类" in body
    assert "login_expired" in body
    assert "建议动作" in body
    assert "扫码" in body
