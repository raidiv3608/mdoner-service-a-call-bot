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


def test_duplicate_webhook_returns_same_greeting_without_new_state(monkeypatch) -> None:
    params = {"CallSid": "CA_DUPLICATE", "From": "+15550000000"}
    monkeypatch.setattr(main_module, "twilio_adapter", TwilioAdapter(auth_token=AUTH_TOKEN))

    first = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=signed_headers(params),
    )
    second = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=signed_headers(params),
    )

    assert first.status_code == second.status_code == 200
    assert first.content == second.content


def test_malformed_webhook_input_returns_safe_error() -> None:
    response = client.post(WEBHOOK_PATH, data={"From": "+15550000000"})

    assert response.status_code == 400


def test_provider_failure_returns_safe_error(monkeypatch) -> None:
    adapter = TwilioAdapter(auth_token=AUTH_TOKEN)

    def fail_greeting() -> str:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(adapter, "greeting_response", fail_greeting)
    monkeypatch.setattr(main_module, "twilio_adapter", adapter)
    params = {"CallSid": "CA_PROVIDER_FAILURE"}

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=signed_headers(params),
    )

    assert response.status_code == 502


def test_trigger_outbound_call_returns_call_sid(monkeypatch) -> None:
    adapter = MockTwilioAdapter(call_sid="CA_TRIGGERED")
    monkeypatch.setattr(main_module, "twilio_adapter", adapter)
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, twilio_public_base_url="https://service.example.com"),
    )

    response = client.post(
        "/v1/calls/trigger",
        json={"to_phone_number": "+15551234567"},
    )

    assert response.status_code == 200
    assert response.json() == {"call_sid": "CA_TRIGGERED"}


def test_trigger_rejects_invalid_phone_number(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, twilio_public_base_url="https://service.example.com"),
    )

    response = client.post(
        "/v1/calls/trigger",
        json={"to_phone_number": "555-123-4567"},
    )

    assert response.status_code == 422


def test_trigger_returns_safe_error_on_provider_failure(monkeypatch) -> None:
    adapter = MockTwilioAdapter(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr(main_module, "twilio_adapter", adapter)
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, twilio_public_base_url="https://service.example.com"),
    )

    response = client.post(
        "/v1/calls/trigger",
        json={"to_phone_number": "+15551234567"},
    )

    assert response.status_code == 502


def test_trigger_builds_exact_voice_webhook_url(monkeypatch) -> None:
    adapter = MockTwilioAdapter(call_sid="CA_URL_CHECK")
    monkeypatch.setattr(main_module, "twilio_adapter", adapter)
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, twilio_public_base_url="https://service.example.com/"),
    )

    response = client.post(
        "/v1/calls/trigger",
        json={"to_phone_number": "+447700900123"},
    )

    assert response.status_code == 200
    assert adapter.calls == [
        ("+447700900123", "https://service.example.com/webhooks/twilio/voice/start")
    ]


class MockTwilioAdapter:
    def __init__(self, call_sid: str = "CA_MOCK", error: Exception | None = None) -> None:
        self.call_sid = call_sid
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def start_outbound_call(self, to_phone_number: str, voice_url: str) -> str:
        self.calls.append((to_phone_number, voice_url))
        if self.error:
            raise self.error
        return self.call_sid
