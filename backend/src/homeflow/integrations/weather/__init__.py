"""Outdoor temperature, normalised into an ordinary sensor device."""

from homeflow.integrations.weather.provider import (
    PROVIDER_NAME,
    OpenMeteoProvider,
    outdoor_ref,
)

__all__ = ["PROVIDER_NAME", "OpenMeteoProvider", "outdoor_ref"]
