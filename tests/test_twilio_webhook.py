from dataclasses import replace
import importlib

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.main import app
from app.telephony.twilio import TwilioAdapter


main_module = importlib.import_module("app.main")
client = TestClient(app)
AUTH_TOKEN = "test-auth-token"
WEBHOOK_PATH = "/webhooks/twilio/voice/start"
WEBHOOK_URL = f"http://testserver{WEBHOOK_PATH}"


def signed_headers(params: dict[str, str]) -> dict[str, str]:
    signature = RequestValidator(AUTH_TOKEN).compute_signature(WEBHOOK_URL, params)
    return {"X-Twilio-Signature": signature}


def test_voice_start_returns_greeting_twiml(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "twilio_adapter", TwilioAdapter(auth_token=AUTH_TOKEN))
    params = {"CallSid": "CA123", "From": "+15550000000"}

    response = client.post(
        "/webhooks/twilio/voice/start",
        data=params,
        headers=signed_headers(params),
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "<Say" in response.text
    assert "memory assistance companion" in response.text


def test_voice_start_validates_signature_using_public_base_url(monkeypatch) -> None:
    public_base_url = "https://service.example.com"
    public_webhook_url = f"{public_base_url}{WEBHOOK_PATH}"
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, twilio_public_base_url=public_base_url),
    )
    monkeypatch.setattr(main_module, "twilio_adapter", TwilioAdapter(auth_token=AUTH_TOKEN))
    params = {"CallSid": "CA456"}
    signature = RequestValidator(AUTH_TOKEN).compute_signature(public_webhook_url, params)

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 200


def test_voice_start_rejects_missing_signature() -> None:
    response = client.post(
        "/webhooks/twilio/voice/start",
        data={"CallSid": "CA123"},
    )

    assert response.status_code == 403


def test_voice_start_rejects_invalid_signature() -> None:
    response = client.post(
        "/webhooks/twilio/voice/start",
        data={"CallSid": "CA123"},
        headers={"X-Twilio-Signature": "invalid"},
    )

    assert response.status_code == 403
