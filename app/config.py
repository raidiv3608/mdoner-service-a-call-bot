"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    """Configuration needed by the Service A application shell."""

    app_name: str = os.getenv("APP_NAME", "MDoNER Service A")
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))


settings = Settings()
