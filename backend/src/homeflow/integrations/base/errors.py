"""Adapter-level failures.

Provider detail stays inside these exceptions and is only written to sanitized
local logs; clients receive a stable failure code (see SECURITY.md).
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for adapter failures."""

    failure_code = "provider_error"


class ProviderUnavailableError(ProviderError):
    """The device or provider could not be reached."""

    failure_code = "device_unavailable"


class ProviderRejectedError(ProviderError):
    """The provider understood the command and refused it."""

    failure_code = "command_rejected"
