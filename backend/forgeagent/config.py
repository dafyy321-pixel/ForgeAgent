from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORGE_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://forge:forge-local@127.0.0.1:55432/forge"
    data_dir: Path = Path(".forge")
    auth_mode: str = "local"
    trusted_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:5174,http://localhost:5174,http://127.0.0.1:8000"
    )
    oidc_issuer: str = ""
    oidc_audience: str = "forgeagent"
    oidc_jwks_url: str = ""
    oidc_client_id: str = ""
    oidc_authorization_url: str = ""
    oidc_token_url: str = ""
    oidc_scope: str = "openid profile"
    cursor_secret: str = ""
    model_provider: str = "unconfigured"
    model_id: str = ""
    model_base_url: str = ""
    model_api_key: str = ""
    input_price: float = 0
    output_price: float = 0
    cached_input_price: float | None = None
    cache_write_price: float | None = None
    model_protocol: Literal["responses", "chat"] = "responses"
    model_output_mode: Literal["native", "structured", "json"] = "native"
    model_tokenizer: str = "o200k_base"
    model_token_count: bool = True
    model_supports_schema: bool = True
    model_supports_tools: bool = True
    context_window: int = 32768
    max_output: int = 4096
    sandbox_image: str = "forgeagent-sandbox:local"
    sandbox_runtime: str = "runc"
    sandbox_manager_url: str = ""
    sandbox_manager_secret: str = ""
    credential_routes: str = "{}"
    lease_seconds: int = 30
    worker_tenants: str = ""
    worker_slots: int = Field(4, ge=1, le=32)
    provider_concurrency: int = Field(2, ge=1, le=32)
    sandbox_concurrency: int = Field(4, ge=1, le=32)
    s3_endpoint: str = ""
    s3_bucket: str = "forgeagent"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_session_token: str = ""
    object_spool_bytes: int = Field(4 * 1024 * 1024, ge=65536, le=64 * 1024 * 1024)
    s3_multipart_bytes: int = Field(8 * 1024 * 1024, ge=5 * 1024 * 1024, le=64 * 1024 * 1024)
    remote_hosts: str = ""
    callback_secret: str = ""
    remote_credentials: str = "{}"
    max_object_bytes: int = 16 * 1024 * 1024


settings = Settings()
