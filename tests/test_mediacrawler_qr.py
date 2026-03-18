from __future__ import annotations

import asyncio
import base64
import io
from datetime import datetime

from PIL import Image

from rednote_spider.mediacrawler_qr import (
    _click_first_visible,
    _wait_for_first_qr_source,
    emit_terminal_qr_and_save,
)


def _png_base64_from_image(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _sample_qr_data_url() -> str:
    image = Image.new("1", (8, 8), 1)
    pixels = image.load()
    for x in range(8):
        pixels[x, 0] = 0
        pixels[x, 7] = 0
        pixels[0, x] = 0
        pixels[7, x] = 0
    pixels[2, 2] = 0
    pixels[5, 2] = 0
    pixels[2, 5] = 0
    pixels[5, 5] = 0
    return f"data:image/png;base64,{_png_base64_from_image(image)}"


def test_emit_terminal_qr_and_save_writes_png_and_block_art(tmp_path):
    stream = io.StringIO()

    output_path = emit_terminal_qr_and_save(
        _sample_qr_data_url(),
        output_dir=tmp_path,
        filename_prefix="xhs-login",
        stream=stream,
        now=datetime(2026, 3, 7, 7, 0, 1),
    )

    assert output_path == tmp_path / "xhs-login-20260307-070001.png"
    assert output_path.exists()

    rendered = stream.getvalue()
    assert "xhs-login-20260307-070001.png" in rendered
    assert "请使用小红书 App 扫描下方二维码" in rendered
    assert any(ch in rendered for ch in "█▀▄")



def test_emit_terminal_qr_and_save_accepts_plain_base64(tmp_path):
    stream = io.StringIO()
    image = Image.new("1", (4, 4), 0)

    output_path = emit_terminal_qr_and_save(
        _png_base64_from_image(image),
        output_dir=tmp_path,
        filename_prefix="plain",
        stream=stream,
        now=datetime(2026, 3, 7, 7, 0, 2),
    )

    assert output_path == tmp_path / "plain-20260307-070002.png"
    assert output_path.exists()


class _FakeQrHandle:
    def __init__(self, *, src: str = "", click_ok: bool = True):
        self.src = src
        self.click_ok = click_ok
        self.click_count = 0

    async def get_attribute(self, name: str):
        if name != "src":
            return None
        return self.src

    async def click(self):
        if not self.click_ok:
            raise RuntimeError("not clickable")
        self.click_count += 1


class _FakeQrRoot:
    def __init__(self, matches: dict[str, _FakeQrHandle] | None = None):
        self.matches = matches or {}
        self.calls: list[str] = []

    async def wait_for_selector(self, selector: str, timeout: int = 0):
        del timeout
        self.calls.append(selector)
        handle = self.matches.get(selector)
        if handle is None:
            raise RuntimeError(f"missing selector: {selector}")
        return handle


def test_wait_for_first_qr_source_searches_multiple_roots_and_selectors():
    frame_handle = _FakeQrHandle(src="data:image/png;base64,abc123")
    page = _FakeQrRoot()
    frame = _FakeQrRoot({"img[alt*='二维码']": frame_handle})

    src, selector, root = asyncio.run(
        _wait_for_first_qr_source(
            [page, frame],
            [
                "img.qrcode-img",
                "img[alt*='二维码']",
            ],
            timeout_ms=10,
        )
    )

    assert src == "data:image/png;base64,abc123"
    assert selector == "img[alt*='二维码']"
    assert root is frame


def test_click_first_visible_uses_fallback_login_trigger():
    handle = _FakeQrHandle()
    page = _FakeQrRoot({"text=登录": handle})

    clicked_handle, selector, root = asyncio.run(
        _click_first_visible(
            [page],
            [
                "xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button",
                "text=登录",
            ],
            timeout_ms=10,
        )
    )

    assert clicked_handle is handle
    assert selector == "text=登录"
    assert root is page
    assert handle.click_count == 1
