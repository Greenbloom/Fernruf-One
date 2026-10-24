"""Startup warnings for optional third-party inference (TypeSafe/Jev)."""

from __future__ import annotations

import logging

from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.privacy")


def log_privacy_warnings(settings: Settings) -> None:
    if settings.privacy_minimal:
        LOG.info(
            "FERNRUF_PRIVACY_MINIMAL=true: TypeSafe/Jev is disabled; only LLM and TTS "
            "are intended third-party services. See PRIVACY.md."
        )
    if settings.typesafe_requests_enabled():
        LOG.warning(
            "TypeSafe/Jev is active (FERNRUF_TYPESAFE_ENABLED with TYPESAFE_API_KEY): "
            "task and/or callee transcript text may be sent to api.typesafe.ai. See PRIVACY.md."
        )
    if settings.mood_enabled and settings.typesafe_requests_enabled():
        LOG.warning(
            "FERNRUF_MOOD_ENABLED=true: callee emotion inference runs on TypeSafe Jev "
            "(extra transparency duties may apply under GDPR and EU AI Act Art. 50(3)). "
            "See PRIVACY.md."
        )
