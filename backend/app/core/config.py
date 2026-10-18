from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    app_name: str = "TraceLayer API"

    # Supabase. The service-role key is a server-side secret and must never reach the browser.
    supabase_url: str
    supabase_service_role_key: str
    # Only needed for projects still on the legacy shared HS256 JWT secret; asymmetric keys use JWKS.
    supabase_jwt_secret: str | None = None
    jwt_audience: str = "authenticated"

    # Shared secret for internal workers (model training / agent services) calling this API.
    # Sent as X-Service-Key together with X-Tenant-Id. Leave unset to disable worker access.
    service_api_key: str | None = None

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    max_upload_mb: int = 25
    governance_contracts_enabled: bool = False
    enterprise_enabled: bool = False
    case_authorization_enabled: bool = False
    deployment_region: str = "US"
    release_public_key_path: str | None = None
    approved_data_destinations: list[str] = Field(default_factory=lambda: ["local", "supabase"])

    source_files_bucket: str = "source-files"
    case_exports_bucket: str = "case-exports"
    model_artifacts_bucket: str = "model-artifacts"

    canonical_schema_version: str = "canonical-txn-v1"
    pipeline_version: str = "ingest-pipeline-1.0.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
