from dataclasses import replace
import importlib

import pytest

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


def signed_headers_for(path: str, params: dict[str, str], query: str = "") -> dict[str, str]:
    url = f"http://testserver{path}{query}"
    signature = RequestValidator(AUTH_TOKEN).compute_signature(url, params)
    return {"X-Twilio-Signature": signature}


@pytest.fixture(autouse=True)
def isolated_conversation_database(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main_module, "twilio_adapter", TwilioAdapter(auth_token=AUTH_TOKEN))
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, local_database_path=str(tmp_path / "service.sqlite3")),
    )


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


def start_real_flow(call_sid: str) -> None:
    params = {"CallSid": call_sid, "From": "+15550000000"}
    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=signed_headers_for(WEBHOOK_PATH, params),
    )
    assert response.status_code == 200
    assert "/webhooks/twilio/voice/readiness?turn=1" in response.text


def submit_readiness(call_sid: str, answer: str, turn: int = 1):
    path = "/webhooks/twilio/voice/readiness"
    params = {"CallSid": call_sid, "SpeechResult": answer, "Confidence": "0.9"}
    query = f"?turn={turn}"
    return client.post(
        path + query,
        data=params,
        headers=signed_headers_for(path, params, query),
    )


def submit_answer(call_sid: str, answer: str, turn: int):
    path = "/webhooks/twilio/voice/answer"
    params = {"CallSid": call_sid, "SpeechResult": answer, "Confidence": "0.9"}
    query = f"?turn={turn}"
    return client.post(
        path + query,
        data=params,
        headers=signed_headers_for(path, params, query),
    )


def test_real_flow_start_and_readiness_return_gathered_orientation_question() -> None:
    call_sid = "CA_REAL_FLOW_START"
    start_real_flow(call_sid)

    response = submit_readiness(call_sid, "yes")

    assert response.status_code == 200
    assert "Thank you. We will take this one question at a time." in response.text
    assert "What day is it today?" in response.text
    assert "/webhooks/twilio/voice/answer?turn=2" in response.text


def test_real_flow_answer_progresses_to_next_question() -> None:
    call_sid = "CA_REAL_FLOW_PROGRESS"
    start_real_flow(call_sid)
    assert submit_readiness(call_sid, "ready").status_code == 200

    response = submit_answer(call_sid, "monday", 2)

    assert response.status_code == 200
    assert "What month is it?" in response.text
    assert "What day is it today?" not in response.text


def test_real_flow_stop_returns_terminal_twiml() -> None:
    call_sid = "CA_REAL_FLOW_STOP"
    start_real_flow(call_sid)
    submit_readiness(call_sid, "yes")

    response = submit_answer(call_sid, "stop", 2)

    assert response.status_code == 200
    assert "Understood. Thank you for your time. Goodbye." in response.text
    assert "<Hangup" in response.text


def test_real_flow_unscorable_repeats_once_then_terminates() -> None:
    call_sid = "CA_REAL_FLOW_UNSCORABLE"
    start_real_flow(call_sid)
    submit_readiness(call_sid, "yes")

    retry = submit_answer(call_sid, "unclear", 2)
    terminal = submit_answer(call_sid, "", 3)

    assert retry.status_code == 200
    assert "try once more" in retry.text
    assert "/webhooks/twilio/voice/answer?turn=3" in retry.text
    assert terminal.status_code == 200
    assert "end the call now" in terminal.text
    assert "<Hangup" in terminal.text


def test_real_flow_three_incorrect_answers_terminate() -> None:
    call_sid = "CA_REAL_FLOW_INCORRECT"
    start_real_flow(call_sid)
    submit_readiness(call_sid, "yes")

    assert submit_answer(call_sid, "wrong", 2).status_code == 200
    assert submit_answer(call_sid, "wrong", 3).status_code == 200
    terminal = submit_answer(call_sid, "wrong", 4)

    assert terminal.status_code == 200
    assert "It seems like this is not a good time" in terminal.text
    assert "<Hangup" in terminal.text


def test_duplicate_webhook_replays_response_without_advancing_state() -> None:
    call_sid = "CA_REAL_FLOW_DUPLICATE"
    start_real_flow(call_sid)
    first = submit_readiness(call_sid, "yes")
    duplicate = submit_readiness(call_sid, "yes")

    assert first.status_code == duplicate.status_code == 200
    assert first.content == duplicate.content
    next_question = submit_answer(call_sid, "monday", 2)
    assert next_question.status_code == 200
    assert "What month is it?" in next_question.text
