"""HTTP application entry point for MDoNER Service A."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from app.config import settings
from app.telephony.twilio import TwilioAdapter


app = FastAPI(title=settings.app_name)
TWILIO_VOICE_START_PATH = "/webhooks/twilio/voice/start"
twilio_adapter = TwilioAdapter(
    account_sid=settings.twilio_account_sid,
    auth_token=settings.twilio_auth_token,
    from_phone_number=settings.twilio_from_phone_number,
)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the application process is available."""

    return {"status": "ok", "service": settings.app_name}


@app.post("/webhooks/twilio/voice/start", response_class=Response)
async def twilio_voice_start(request: Request) -> Response:
    """Authenticate Twilio's request and return the initial greeting TwiML."""

    try:
        form = await request.form()
    except Exception as error:
        raise HTTPException(status_code=400, detail="Malformed webhook input") from error
    params = {key: str(value) for key, value in form.items()}
    if not params.get("CallSid"):
        raise HTTPException(status_code=400, detail="CallSid is required")
    signature = request.headers.get("X-Twilio-Signature")
    webhook_url = (
        f"{settings.twilio_public_base_url.rstrip('/')}{TWILIO_VOICE_START_PATH}"
        if settings.twilio_public_base_url
        else str(request.url)
    )
    try:
        valid_signature = twilio_adapter.validate_webhook(webhook_url, params, signature)
    except Exception as error:
        raise HTTPException(status_code=502, detail="Telephony provider unavailable") from error
    if not valid_signature:
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    try:
        greeting = twilio_adapter.greeting_response()
    except Exception as error:
        raise HTTPException(status_code=502, detail="Telephony provider unavailable") from error
    return Response(content=greeting, media_type="application/xml")
