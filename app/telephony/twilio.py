"""Twilio implementation of the Service A telephony contract."""

from collections.abc import Mapping

from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from app.telephony.base import TelephonyAdapter


class TwilioAdapter(TelephonyAdapter):
    """Build and validate the deterministic Twilio voice interaction."""

    OUTBOUND_REQUEST_TIMEOUT_SECONDS = 10

    GREETING = (
        "Hello, this is your memory assistance companion. "
        "Take your time, and I will be here to help."
    )

    def __init__(
        self,
        account_sid: str = "",
        auth_token: str = "",
        from_phone_number: str = "",
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_phone_number = from_phone_number
        self._client = (
            Client(
                account_sid,
                auth_token,
                http_client=TwilioHttpClient(timeout=self.OUTBOUND_REQUEST_TIMEOUT_SECONDS),
            )
            if account_sid and auth_token
            else None
        )
        self._validator = RequestValidator(auth_token)

    def validate_webhook(
        self,
        url: str,
        params: Mapping[str, str],
        signature: str | None,
    ) -> bool:
        # Security: Fail closed if signature is missing or auth token is not configured
        if not signature or not self._auth_token:
            return False
        return bool(self._validator.validate(url, dict(params), signature))

    def greeting_response(self) -> str:
        response = VoiceResponse()
        self._say(response, self.GREETING)
        return str(response)

    def gather_response(self, messages: tuple[str, ...], action_url: str) -> str:
        response = VoiceResponse()
        gather = response.gather(
            input="speech",
            action=action_url,
            method="POST",
            speech_timeout="auto",
        )
        for message in messages:
            self._say(gather, message)
        return str(response)

    def greeting_gather_response(self, message: str, action_url: str) -> str:
        self.greeting_response()
        response = VoiceResponse()
        self._say(response, self.GREETING)
        gather = response.gather(
            input="speech",
            action=action_url,
            method="POST",
            speech_timeout="auto",
        )
        self._say(gather, message)
        return str(response)

    def terminal_response(self, messages: tuple[str, ...]) -> str:
        response = VoiceResponse()
        for message in messages:
            self._say(response, message)
        response.hangup()
        return str(response)

    def _say(self, response: VoiceResponse, message: str) -> None:
        response.say(message, voice="alice", language="en-US")

    def start_outbound_call(self, to_phone_number: str, voice_url: str) -> str:
        if not self._client or not self._from_phone_number:
            raise RuntimeError("Twilio outbound call configuration is incomplete")
        call = self._client.calls.create(
            to=to_phone_number,
            from_=self._from_phone_number,
            url=voice_url,
        )
        return str(call.sid)
