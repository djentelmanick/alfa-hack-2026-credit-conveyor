from functools import lru_cache
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_comma(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


# Без NoDecode pydantic-settings ждёт JSON-список, а в .env удобнее писать адреса через запятую.
CommaSeparated = Annotated[list[str], NoDecode, BeforeValidator(_split_comma)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_ttl_minutes: int = Field(default=60, gt=0)

    s3_endpoint: str
    s3_public_endpoint: str
    s3_access_key: SecretStr
    s3_secret_key: SecretStr
    s3_region: str = "us-east-1"
    s3_bucket_files: str = "credit-files"
    s3_bucket_contracts: str = "credit-contracts"
    presigned_ttl_seconds: int = Field(default=300, gt=0)
    max_upload_mb: int = Field(default=10, gt=0)

    pipeline_step_delay_ms: int = Field(default=800, ge=0)
    source_retry_attempts: int = Field(default=3, ge=1)
    source_retry_pause_ms: int = Field(default=500, ge=0)
    offer_ttl_days: int = Field(default=7, gt=0)
    signing_code_ttl_seconds: int = Field(default=300, gt=0)

    demo_mode: bool = False
    # Нужен только seed.py: пароль тестовых учёток не хранится в коде.
    seed_password: SecretStr | None = None
    cors_origins: CommaSeparated = []


@lru_cache
def get_settings() -> Settings:
    return Settings()
