"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    """Configuration needed by the Service A application."""

    app_name: str = os.getenv("APP_NAME", "MDoNER Service A")
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_from_phone_number: str = os.getenv("TWILIO_FROM_PHONE_NUMBER", "")
    twilio_public_base_url: str = os.getenv("TWILIO_PUBLIC_BASE_URL", "")
    local_database_path: str = os.getenv("SERVICE_A_DATABASE_PATH", "service_a.sqlite3")


settings = Settings()
