"""Security and environment guards for Streamlit UI."""

from __future__ import annotations

from sqlalchemy.engine import make_url

_PROD_VALUES = {"prod", "production"}


def is_production_env(app_env: str | None) -> bool:
    if app_env is None:
        return False
    return app_env.strip().lower() in _PROD_VALUES


def mask_database_url(database_url: str) -> str:
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001
        if "@" in database_url:
            return "<redacted>"
        return database_url


def validate_access_token(
    *,
    expected_token: str | None,
    provided_token: str | None,
    app_env: str | None,
) -> tuple[bool, str]:
    expected = (expected_token or "").strip()
    provided = (provided_token or "").strip()

    if not expected:
        if is_production_env(app_env):
            return False, "APP_ENV=production 时必须配置 STREAMLIT_ACCESS_TOKEN"
        return True, ""

    if not provided:
        return False, "请输入 Access Token"
    if provided != expected:
        return False, "Access Token 不正确"
    return True, ""
