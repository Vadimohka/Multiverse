from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Parser Studio"
    app_env: str = "development"
    app_secret_key: str = "development-secret-change-me-32-bytes-minimum"
    encryption_master_key: str = "development-encryption-key-change-me"
    database_url: str = "sqlite:///./parser_studio.db"
    redis_url: str = "redis://localhost:6379/0"
    artifact_storage_backend: str = "s3"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minio"
    s3_secret_key: str = "minio123"
    s3_bucket_raw: str = "raw"
    s3_bucket_exports: str = "exports"
    default_admin_email: str = "admin@parser.local"
    default_admin_password: str = "Admin123!"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    deepseek_default_model: str = "deepseek-chat"
    public_app_url: str = "http://localhost:8080"
    internal_api_url: str = "http://api:8000"
    cors_origins: str = "http://localhost:8080,http://localhost:5173"
    access_token_minutes: int = 30
    refresh_token_days: int = 7

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
