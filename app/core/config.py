from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "GitStream API"
    env: str = "dev"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://gitstream:gitstream@localhost:5432/gitstream"
    default_tenant_id: str = "dev-tenant"
    redis_url: str = "redis://localhost:6379"
    model_path: str = "models/merge_time_model.joblib"
    github_token: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
