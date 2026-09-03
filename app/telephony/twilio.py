"""Twilio implementation of the Service A telephony contract."""

from collections.abc import Mapping

from twilio.rest import Client
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from app.telephony.base import TelephonyAdapter


class TwilioAdapter(TelephonyAdapter):
    """Build and validate the deterministic Twilio voice interaction."""

    def __init__(
        self,
        account_sid: str = "",
        auth_token: str = "",
        from_phone_number: str = "",
    ) -> None:
        self._account_sid = account_sid
        self._from_phone_number = from_phone_number
        self._client = Client(account_sid, auth_token) if account_sid and auth_token else None
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

    def start_outbound_call(self, to_phone_number: str, voice_url: str) -> str:
        if not self._client or not self._from_phone_number:
            raise RuntimeError("Twilio outbound call configuration is incomplete")
        call = self._client.calls.create(
            to=to_phone_number,
            from_=self._from_phone_number,
            url=voice_url,
        )
        return str(call.sid)
