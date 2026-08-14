from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "GitStream API"
    env: str = "dev"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://gitstream:gitstream@localhost:5432/gitstream"
    default_tenant_id: str = "dev-tenant"
    gitstream_webhook_url: str = "https://api.gitstream.dev/api/v1/webhooks/github"
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
