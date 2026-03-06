from __future__ import annotations

from pathlib import Path

from rednote_spider.config import Settings


def test_settings_load_from_env_file(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_URL=sqlite:///./from_env.db",
                "APP_ENV=production",
                "STREAMLIT_ACCESS_TOKEN=token-xyz",
                "CRAWL_BACKEND=command",
                "CRAWL_COMMAND_TEMPLATE=python external.py --keywords '{keywords}' --max-notes {max_notes}",
                "CRAWL_COMMAND_TIMEOUT_SECONDS=120",
                "OPPORTUNITY_LLM_PROVIDER=mock",
                "OPPORTUNITY_LLM_API_KEY=test-key",
                "OPPORTUNITY_LLM_BASE_URL=https://example.com/v1",
                "OPPORTUNITY_LLM_MODEL=test-model",
                "OPPORTUNITY_LLM_TIMEOUT_SECONDS=30",
                "OPPORTUNITY_LLM_TEMPERATURE=0.2",
                "LOG_LEVEL=DEBUG",
            ]
        ),
        encoding="utf-8",
    )

    cfg = Settings(_env_file=env_file)
    assert cfg.database_url == "sqlite:///./from_env.db"
    assert cfg.app_env == "production"
    assert cfg.streamlit_access_token == "token-xyz"
    assert cfg.crawl_backend == "command"
    assert "external.py" in cfg.crawl_command_template
    assert cfg.crawl_command_timeout_seconds == 120
    assert cfg.opportunity_llm_provider == "mock"
    assert cfg.opportunity_llm_api_key == "test-key"
    assert cfg.opportunity_llm_base_url == "https://example.com/v1"
    assert cfg.opportunity_llm_model == "test-model"
    assert cfg.opportunity_llm_timeout_seconds == 30
    assert cfg.opportunity_llm_temperature == 0.2
    assert cfg.log_level == "DEBUG"
