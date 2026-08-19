from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ic:ic@postgres:5432/ic"
    http_timeout_seconds: float = 20.0
