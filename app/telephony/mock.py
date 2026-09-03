"""Deterministic in-memory telephony adapter for local testing."""

from collections.abc import Iterable

from app.telephony.base import TelephonyAdapter


class MockTelephonyAdapter(TelephonyAdapter):
    """Record bot speech and return a predefined sequence of patient answers."""

    def __init__(self, responses: Iterable[str | None]) -> None:
        self._responses = iter(responses)
        self.transcript: list[str] = []
        self.call_started = False

    def validate_webhook(self, url, params, signature) -> bool:
        return True

    def greeting_response(self) -> str:
        return (
            "Hello, this is your memory assistance companion. "
            "Take your time, and I will be here to help."
        )

    def start_outbound_call(self, to_phone_number: str, voice_url: str) -> str:
        self.start_call()
        return "MOCK-CALL-SID"

    def start_call(self) -> None:
        self.call_started = True
        self.transcript.append("START_CALL")

    def speak(self, message: str) -> None:
        self.transcript.append(f"BOT: {message}")

    def listen(self) -> str | None:
        response = next(self._responses, None)
        self.transcript.append(f"PATIENT: {response or '[NO INPUT]'}")
        return response
