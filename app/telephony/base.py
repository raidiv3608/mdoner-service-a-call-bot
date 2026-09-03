"""Provider-independent telephony contract."""

from abc import ABC, abstractmethod
from collections.abc import Mapping


class TelephonyAdapter(ABC):
    """Interface used by Service A's HTTP layer for voice webhooks."""

    @abstractmethod
    def validate_webhook(
        self,
        url: str,
        params: Mapping[str, str],
        signature: str | None,
    ) -> bool:
        """Return whether a provider webhook request is authentic."""

    @abstractmethod
    def greeting_response(self) -> str:
        """Return the provider-specific response for the call greeting."""

    @abstractmethod
    def start_outbound_call(self, to_phone_number: str, voice_url: str) -> str:
        """Start an outbound call and return the provider call identifier."""
