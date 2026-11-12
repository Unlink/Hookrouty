import base64
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    app_secret_key: str
    admin_username: str = Field("admin", pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    admin_password: str = ""
    app_url: str = "http://localhost:8080"
    data_dir: Path = Path("/data")
    log_level: str = "INFO"
    max_inflight_requests: int = Field(20, gt=0)
    max_request_body_bytes: int = Field(1048576, gt=0)
    max_management_body_bytes: int = Field(1048576, gt=0)
    max_rendered_body_bytes: int = Field(1048576, gt=0)
    max_upstream_response_bytes: int = Field(8388608, gt=0)
    history_capture_bytes: int = Field(16384, gt=0)
    history_retention_days: int = Field(7, gt=0)
    history_max_records: int = Field(10000, gt=0)
    shutdown_grace_seconds: int = Field(30, gt=0)

    @field_validator("app_secret_key")
    @classmethod
    def valid_key(cls, value):
        try:
            if len(base64.b64decode(value, altchars=b"-_", validate=True)) != 32:
                raise ValueError()
        except Exception:
            raise ValueError("APP_SECRET_KEY must encode 32 random bytes") from None
        return value

    @field_validator("app_url")
    @classmethod
    def valid_url(cls, value):
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.query
            or url.fragment
            or url.username
        ):
            raise ValueError("APP_URL must be an HTTP(S) base URL")
        return value.rstrip("/")
