#!/usr/bin/env python3
"""Send scheduler alert email when login expiry is detected in cycle logs."""

from __future__ import annotations

import argparse
import base64
import html
import os
import re
import smtplib
import tempfile
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


LOGIN_EXPIRY_HINTS = (
    "可能卡在扫码/验证码",
    "请通过验证",
    "please verify manually",
    "login xiaohongshu failed by qrcode",
    "waiting for scan code login",
    "check login state failed",
    "login state result: false",
    "login_required",
)
TIMEOUT_HINTS = (
    "command timed out after",
    "crawler command timed out after",
)
BROWSER_UNAVAILABLE_HINTS = (
    "no available browser found",
    "please ensure chrome or edge browser is installed",
    "set custom_browser_path",
)
ENV_WARNING_HINTS = (
    "warning: `virtual_env=",
    "use `--active` to target the active environment instead",
)
NOISY_LINE_HINTS = (
    "warning: `virtual_env=",
)


@dataclass(frozen=True, slots=True)
class SchedulerIssue:
    code: str
    subject_text: str
    evidence: tuple[str, ...]
    actions: tuple[str, ...]
    attach_qr: bool = False


def load_dotenv(path: Path) -> None:
    """Load key=value from .env without overriding existing env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'").strip('"')


def is_truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _contains_any(log_text: str, patterns: tuple[str, ...]) -> bool:
    lowered = log_text.lower()
    return any(token in lowered or token in log_text for token in patterns)


def _extract_matching_lines(log_text: str, patterns: tuple[str, ...], *, limit: int = 5) -> tuple[str, ...]:
    lowered_patterns = tuple(token.lower() for token in patterns)
    hits: list[str] = []
    for raw in log_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(token in lowered for token in lowered_patterns):
            hits.append(line)
        if len(hits) >= limit:
            break
    return tuple(hits)


def _build_log_tail(log_text: str, *, limit: int = 80) -> str:
    lines = [line for line in log_text.splitlines() if line.strip()]
    filtered = [
        line for line in lines
        if not any(noise in line.lower() for noise in NOISY_LINE_HINTS)
    ]
    tail_lines = filtered[-limit:] if filtered else lines[-limit:]
    return "\n".join(tail_lines) if tail_lines else "(no log content)"


def classify_scheduler_issue(log_text: str) -> SchedulerIssue | None:
    if _contains_any(log_text, BROWSER_UNAVAILABLE_HINTS):
        return SchedulerIssue(
            code="browser_unavailable",
            subject_text="爬虫浏览器不可用",
            evidence=_extract_matching_lines(log_text, BROWSER_UNAVAILABLE_HINTS),
            actions=(
                "确认服务器已安装 Chrome/Edge，或在 MediaCrawler 配置里设置 CUSTOM_BROWSER_PATH。",
                "如果是容器/无桌面环境，确认浏览器二进制和运行依赖完整。",
                "浏览器问题修复前，不要把本次失败按登录过期处理。",
            ),
            attach_qr=False,
        )
    if _contains_any(log_text, LOGIN_EXPIRY_HINTS):
        return SchedulerIssue(
            code="login_expired",
            subject_text="登录疑似过期或卡在扫码/验证码",
            evidence=_extract_matching_lines(log_text, LOGIN_EXPIRY_HINTS),
            actions=(
                "打开 UI 的 Login QR 面板，重新生成二维码并扫码。",
                "如果页面提示验证码/人工验证，先在浏览器里完成验证。",
                "扫码完成后再重跑 discover，确认失败不再复现。",
            ),
            attach_qr=True,
        )
    if _contains_any(log_text, TIMEOUT_HINTS):
        return SchedulerIssue(
            code="crawler_timeout",
            subject_text="爬虫执行超时",
            evidence=_extract_matching_lines(log_text, TIMEOUT_HINTS),
            actions=(
                "检查外部爬虫是否卡死、是否存在长时间等待页面加载或阻塞。",
                "结合近期日志确认是资源瓶颈、网站风控，还是 timeout 配置偏小。",
                "必要时临时提高 crawler timeout，但先定位根因再放大超时时间。",
            ),
            attach_qr=False,
        )
    return None


def should_send_by_cooldown(state_file: Path, cooldown_seconds: int, now_ts: int) -> bool:
    try:
        last_ts = int(state_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return True
    return now_ts - last_ts >= max(cooldown_seconds, 0)


def mark_sent(state_file: Path, now_ts: int) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(str(now_ts), encoding="utf-8")


def build_body(*, mode: str, now_ts: int, log_text: str, issue: SchedulerIssue | None = None) -> str:
    issue = issue or classify_scheduler_issue(log_text)
    if issue is None:
        raise ValueError("issue classification is required to build alert body")
    tail = _build_log_tail(log_text)
    env_warning_note = (
        "\n额外提示: 检测到 uv 的 VIRTUAL_ENV mismatch warning；它通常是噪音，不是本次主因。\n"
        if _contains_any(log_text, ENV_WARNING_HINTS)
        else ""
    )
    evidence_block = "\n".join(f"- {line}" for line in issue.evidence) if issue.evidence else "- (none)"
    action_block = "\n".join(f"- {line}" for line in issue.actions)
    return (
        "rednote scheduler 检测到需要人工处理的故障。\n"
        f"模式: {mode}\n"
        f"时间戳: {now_ts}\n\n"
        f"故障分类: {issue.code}\n"
        f"告警标题: {issue.subject_text}\n\n"
        "匹配证据:\n"
        f"{evidence_block}\n\n"
        "建议动作:\n"
        f"{action_block}\n"
        f"{env_warning_note}\n"
        "日志尾部（最多80行）:\n"
        f"{tail}\n"
    )


def extract_latest_qrcode_png(log_text: str) -> tuple[bytes | None, str]:
    """Extract latest QR image bytes from log marker output."""
    matches = re.findall(r"QR_CODE_IMAGE_PATH=(.+)", log_text)
    if matches:
        raw = matches[-1].strip()
        qr_path = Path(raw).expanduser()
    else:
        raw = os.getenv("MEDIACRAWLER_QR_SAVE_PATH", "").strip()
        qr_path = (
            Path(raw).expanduser()
            if raw
            else Path(tempfile.gettempdir()) / "mediacrawler_latest_qrcode.png"
        )
    if not qr_path.exists() or not qr_path.is_file():
        return None, str(qr_path)
    try:
        return qr_path.read_bytes(), str(qr_path)
    except OSError:
        return None, str(qr_path)


def send_email(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    to_email: str,
    subject: str,
    body: str,
    qr_png: bytes | None = None,
    qr_path_hint: str = "",
) -> None:
    message = EmailMessage()
    message["From"] = username
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body, subtype="plain", charset="utf-8")
    if qr_png:
        img_b64 = base64.b64encode(qr_png).decode("ascii")
        safe_hint = html.escape(qr_path_hint or "(unknown)")
        safe_tail = html.escape(body)
        message.add_alternative(
            (
                "<html><body>"
                "<p><strong>检测到登录疑似过期，可直接扫码：</strong></p>"
                f"<p>二维码文件: <code>{safe_hint}</code></p>"
                f"<img src=\"data:image/png;base64,{img_b64}\" "
                "style=\"max-width:420px;border:1px solid #ddd;padding:8px;\"/>"
                "<hr/>"
                f"<pre style=\"white-space:pre-wrap;\">{safe_tail}</pre>"
                "</body></html>"
            ),
            subtype="html",
        )

    with smtplib.SMTP_SSL(host=host, port=port, timeout=20) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send login expiry email alert by scheduler logs")
    parser.add_argument("--mode", required=True, help="scheduler mode: discover/all")
    parser.add_argument("--log-file", required=True, help="log file path generated by loop script")
    args = parser.parse_args()

    load_dotenv(Path(".env"))
    enabled = is_truthy(os.getenv("SCHED_LOGIN_ALERT_ENABLED"), default=True)
    if not enabled:
        return

    log_path = Path(args.log_file)
    if not log_path.exists():
        return
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    issue = classify_scheduler_issue(log_text)
    if issue is None:
        return

    username = os.getenv("SCHED_LOGIN_ALERT_FROM_EMAIL", "").strip()
    password = os.getenv("SCHED_LOGIN_ALERT_PASSWORD", "").strip()
    to_email = os.getenv("SCHED_LOGIN_ALERT_TO_EMAIL", username).strip()
    smtp_host = os.getenv("SCHED_LOGIN_ALERT_SMTP_HOST", "smtp.qq.com").strip() or "smtp.qq.com"
    smtp_port = int(os.getenv("SCHED_LOGIN_ALERT_SMTP_PORT", "465"))
    subject_prefix = os.getenv("SCHED_LOGIN_ALERT_SUBJECT_PREFIX", "[rednote-spider]").strip() or "[rednote-spider]"
    cooldown_seconds = int(os.getenv("SCHED_LOGIN_ALERT_COOLDOWN_SECONDS", "21600"))
    state_file = Path(
        os.getenv("SCHED_LOGIN_ALERT_STATE_FILE", "/tmp/rednote_scheduler_login_alert.state")
    ).expanduser()

    if not username or not password or not to_email:
        print("[login-alert] skip: missing email config", flush=True)
        return

    now_ts = int(time.time())
    if not should_send_by_cooldown(state_file, cooldown_seconds, now_ts):
        print("[login-alert] skip: within cooldown window", flush=True)
        return

    qr_png, qr_path_hint = extract_latest_qrcode_png(log_text) if issue.attach_qr else (None, "")
    subject = f"{subject_prefix} {issue.subject_text}（{args.mode}）"
    body = build_body(mode=args.mode, now_ts=now_ts, log_text=log_text)
    send_email(
        host=smtp_host,
        port=smtp_port,
        username=username,
        password=password,
        to_email=to_email,
        subject=subject,
        body=body,
        qr_png=qr_png,
        qr_path_hint=qr_path_hint,
    )
    mark_sent(state_file, now_ts)
    print(f"[login-alert] sent issue={issue.code} to {to_email}", flush=True)


if __name__ == "__main__":
    main()
