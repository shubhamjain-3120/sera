from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/formfiller.db"
    storage_backend: str = "filesystem"
    storage_root: Path = Path("./data/objects")
    redis_url: str = "redis://localhost:6379/0"
    reducto_api_key: str | None = None
    reducto_base_url: str = "https://platform.reducto.ai"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "formfiller"
    s3_secret_key: str = "formfiller-local-secret"
    s3_bucket: str = "formfiller"
    max_upload_bytes: int = 50 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()

