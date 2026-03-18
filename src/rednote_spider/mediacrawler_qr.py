"""Helpers for showing MediaCrawler login QR codes in terminals."""

from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import os
import shutil
import sys
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import TextIO

from PIL import Image, ImageDraw, ImageOps

from .login_runtime_events import format_login_runtime_event

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QR_OUTPUT_DIR = PROJECT_ROOT / "logs" / "login_qr"
LOGIN_ATTEMPT_ID_ENV = "REDNOTE_LOGIN_ATTEMPT_ID"
QR_IMAGE_SELECTORS = (
    "img.qrcode-img",
    "img[class*='qrcode']",
    "img[alt*='二维码']",
    "img[alt*='登录']",
)
QR_LOGIN_TRIGGER_SELECTORS = (
    "xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button",
    "xpath=//button[contains(normalize-space(), '登录')]",
    "xpath=//button[contains(normalize-space(), '注册')]",
    "xpath=//*[self::button or self::div or self::span][contains(normalize-space(), '登录')]",
    "xpath=//*[self::button or self::div or self::span][contains(normalize-space(), '注册')]",
    "text=登录",
    "text=注册",
)


def _normalize_qr_payload(qr_code: str) -> str:
    payload = (qr_code or "").strip()
    if not payload:
        raise ValueError("qr_code is required")
    if "," in payload:
        payload = payload.split(",", 1)[1]
    return payload.strip()


def decode_qr_image(qr_code: str) -> Image.Image:
    payload = _normalize_qr_payload(qr_code)
    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise ValueError("qr_code is not valid base64") from exc

    try:
        image = Image.open(BytesIO(image_bytes))
        image.load()
    except Exception as exc:  # pragma: no cover - pillow raises multiple exception types
        raise ValueError("qr_code is not a valid image") from exc
    return image.convert("L")


def _framed_qr_image(image: Image.Image) -> Image.Image:
    width, height = image.size
    framed = Image.new("RGB", (width + 20, height + 20), color=(255, 255, 255))
    framed.paste(image.convert("RGB"), (10, 10))
    draw = ImageDraw.Draw(framed)
    draw.rectangle((0, 0, width + 19, height + 19), outline=(0, 0, 0), width=1)
    return framed


def render_terminal_qr(image: Image.Image, *, max_columns: int | None = None) -> str:
    columns = max_columns or max(shutil.get_terminal_size(fallback=(100, 40)).columns - 2, 32)
    terminal_image = ImageOps.expand(image.convert("L"), border=4, fill=255)
    if terminal_image.width > columns:
        scale = columns / terminal_image.width
        terminal_image = terminal_image.resize(
            (
                max(1, int(terminal_image.width * scale)),
                max(1, int(terminal_image.height * scale)),
            ),
            Image.Resampling.NEAREST,
        )

    bw_image = terminal_image.point(lambda value: 0 if value < 128 else 255, mode="1")
    if bw_image.height % 2:
        bw_image = ImageOps.expand(bw_image, border=(0, 0, 0, 1), fill=1)

    pixels = bw_image.load()
    lines: list[str] = []
    for y in range(0, bw_image.height, 2):
        chars: list[str] = []
        for x in range(bw_image.width):
            upper_black = pixels[x, y] == 0
            lower_black = pixels[x, y + 1] == 0
            if upper_black and lower_black:
                chars.append("█")
            elif upper_black:
                chars.append("▀")
            elif lower_black:
                chars.append("▄")
            else:
                chars.append(" ")
        lines.append("".join(chars))
    return "\n".join(lines)


