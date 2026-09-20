from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://bazra:bazra@localhost:5434/bazra"

    session_cookie_name: str = "bazra_session"
    session_cookie_secure: bool = False


settings = Settings()
