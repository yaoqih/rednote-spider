"""Application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./rednote.db"
    app_env: str = "dev"
    streamlit_access_token: str = ""
    crawl_backend: str = "command"
    crawl_command_template: str = ""
    crawl_command_timeout_seconds: int = 600
    opportunity_llm_provider: str = "openai"
    opportunity_llm_api_key: str = ""
    opportunity_llm_base_url: str = "https://api.openai.com/v1"
    opportunity_llm_model: str = "gpt-4.1-mini"
    opportunity_llm_timeout_seconds: int = 600
    opportunity_llm_temperature: float = 0.1
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
