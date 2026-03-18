"""Runtime helpers for MediaCrawler phone login patching."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from tenacity import RetryError

from .login_runtime_events import format_login_runtime_event

PHONE_STAGE_PREFIX = "[rednote-phone]"
SECURITY_VERIFICATION_STAGE = "need_verify"
SECURITY_VERIFICATION_TIMEOUT_SECONDS = 180
SECURITY_VERIFICATION_MESSAGE = "检测到小红书安全校验，请使用已登录小红书 App 扫描二维码后继续。"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SECURITY_OUTPUT_DIR = PROJECT_ROOT / "logs" / "login_security"
_runtime_attempt_id: int = 0
_runtime_platform: str = "xhs"
_runtime_sms_code_provider: Any = None
_runtime_event_emitter: Any = None
PHONE_LOGIN_TRIGGER_SELECTORS = (
    "xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button",
    "xpath=//button[contains(normalize-space(), '登录')]",
    "xpath=//button[contains(normalize-space(), '注册')]",
    "text=登录",
    "text=注册",
)
PHONE_LOGIN_TOGGLE_SELECTORS = (
    'xpath=//div[contains(@class, "login-container")]//div[contains(@class, "other-method")]//div[1]',
    "xpath=//div[contains(@class, 'other-method')]//*[contains(normalize-space(), '验证码')]",
    "xpath=//div[contains(@class, 'other-method')]//*[contains(normalize-space(), '手机')]",
    "text=验证码",
    "text=手机",
)
PHONE_INPUT_SELECTORS = (
    "label.phone > input",
    "input[placeholder*='手机号']",
    "input[autocomplete='tel']",
    "input[type='tel']",
    "input[placeholder*='手机']",
)
PHONE_SEND_CODE_SELECTORS = (
    "label.auth-code > span",
    "xpath=//span[contains(normalize-space(), '发送验证码')]",
    "xpath=//button[contains(normalize-space(), '发送验证码')]",
    "xpath=//button[contains(normalize-space(), '获取验证码')]",
    "text=发送验证码",
    "text=获取验证码",
)
PHONE_SMS_CODE_INPUT_SELECTORS = (
    "label.auth-code > input",
    "input[placeholder*='验证码']",
    "input[inputmode='numeric']",
    "input[autocomplete='one-time-code']",
    "input[placeholder*='短信']",
)
PHONE_SUBMIT_SELECTORS = (
    "div.input-container > button",
    "xpath=//button[contains(normalize-space(), '登录')]",
    "xpath=//button[contains(normalize-space(), '立即登录')]",
    "xpath=//button[contains(normalize-space(), '确认')]",
    "xpath=//*[self::button or self::div or self::span][contains(normalize-space(), '登录')]",
    "xpath=//*[self::button or self::div or self::span][contains(normalize-space(), '确认')]",
    "text=登录",
    "text=立即登录",
    "text=确认",
)
PHONE_CONTINUE_SELECTORS = (
    "xpath=//button[contains(normalize-space(), '同意并继续')]",
    "xpath=//button[contains(normalize-space(), '继续')]",
    "xpath=//span[contains(normalize-space(), '同意并继续')]/ancestor::button[1]",
    "xpath=//span[contains(normalize-space(), '继续')]/ancestor::button[1]",
    "xpath=//*[self::button or self::div or self::span][contains(normalize-space(), '同意并继续')]",
    "xpath=//*[self::button or self::div or self::span][contains(normalize-space(), '继续')]",
    "text=同意并继续",
    "text=继续",
)
PHONE_AGREEMENT_SELECTORS = (
    "xpath=//div[contains(@class, 'agreements')]//*[local-name()='svg']",
    "xpath=//div[contains(@class, 'agreements')]//input[@type='checkbox']",
)


def normalize_phone_number(raw: str) -> str:
    token = re.sub(r"\D+", "", raw or "")
    if token.startswith("86") and len(token) > 11:
        token = token[2:]
    if len(token) != 11:
        raise ValueError("phone number must contain 11 digits")
    return token


def normalize_sms_code(raw: str) -> str:
    token = re.sub(r"\D+", "", raw or "")
    if len(token) != 6:
        raise ValueError("sms code must contain 6 digits")
    return token


def format_phone_stage_marker(stage: str, message: str = "") -> str:
    payload = {"stage": stage, "message": message}
    return f"{PHONE_STAGE_PREFIX}{json.dumps(payload, ensure_ascii=False)}"


def parse_phone_stage_marker(line: str) -> dict[str, str] | None:
    raw = (line or "").strip()
    if not raw.startswith(PHONE_STAGE_PREFIX):
        return None
    try:
        payload = json.loads(raw[len(PHONE_STAGE_PREFIX):].strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    stage = str(payload.get("stage") or "").strip()
    if not stage:
        return None
    return {"stage": stage, "message": str(payload.get("message") or "")}


def _emit_phone_stage(stage: str, message: str = "") -> None:
    print(format_phone_stage_marker(stage, message), file=sys.stderr, flush=True)


def _resolve_attempt_id() -> int:
    if _runtime_attempt_id > 0:
        return int(_runtime_attempt_id)
    raw = os.getenv("REDNOTE_LOGIN_ATTEMPT_ID", "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _emit_login_runtime_event(event_type: str, message: str = "", **payload: object) -> None:
    attempt_id = _resolve_attempt_id()
    if attempt_id <= 0:
        return
    if callable(_runtime_event_emitter):
        _runtime_event_emitter(event_type, message, **payload)
        return
    print(
        format_login_runtime_event(
            event_type,
            message,
            attempt_id=attempt_id,
            **payload,
        ),
        file=sys.stderr,
        flush=True,
    )


def _emit_phone_state(stage: str, message: str = "", **payload: object) -> None:
    _emit_phone_stage(stage, message)
    if stage == "waiting_code":
        _emit_login_runtime_event("waiting_phone_code", message, **payload)
    elif stage == SECURITY_VERIFICATION_STAGE:
        _emit_login_runtime_event("waiting_security_verification", message, **payload)
    elif stage == "verifying":
        _emit_login_runtime_event("verifying", message, **payload)


@lru_cache(maxsize=4)
def _build_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, future=True)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def _consume_submitted_sms_code(*, start_nonce: int) -> str | None:
    if callable(_runtime_sms_code_provider):
        return _runtime_sms_code_provider(int(start_nonce))
    database_url = os.getenv("REDNOTE_LOGIN_DATABASE_URL", "").strip()
    platform = _runtime_platform or os.getenv("REDNOTE_LOGIN_PLATFORM", "xhs").strip() or "xhs"
    attempt_id = _resolve_attempt_id()
    if database_url and attempt_id > 0:
        from .services.login_controller_service import LoginControllerService

        return LoginControllerService(_build_session_factory(database_url)).consume_submitted_sms_code(
            attempt_id=attempt_id,
            platform=platform,
        )
    return None


def configure_phone_login_runtime(
    *,
    attempt_id: int,
    platform: str = "xhs",
    sms_code_provider=None,
    event_emitter=None,
) -> None:
    global _runtime_attempt_id, _runtime_platform, _runtime_sms_code_provider, _runtime_event_emitter
    _runtime_attempt_id = max(0, int(attempt_id))
    _runtime_platform = str(platform or "xhs").strip() or "xhs"
    _runtime_sms_code_provider = sms_code_provider
    _runtime_event_emitter = event_emitter


def reset_phone_login_runtime() -> None:
    global _runtime_attempt_id, _runtime_platform, _runtime_sms_code_provider, _runtime_event_emitter
    _runtime_attempt_id = 0
    _runtime_platform = "xhs"
    _runtime_sms_code_provider = None
    _runtime_event_emitter = None


def _default_phone_code_timeout_seconds() -> int:
    raw = os.getenv("REDNOTE_LOGIN_CODE_TIMEOUT_SECONDS", "120")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 120


def is_security_verification_context(
    *,
    title: str = "",
    body_text: str = "",
    frame_urls: list[str] | tuple[str, ...] | None = None,
) -> bool:
    title_token = (title or "").strip().lower()
    body_token = re.sub(r"\s+", " ", body_text or "").strip().lower()
    urls = " ".join(frame_urls or []).lower()
    if "security verification" in title_token:
        return True
    if "scan with logged-in" in body_token and "account security" in body_token:
        return True
    if "请通过验证" in body_token:
        return True
    if "website-login/captcha" in urls or "verifybiz=" in urls or "verifytype=" in urls:
        return True
    return False


def _iter_selector_roots(context_page: Any) -> list[Any]:
    roots = [context_page]
    for frame in getattr(context_page, "frames", []) or []:
        if frame is context_page:
            continue
        roots.append(frame)
    return roots


async def _wait_for_first_selector(
    roots: list[Any],
    selectors: tuple[str, ...] | list[str],
    *,
    timeout_ms: int,
) -> tuple[Any | None, str | None, Any | None]:
    for selector in selectors:
        for root in roots:
            try:
                handle = await root.wait_for_selector(selector, timeout=timeout_ms)
            except Exception:
                continue
            if handle is not None:
                return handle, selector, root
    return None, None, None


async def _click_first_visible(
    roots: list[Any],
    selectors: tuple[str, ...] | list[str],
    *,
    timeout_ms: int,
) -> tuple[Any | None, str | None, Any | None]:
    for selector in selectors:
        for root in roots:
            try:
                handle = await root.wait_for_selector(selector, timeout=timeout_ms)
            except Exception:
                continue
            if handle is None:
                continue
            try:
                await handle.click()
            except Exception:
                continue
            return handle, selector, root
    return None, None, None


def determine_phone_login_mode(
    *,
    send_button: Any | None,
    sms_code_input: Any | None,
    submit_button: Any | None,
    continue_button: Any | None,
) -> str:
    del submit_button
    if send_button is not None or sms_code_input is not None:
        return "direct_sms"
    if continue_button is not None:
        return "continue_then_code"
    return "incomplete"


async def _collect_phone_debug_context(context_page: Any) -> str:
    parts: list[str] = []
    try:
        title = await context_page.title()
    except Exception:
        title = ""
    if title:
        parts.append(f"title={title}")
    try:
        body_text = await context_page.text_content("body")
    except Exception:
        body_text = ""
    normalized_body = re.sub(r"\s+", " ", body_text or "").strip()
    if normalized_body:
        parts.append(f"body={normalized_body[:200]}")
    frame_urls = [str(getattr(frame, "url", "")).strip() for frame in _iter_selector_roots(context_page)[1:]]
    frame_urls = [item for item in frame_urls if item]
    if frame_urls:
        parts.append("frames=" + ", ".join(frame_urls[:3]))
    return "; ".join(parts) or "no debug context"


async def _root_context_snapshot(root: Any) -> tuple[str, str, str]:
    title = ""
    body_text = ""
    try:
        if hasattr(root, "title"):
            title = await root.title()
    except Exception:
        title = ""
    try:
        body_text = await root.text_content("body")
    except Exception:
        body_text = ""
    return title, body_text or "", str(getattr(root, "url", "") or "")


async def _detect_security_verification(context_page: Any) -> tuple[Any | None, str]:
    roots = _iter_selector_roots(context_page)
    frame_urls: list[str] = []
    for root in roots:
        title, body_text, url = await _root_context_snapshot(root)
        if url:
            frame_urls.append(url)
        if is_security_verification_context(title=title, body_text=body_text, frame_urls=[url]):
            return root, body_text
    title, body_text, _ = await _root_context_snapshot(context_page)
    if is_security_verification_context(title=title, body_text=body_text, frame_urls=frame_urls):
        return context_page, body_text
    return None, ""


async def _save_security_verification_snapshot(root: Any, context_page: Any, *, start_nonce: int) -> str | None:
    output_dir = Path(
        os.getenv("REDNOTE_SECURITY_OUTPUT_DIR", str(DEFAULT_SECURITY_OUTPUT_DIR))
    ).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"xhs-security-{int(start_nonce)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    try:
        if root is context_page:
            await context_page.screenshot(path=str(output_path))
        else:
            frame_element = await root.frame_element()
            await frame_element.screenshot(path=str(output_path))
    except Exception:
        try:
            await context_page.screenshot(path=str(output_path))
        except Exception:
            return None
    return str(output_path)


async def _click_phone_agreement_if_present(context_page: Any) -> bool:
    agreement_roots = _iter_selector_roots(context_page)
    agree_privacy_ele, _, _ = await _click_first_visible(
        agreement_roots,
        PHONE_AGREEMENT_SELECTORS,
        timeout_ms=1200,
    )
    return agree_privacy_ele is not None


async def _resolve_sms_step_controls(
    context_page: Any,
    *,
    timeout_seconds: int = 10,
) -> tuple[Any | None, Any | None, Any | None]:
    deadline = time.time() + max(1, timeout_seconds)
    send_button: Any | None = None
    sms_code_input: Any | None = None
    submit_button: Any | None = None
    while time.time() < deadline:
        roots = _iter_selector_roots(context_page)
        send_button, _, _ = await _wait_for_first_selector(roots, PHONE_SEND_CODE_SELECTORS, timeout_ms=800)
        sms_code_input, _, _ = await _wait_for_first_selector(roots, PHONE_SMS_CODE_INPUT_SELECTORS, timeout_ms=800)
        submit_button, _, _ = await _wait_for_first_selector(roots, PHONE_SUBMIT_SELECTORS, timeout_ms=800)
        if sms_code_input is not None or send_button is not None:
            return send_button, sms_code_input, submit_button
        await asyncio.sleep(0.5)
    return send_button, sms_code_input, submit_button


async def _fill_sms_code_input(code_input: Any, code: str) -> None:
    await code_input.fill(value=code)


async def _find_phone_login_controls_once(context_page: Any, logger: Any) -> tuple[Any, Any | None, Any | None, Any | None, str]:
    roots = _iter_selector_roots(context_page)
    phone_input, selector, _ = await _wait_for_first_selector(roots, PHONE_INPUT_SELECTORS, timeout_ms=1500)
    if phone_input is None:
        toggle_handle, toggle_selector, _ = await _click_first_visible(
            roots,
            PHONE_LOGIN_TOGGLE_SELECTORS,
            timeout_ms=1200,
        )
        if toggle_handle is not None:
            logger.info(
                "[rednote_spider.phone_login] Switched login method via selector: %s",
                toggle_selector,
            )
            await asyncio.sleep(0.8)

        if phone_input is None:
            trigger_handle, trigger_selector, _ = await _click_first_visible(
                roots,
                PHONE_LOGIN_TRIGGER_SELECTORS,
                timeout_ms=1500,
            )
            if trigger_handle is not None:
                logger.info(
                    "[rednote_spider.phone_login] Opened login dialog via selector: %s",
                    trigger_selector,
                )
                await asyncio.sleep(1)

            roots = _iter_selector_roots(context_page)
            toggle_handle, toggle_selector, _ = await _click_first_visible(
                roots,
                PHONE_LOGIN_TOGGLE_SELECTORS,
                timeout_ms=1500,
            )
            if toggle_handle is not None:
                logger.info(
                    "[rednote_spider.phone_login] Switched login method via selector: %s",
                    toggle_selector,
                )
                await asyncio.sleep(0.8)

        roots = _iter_selector_roots(context_page)
        phone_input, selector, _ = await _wait_for_first_selector(
            roots,
            PHONE_INPUT_SELECTORS,
            timeout_ms=5000,
        )
        if phone_input is None:
            debug_context = await _collect_phone_debug_context(context_page)
            raise RuntimeError(f"xhs phone login form not found; {debug_context}")
        logger.info(
            "[rednote_spider.phone_login] Found phone input via selector: %s",
            selector,
        )

    roots = _iter_selector_roots(context_page)
    send_button, send_selector, _ = await _wait_for_first_selector(
        roots,
        PHONE_SEND_CODE_SELECTORS,
        timeout_ms=1500,
    )
    sms_code_input, sms_selector, _ = await _wait_for_first_selector(
        roots,
        PHONE_SMS_CODE_INPUT_SELECTORS,
        timeout_ms=1500,
    )
    submit_button, submit_selector, _ = await _wait_for_first_selector(
        roots,
        PHONE_SUBMIT_SELECTORS,
        timeout_ms=1500,
    )
    continue_button, continue_selector, _ = await _wait_for_first_selector(
        roots,
        PHONE_CONTINUE_SELECTORS,
        timeout_ms=1500,
    )
    mode = determine_phone_login_mode(
        send_button=send_button,
        sms_code_input=sms_code_input,
        submit_button=submit_button,
        continue_button=continue_button,
    )
    if mode == "incomplete":
        debug_context = await _collect_phone_debug_context(context_page)
        raise RuntimeError(
            "xhs phone login form is incomplete; "
            f"send_selector={send_selector!r}; sms_selector={sms_selector!r}; "
            f"submit_selector={submit_selector!r}; continue_selector={continue_selector!r}; {debug_context}"
        )
    return phone_input, send_button, sms_code_input, submit_button, mode


async def _find_phone_login_controls(
    context_page: Any,
    logger: Any,
    *,
    start_nonce: int,
) -> tuple[Any, Any, Any, Any]:
    security_deadline = time.time() + SECURITY_VERIFICATION_TIMEOUT_SECONDS
    announced_need_verify = False
    last_snapshot_path: str | None = None
    while True:
        try:
            return await _find_phone_login_controls_once(context_page, logger)
        except RuntimeError:
            security_root, security_body = await _detect_security_verification(context_page)
            if security_root is None:
                raise
            if not announced_need_verify:
                last_snapshot_path = await _save_security_verification_snapshot(
                    security_root,
                    context_page,
                    start_nonce=start_nonce,
                )
                logger.info(
                    "[rednote_spider.phone_login] Security verification detected; snapshot=%s; body=%s",
                    last_snapshot_path,
                    re.sub(r"\\s+", " ", security_body or "").strip()[:200],
                )
                _emit_phone_state(
                    SECURITY_VERIFICATION_STAGE,
                    SECURITY_VERIFICATION_MESSAGE,
                    image_path=last_snapshot_path,
                )
                announced_need_verify = True
            if time.time() >= security_deadline:
                raise RuntimeError("security verification timed out before phone login form became available")
            await asyncio.sleep(2)


def install_phone_login_patch() -> None:
    from media_platform.xhs.login import XiaoHongShuLogin

    if getattr(XiaoHongShuLogin, "_rednote_phone_patch_installed", False):
        return

    original_init = XiaoHongShuLogin.__init__

    def _patched_init(
        self: Any,
        login_type: str,
        browser_context: Any,
        context_page: Any,
        login_phone: str = "",
        cookie_str: str = "",
    ) -> None:
        resolved_phone = login_phone
        if login_type == "phone" and not resolved_phone:
            resolved_phone = os.getenv("REDNOTE_LOGIN_PHONE", "")
        original_init(
            self,
            login_type=login_type,
            browser_context=browser_context,
            context_page=context_page,
            login_phone=resolved_phone,
            cookie_str=cookie_str,
        )

    async def _patched_login_by_mobile(self: Any) -> None:
        from tools import utils

        start_nonce = int(os.getenv("REDNOTE_LOGIN_START_NONCE", "0") or 0)
        phone_number = normalize_phone_number(self.login_phone or os.getenv("REDNOTE_LOGIN_PHONE", ""))
        code_timeout_seconds = max(
            1,
            int(os.getenv("REDNOTE_LOGIN_CODE_TIMEOUT_SECONDS", str(_default_phone_code_timeout_seconds()))),
        )
        login_result_timeout_seconds = 45

        utils.logger.info("[rednote_spider.phone_login] Begin patched xiaohongshu mobile login ...")
        _emit_phone_stage("starting", "phone login started")
        await asyncio.sleep(1)
        try:
            input_ele, send_btn_ele, sms_code_input_ele, submit_btn_ele, login_mode = await _find_phone_login_controls(
                self.context_page,
                utils.logger,
                start_nonce=start_nonce,
            )

            await input_ele.fill(phone_number)
            await asyncio.sleep(0.5)
            if login_mode == "continue_then_code":
                await _click_phone_agreement_if_present(self.context_page)
                continue_button, _, _ = await _wait_for_first_selector(
                    _iter_selector_roots(self.context_page),
                    PHONE_CONTINUE_SELECTORS,
                    timeout_ms=3000,
                )
                if continue_button is None:
                    raise RuntimeError("phone continue button not found after filling number")
                await continue_button.click()
                await asyncio.sleep(1)
                send_btn_ele, sms_code_input_ele, submit_btn_ele = await _resolve_sms_step_controls(self.context_page)
                if send_btn_ele is not None and sms_code_input_ele is None:
                    await send_btn_ele.click()
                    await asyncio.sleep(0.8)
                    _, sms_code_input_ele, submit_btn_ele = await _resolve_sms_step_controls(self.context_page)
            else:
                if send_btn_ele is not None:
                    await send_btn_ele.click()
                    await asyncio.sleep(0.5)
                if sms_code_input_ele is None:
                    _, sms_code_input_ele, submit_btn_ele = await _resolve_sms_step_controls(self.context_page)

            if sms_code_input_ele is None:
                raise RuntimeError("sms code input not found after phone number submission")

            current_cookie = await self.browser_context.cookies()
            _, cookie_dict = utils.convert_cookies(current_cookie)
            no_logged_in_session = cookie_dict.get("web_session") or ""

            _emit_phone_state("waiting_code", "sms code requested")

            deadline = time.time() + code_timeout_seconds
            while time.time() < deadline:
                sms_code = _consume_submitted_sms_code(start_nonce=start_nonce)
                if not sms_code:
                    await asyncio.sleep(1)
                    continue

                normalized_code = normalize_sms_code(sms_code)
                _emit_phone_state("verifying", "submitting sms code")
                await _fill_sms_code_input(sms_code_input_ele, normalized_code)
                await asyncio.sleep(0.5)

                try:
                    if await _click_phone_agreement_if_present(self.context_page):
                        await asyncio.sleep(0.5)
                except Exception:
                    pass

                if submit_btn_ele is not None:
                    await submit_btn_ele.click()
                try:
                    await asyncio.wait_for(
                        self.check_login_state(no_logged_in_session),
                        timeout=login_result_timeout_seconds,
                    )
                except (RetryError, asyncio.TimeoutError) as exc:
                    del exc
                    message = "手机号验证码登录失败，可能验证码错误、已过期，或遇到额外验证"
                    security_root, _ = await _detect_security_verification(self.context_page)
                    if security_root is not None:
                        snapshot_path = await _save_security_verification_snapshot(
                            security_root,
                            self.context_page,
                            start_nonce=start_nonce,
                        )
                        _emit_phone_state(
                            SECURITY_VERIFICATION_STAGE,
                            SECURITY_VERIFICATION_MESSAGE,
                            image_path=snapshot_path,
                        )
                    else:
                        _emit_login_runtime_event("invalid_sms_code", message)
                    _, refreshed_input, refreshed_submit = await _resolve_sms_step_controls(self.context_page, timeout_seconds=3)
                    if refreshed_input is not None:
                        sms_code_input_ele = refreshed_input
                    if refreshed_submit is not None:
                        submit_btn_ele = refreshed_submit
                    continue

                wait_redirect_seconds = 5
                _emit_phone_stage("success", "phone login success")
                await asyncio.sleep(wait_redirect_seconds)
                return

            message = f"等待验证码超时，{code_timeout_seconds}s 内未收到提交"
            _emit_phone_stage("failed", message)
            raise SystemExit(2)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            _emit_phone_stage("failed", message)
            raise

    XiaoHongShuLogin.__init__ = _patched_init
    XiaoHongShuLogin.login_by_mobile = _patched_login_by_mobile
    XiaoHongShuLogin._rednote_phone_patch_installed = True
