from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime config comes from MHSQUARE_* env vars. No secrets have defaults."""

    model_config = SettingsConfigDict(env_prefix="MHSQUARE_", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://mhsquare:mhsquare@postgres:5432/mhsquare"
    redis_url: str = "redis://redis:6379/0"

    secret_key: str = Field(min_length=32)
    session_max_age_s: int = 60 * 60 * 24 * 14
    cookie_secure: bool = True
    ui_origin: str = "http://localhost:8080"

    admin_email: str | None = None
    admin_password: str | None = None

    login_rate_limit: int = 5
    login_rate_window_s: int = 300

    litellm_url: str = "http://litellm:4000"
    litellm_api_key: str | None = None  # fallback; normally stored in llmconfig.env
    litellm_master_key: str | None = None  # key admin only (create/update budget key)
    llmconfig_path: str = "/mh/local/llmconfig.env"
    llm_defaults_path: str = "/mh/litellm/llm-defaults.env"
    renderer_url: str = "http://manim-renderer:8000"
    storage_root: str = "/data/storage"
    routing_path: str = "/config/routing.yaml"

    @field_validator("secret_key")
    @classmethod
    def _not_placeholder(cls, v: str) -> str:
        if v.startswith("change-me"):
            raise ValueError("MHSQUARE_SECRET_KEY is still the placeholder; run scripts/setup")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
