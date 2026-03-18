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
                "SCHED_DISCOVER_LOOP_INTERVAL_SECONDS=321",
                "SCHED_DISCOVER_NOTE_LIMIT=37",
                "SCHED_OPPORTUNITY_LOOP_INTERVAL_SECONDS=654",
                "STREAMLIT_SERVER_ADDRESS=0.0.0.0",
                "STREAMLIT_SERVER_PORT=9999",
                "STREAMLIT_SERVER_HEADLESS=false",
                "LOGIN_QR_VALID_SECONDS=111",
                "LOGIN_QR_GENERATION_TIMEOUT_SECONDS=22",
                "LOGIN_QR_AUTO_REFRESH=false",
                "LOGIN_QR_KEYWORDS=登录二维码",
                "LOGIN_QR_COMMAND=python qr.py --keywords '{keywords}'",
                "LOGIN_QR_CRAWLER_CWD=../MediaCrawler",
                "LOGIN_QR_OUTPUT_DIR=./logs/login_qr",
                "LOGIN_QR_POLL_SECONDS=3",
                "LOGIN_PHONE_COMMAND=python phone.py --phone-number '{phone_number}'",
                "LOGIN_PHONE_CRAWLER_CWD=../MediaCrawler",
                "LOGIN_PHONE_POLL_SECONDS=4",
                "LOGIN_PHONE_CODE_TIMEOUT_SECONDS=130",
                "LOGIN_RUNTIME_PYTHON=/root/MediaCrawler/.venv/bin/python",
                "LOGIN_RUNTIME_CRAWLER_CWD=../MediaCrawler",
                "LOGIN_CONTROLLER_POLL_SECONDS=5",
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
    assert cfg.sched_discover_loop_interval_seconds == 321
    assert cfg.sched_discover_note_limit == 37
    assert cfg.sched_opportunity_loop_interval_seconds == 654
    assert cfg.streamlit_server_address == "0.0.0.0"
    assert cfg.streamlit_server_port == 9999
    assert cfg.streamlit_server_headless is False
    assert cfg.login_qr_valid_seconds == 111
    assert cfg.login_qr_generation_timeout_seconds == 22
    assert cfg.login_qr_auto_refresh is False
    assert cfg.login_qr_keywords == "登录二维码"
    assert cfg.login_qr_command == "python qr.py --keywords '{keywords}'"
    assert cfg.login_qr_crawler_cwd == "../MediaCrawler"
    assert cfg.login_qr_output_dir == "./logs/login_qr"
    assert cfg.login_qr_poll_seconds == 3
    assert cfg.login_phone_command == "python phone.py --phone-number '{phone_number}'"
    assert cfg.login_phone_crawler_cwd == "../MediaCrawler"
    assert cfg.login_phone_poll_seconds == 4
    assert cfg.login_phone_code_timeout_seconds == 130
    assert cfg.login_runtime_python == "/root/MediaCrawler/.venv/bin/python"
    assert cfg.login_runtime_crawler_cwd == "../MediaCrawler"
    assert cfg.login_controller_poll_seconds == 5
