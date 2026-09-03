"""Twilio implementation of the Service A telephony contract."""

from collections.abc import Mapping

from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from app.telephony.base import TelephonyAdapter


class TwilioAdapter(TelephonyAdapter):
    """Build and validate the deterministic Twilio voice interaction."""

    def __init__(self, auth_token: str) -> None:
        self._validator = RequestValidator(auth_token)

    def validate_webhook(
        self,
        url: str,
        params: Mapping[str, str],
        signature: str | None,
    ) -> bool:
        if not signature:
            return False
        return bool(self._validator.validate(url, dict(params), signature))

    def greeting_response(self) -> str:
        response = VoiceResponse()
        response.say(
            "Hello, this is your memory assistance companion. "
            "Take your time, and I will be here to help.",
            voice="alice",
            language="en-US",
        )
        return str(response)
