"""HTTP application entry point for MDoNER Service A."""

from fastapi import FastAPI

from app.config import settings


app = FastAPI(title=settings.app_name)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the application process is available."""

    return {"status": "ok", "service": settings.app_name}