def save_qr_png(
    image: Image.Image,
    *,
    output_dir: Path,
    filename_prefix: str,
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{filename_prefix}-{timestamp}.png"
    image.save(output_path, format="PNG")
    return output_path


def emit_terminal_image_and_save(
    image: Image.Image,
    *,
    output_dir: Path | str | None = None,
    filename_prefix: str = "xhs-login",
    stream: TextIO | None = None,
    now: datetime | None = None,
) -> Path:
    target_dir = Path(output_dir or DEFAULT_QR_OUTPUT_DIR).expanduser().resolve()
    output_path = save_qr_png(image, output_dir=target_dir, filename_prefix=filename_prefix, now=now)

    target_stream = stream or sys.stderr
    target_stream.write("\n")
    target_stream.write("[rednote-spider] 请使用小红书 App 扫描下方二维码完成登录。\n")
    target_stream.write(f"[rednote-spider] 二维码 PNG 已保存：{output_path}\n\n")
    target_stream.write(render_terminal_qr(image))
    target_stream.write("\n\n")
    target_stream.flush()
    return output_path


def emit_terminal_qr_and_save(
    qr_code: str,
    *,
    output_dir: Path | str | None = None,
    filename_prefix: str = "xhs-login",
    stream: TextIO | None = None,
    now: datetime | None = None,
) -> Path:
    image = decode_qr_image(qr_code)
    framed_image = _framed_qr_image(image)
    return emit_terminal_image_and_save(
        framed_image,
        output_dir=output_dir,
        filename_prefix=filename_prefix,
        stream=stream,
        now=now,
    )


def _resolve_attempt_id() -> int:
    raw = os.getenv(LOGIN_ATTEMPT_ID_ENV, "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _emit_login_event(event_type: str, message: str = "", **payload: object) -> None:
    print(
        format_login_runtime_event(
            event_type,
            message,
            attempt_id=_resolve_attempt_id(),
            **payload,
        ),
        file=sys.stderr,
        flush=True,
    )


def _iter_selector_roots(context_page) -> list[object]:
    roots = [context_page]
    for frame in getattr(context_page, "frames", []) or []:
        if frame is context_page:
            continue
        roots.append(frame)
    return roots


async def _wait_for_first_qr_source(
    roots: list[object],
    selectors: tuple[str, ...] | list[str],
    *,
    timeout_ms: int,
) -> tuple[str | None, str | None, object | None]:
    for selector in selectors:
        for root in roots:
            try:
                handle = await root.wait_for_selector(selector, timeout=timeout_ms)
            except Exception:
                continue
            if handle is None:
                continue
            try:
                src = await handle.get_attribute("src")
            except Exception:
                continue
            token = str(src or "").strip()
            if token:
                return token, selector, root
    return None, None, None


async def _click_first_visible(
    roots: list[object],
    selectors: tuple[str, ...] | list[str],
    *,
    timeout_ms: int,
) -> tuple[object | None, str | None, object | None]:
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


async def _collect_qr_debug_context(context_page) -> str:
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
    normalized_body = " ".join(str(body_text or "").split())
    if normalized_body:
        parts.append(f"body={normalized_body[:200]}")
    frame_urls = [str(getattr(frame, "url", "")).strip() for frame in _iter_selector_roots(context_page)[1:]]
    frame_urls = [item for item in frame_urls if item]
    if frame_urls:
        parts.append("frames=" + ", ".join(frame_urls[:3]))
    return "; ".join(parts) or "no debug context"


async def _find_qr_source(context_page, *, timeout_seconds: int = 10) -> tuple[str | None, str | None]:
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        roots = _iter_selector_roots(context_page)
        src, selector, _ = await _wait_for_first_qr_source(
            roots,
            QR_IMAGE_SELECTORS,
            timeout_ms=800,
        )
        if src:
            return src, selector
        await asyncio.sleep(0.5)
    return None, None


def install_qr_login_patch() -> None:
    from tools import crawler_util, utils

    if getattr(utils, "_rednote_qr_patch_installed", False):
        return

    def _patched_show_qrcode(qr_code) -> None:  # type: ignore[no-untyped-def]
        output_path = emit_terminal_qr_and_save(
            qr_code,
            output_dir=os.getenv("REDNOTE_QR_OUTPUT_DIR", str(DEFAULT_QR_OUTPUT_DIR)),
            filename_prefix="xhs-login",
        )
        _emit_login_event("qr_ready", "qr ready", image_path=str(output_path))

    utils.show_qrcode = _patched_show_qrcode
    crawler_util.show_qrcode = _patched_show_qrcode
    utils._rednote_qr_patch_installed = True
    crawler_util._rednote_qr_patch_installed = True


def install_qr_login_flow_patch() -> None:
    from media_platform.xhs.login import XiaoHongShuLogin
    from tenacity import RetryError
    from tools import utils

    if getattr(XiaoHongShuLogin, "_rednote_qr_login_flow_patch_installed", False):
        return

    async def _patched_login_by_qrcode(self) -> None:  # type: ignore[no-untyped-def]
        utils.logger.info("[XiaoHongShuLogin.login_by_qrcode] Begin login xiaohongshu by qrcode ...")
        base64_qrcode_img, qr_selector = await _find_qr_source(self.context_page, timeout_seconds=8)
        if not base64_qrcode_img:
            _, trigger_selector, _ = await _click_first_visible(
                _iter_selector_roots(self.context_page),
                QR_LOGIN_TRIGGER_SELECTORS,
                timeout_ms=1500,
            )
            if trigger_selector:
                utils.logger.info(
                    "[rednote_spider.qr_login] Opened login dialog via selector: %s",
                    trigger_selector,
                )
                await asyncio.sleep(1)
            base64_qrcode_img, qr_selector = await _find_qr_source(self.context_page, timeout_seconds=10)

        if not base64_qrcode_img:
            debug_context = await _collect_qr_debug_context(self.context_page)
            utils.logger.info(
                "[XiaoHongShuLogin.login_by_qrcode] login failed , have not found qrcode please check .... %s",
                debug_context,
            )
            sys.exit()

        if qr_selector:
            utils.logger.info(
                "[rednote_spider.qr_login] Found login qrcode via selector: %s",
                qr_selector,
            )

        current_cookie = await self.browser_context.cookies()
        _, cookie_dict = utils.convert_cookies(current_cookie)
        no_logged_in_session = cookie_dict.get("web_session")

        partial_show_qrcode = functools.partial(utils.show_qrcode, base64_qrcode_img)
        asyncio.get_running_loop().run_in_executor(executor=None, func=partial_show_qrcode)

        utils.logger.info("[XiaoHongShuLogin.login_by_qrcode] waiting for scan code login, remaining time is 120s")
        try:
            await self.check_login_state(no_logged_in_session)
        except RetryError:
            utils.logger.info("[XiaoHongShuLogin.login_by_qrcode] Login xiaohongshu failed by qrcode login method ...")
            sys.exit()

        wait_redirect_seconds = 5
        utils.logger.info(
            "[XiaoHongShuLogin.login_by_qrcode] Login successful then wait for %s seconds redirect ...",
            wait_redirect_seconds,
        )
        await asyncio.sleep(wait_redirect_seconds)

    XiaoHongShuLogin.login_by_qrcode = _patched_login_by_qrcode
    XiaoHongShuLogin._rednote_qr_login_flow_patch_installed = True


__all__ = [
    "DEFAULT_QR_OUTPUT_DIR",
    "QR_IMAGE_SELECTORS",
    "QR_LOGIN_TRIGGER_SELECTORS",
    "_click_first_visible",
    "_wait_for_first_qr_source",
    "decode_qr_image",
    "emit_terminal_image_and_save",
    "emit_terminal_qr_and_save",
    "install_qr_login_flow_patch",
    "install_qr_login_patch",
    "render_terminal_qr",
    "save_qr_png",
]
