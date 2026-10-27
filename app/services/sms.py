"""Outbound SMS seam.

Milestone 1 only needs one time codes for password reset, so the console
provider is enough. The provider abstraction and per message retries arrive
with the reminder engine in a later milestone.
"""

from __future__ import annotations

import logging

from app.config import get_settings

logger = logging.getLogger("agura.sms")


def send_sms(phone: str, message: str) -> bool:
    settings = get_settings()
    if not settings.sms_enabled:
        logger.info("SMS disabled, would send to %s: %s", phone, message)
        return False
    if settings.sms_provider == "console":
        logger.info("Console SMS to %s: %s", phone, message)
        return True
    logger.warning("SMS provider %s is not configured yet", settings.sms_provider)
    return False
