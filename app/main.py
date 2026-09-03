"""HTTP application entry point for MDoNER Service A."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from app.config import settings
from app.telephony.twilio import TwilioAdapter


app = FastAPI(title=settings.app_name)
twilio_adapter = TwilioAdapter(settings.twilio_auth_token)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the application process is available."""

    return {"status": "ok", "service": settings.app_name}


@app.post("/webhooks/twilio/voice/start", response_class=Response)
async def twilio_voice_start(request: Request) -> Response:
    """Authenticate Twilio's request and return the initial greeting TwiML."""

    form = await request.form()
    params = {key: str(value) for key, value in form.items()}
    signature = request.headers.get("X-Twilio-Signature")
    if not twilio_adapter.validate_webhook(str(request.url), params, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    return Response(content=twilio_adapter.greeting_response(), media_type="application/xml")
