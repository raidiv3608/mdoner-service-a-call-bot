from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.main import app


client = TestClient(app)
AUTH_TOKEN = "test-auth-token"
WEBHOOK_URL = "http://testserver/webhooks/twilio/voice/start"


def signed_headers(params: dict[str, str]) -> dict[str, str]:
    signature = RequestValidator(AUTH_TOKEN).compute_signature(WEBHOOK_URL, params)
    return {"X-Twilio-Signature": signature}


def test_voice_start_returns_greeting_twiml(monkeypatch) -> None:
    monkeypatch.setattr("app.main.twilio_adapter", __import__(
        "app.telephony.twilio", fromlist=["TwilioAdapter"]
    ).TwilioAdapter(AUTH_TOKEN))
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
