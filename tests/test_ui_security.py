from __future__ import annotations

from rednote_spider.ui_security import (
    is_production_env,
    mask_database_url,
    validate_access_token,
)


def test_mask_database_url_hides_password():
    masked = mask_database_url(
        "postgresql+psycopg://iot:supersecret@1.2.3.4:5432/rednote_spider_c8_20260221"
    )
    assert "supersecret" not in masked
    assert "***" in masked


def test_mask_database_url_fallback():
    assert mask_database_url("not-a-url-without-at-sign") == "not-a-url-without-at-sign"
    assert mask_database_url("not-a-url-with-at@value") == "<redacted>"


def test_is_production_env():
    assert is_production_env("production") is True
    assert is_production_env("prod") is True
    assert is_production_env("dev") is False


def test_validate_access_token_rules():
    ok, msg = validate_access_token(expected_token=None, provided_token=None, app_env="dev")
    assert ok is True
    assert msg == ""

    ok, msg = validate_access_token(expected_token="", provided_token=None, app_env="production")
    assert ok is False
    assert "STREAMLIT_ACCESS_TOKEN" in msg

    ok, msg = validate_access_token(
        expected_token="abc123", provided_token="", app_env="production"
    )
    assert ok is False
    assert "请输入" in msg

    ok, msg = validate_access_token(
        expected_token="abc123", provided_token="wrong", app_env="production"
    )
    assert ok is False
    assert "不正确" in msg

    ok, msg = validate_access_token(
        expected_token="abc123", provided_token="abc123", app_env="production"
    )
    assert ok is True
    assert msg == ""
