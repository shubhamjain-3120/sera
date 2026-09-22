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
    evidence_require_reducto: bool = True
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    # Model identifiers stay in configuration so business logic never names one.
    model_template_analysis: str = "gpt-5.6-luna"
    model_evidence_extraction: str = "gpt-5.6-luna"
    model_mapping: str = "gpt-5.6-luna"
    model_verification: str = "gpt-5.6-luna"
    # Blank omits the reasoning parameter, for models that do not accept one.
    model_reasoning_effort: str = "max"
    # A batch of real form fields at effort "max" measured ~167s, so a 120s
    # timeout failed every batch. Leave headroom above the slowest stage.
    model_timeout_seconds: float = 300
    # One request per this many items. A whole form in a single call exceeds the
    # timeout, and one slow call would then lose every item's result.
    model_batch_size: int = 20
    # Batches are independent, so they run concurrently; wall time is then the
    # slowest batch rather than their sum.
    model_max_concurrency: int = 4
    model_max_retries: int = 1
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "formfiller"
    s3_secret_key: str = "formfiller-local-secret"
    s3_bucket: str = "formfiller"
    max_upload_bytes: int = 50 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
