from pathlib import Path

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
    model_provider: str = "unconfigured"
    model_id: str = ""
    model_base_url: str = ""
    model_api_key: str = ""
    input_price: float = 0
    output_price: float = 0
    context_window: int = 32768
    max_output: int = 4096
    sandbox_image: str = "forgeagent-sandbox:local"
    sandbox_runtime: str = "runc"
    lease_seconds: int = 30
    worker_tenants: str = ""
    s3_endpoint: str = ""
    s3_bucket: str = "forgeagent"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    remote_hosts: str = ""
    callback_secret: str = ""
    max_object_bytes: int = 16 * 1024 * 1024


settings = Settings()
