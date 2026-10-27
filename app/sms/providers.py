"""SMS provider abstraction.

The console provider is the default in development so nothing is ever really sent.
Africa's Talking is the production implementation. Nobody outside this package talks
to a provider directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import Settings

logger = logging.getLogger("agura.sms")


@dataclass(frozen=True)
class SmsSendResult:
    delivered: bool
    provider: str
    provider_message_id: str | None = None
    error: str | None = None


class SmsProvider:
    name = "base"

    def send(self, phone: str, body: str) -> SmsSendResult:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleSmsProvider(SmsProvider):
    """Development fake: logs instead of sending, and always reports success."""

    name = "console"

    def send(self, phone: str, body: str) -> SmsSendResult:
        logger.info("Console SMS to %s: %s", phone, body)
        return SmsSendResult(delivered=True, provider=self.name, provider_message_id="console")


class DisabledSmsProvider(SmsProvider):
    """Used when SMS_ENABLED is false: records the attempt without sending."""

    name = "disabled"

    def send(self, phone: str, body: str) -> SmsSendResult:
        logger.info("SMS disabled, not sending to %s: %s", phone, body)
        return SmsSendResult(delivered=False, provider=self.name, error="sms_disabled")


class AfricasTalkingProvider(SmsProvider):
    """Africa's Talking bulk SMS. Only used when SMS_ENABLED is true."""

    name = "africastalking"
    endpoint = "https://api.africastalking.com/version1/messaging"

    def __init__(self, settings: Settings) -> None:
        self._username = settings.sms_username or ""
        self._api_key = settings.sms_api_key or ""
        self._sender = settings.sms_sender_id or "AGUra"

    def send(self, phone: str, body: str) -> SmsSendResult:
        if not self._username or not self._api_key:
            return SmsSendResult(delivered=False, provider=self.name, error="missing_credentials")
        try:
            response = httpx.post(
                self.endpoint,
                headers={
                    "ApiKey": self._api_key,
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "username": self._username,
                    "to": phone,
                    "message": body,
                    "from": self._sender,
                },
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            return SmsSendResult(delivered=False, provider=self.name, error=str(exc)[:300])

        if response.status_code >= 400:
            return SmsSendResult(
                delivered=False,
                provider=self.name,
                error=f"http_{response.status_code}: {response.text[:200]}",
            )
        try:
            data = response.json()
            recipients = data.get("SMSMessageData", {}).get("Recipients", [])
            if recipients:
                first = recipients[0]
                delivered = str(first.get("status", "")).lower() in {"success", "sent", "queued"}
                return SmsSendResult(
                    delivered=delivered,
                    provider=self.name,
                    provider_message_id=first.get("messageId"),
                    error=None if delivered else first.get("status"),
                )
        except ValueError:
            return SmsSendResult(delivered=True, provider=self.name, provider_message_id=None)
        return SmsSendResult(delivered=True, provider=self.name)


def get_provider(settings: Settings) -> SmsProvider:
    if not settings.sms_enabled:
        return DisabledSmsProvider()
    if settings.sms_provider == "africastalking":
        return AfricasTalkingProvider(settings)
    return ConsoleSmsProvider()
