from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://bazra:bazra@localhost:5434/bazra"

    session_cookie_name: str = "bazra_session"
    session_cookie_secure: bool = False

    # Browser-facing origin of the frontend dev server — local dev fallback
    # only; override via CORS_ALLOWED_ORIGIN if the frontend port ever
    # changes again (see the port-remap history in the root README).
    cors_allowed_origin: str = "http://localhost:5174"

    # Phase 3 (AI Core), Checkpoint 3.1. Never logged, never persisted —
    # see model_router/service.py's explicit tests that no trace row or
    # log line ever contains this value.
    anthropic_api_key: str | None = None


settings = Settings()
