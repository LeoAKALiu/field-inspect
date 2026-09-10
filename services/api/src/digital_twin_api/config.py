"""Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Digital Twin API"
    app_version: str = "0.1.0"
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]


settings = Settings()
