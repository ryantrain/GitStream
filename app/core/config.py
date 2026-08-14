from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "GitStream API"
    env: str = "dev"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://gitstream:gitstream@localhost:5432/gitstream"
    default_tenant_id: str = "dev-tenant"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
