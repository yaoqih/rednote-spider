"""Application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./rednote.db"
    app_env: str = "dev"
    streamlit_access_token: str = ""
    streamlit_server_address: str = "127.0.0.1"
    streamlit_server_port: int = 8501
    streamlit_server_headless: bool = True
    crawl_backend: str = "command"
    crawl_command_template: str = ""
    crawl_command_timeout_seconds: int = 600
    opportunity_llm_provider: str = "openai"
    opportunity_llm_api_key: str = ""
    opportunity_llm_base_url: str = "https://api.openai.com/v1"
    opportunity_llm_model: str = "gpt-4.1-mini"
    opportunity_llm_timeout_seconds: int = 600
    opportunity_llm_temperature: float = 0.1
    sched_discover_loop_interval_seconds: int = 900
    sched_discover_note_limit: int = 20
    sched_opportunity_loop_interval_seconds: int = 600
    login_qr_valid_seconds: int = 120
    login_qr_generation_timeout_seconds: int = 30
    login_qr_auto_refresh: bool = True
    login_qr_keywords: str = "登录二维码"
    login_qr_command: str = ""
    login_qr_crawler_cwd: str = ""
    login_qr_output_dir: str = "./logs/login_qr"
    login_qr_poll_seconds: int = 2
    login_phone_command: str = ""
    login_phone_crawler_cwd: str = ""
    login_phone_poll_seconds: int = 2
    login_phone_code_timeout_seconds: int = 120
    login_runtime_python: str = ""
    login_runtime_crawler_cwd: str = ""
    login_controller_poll_seconds: int = 2
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
