"""Domain errors that map onto safe API problem details (see SECURITY.md).

Every ``detail`` is an authored constant. Provider messages, hostnames, entity
ids and stack traces never travel through these objects.
"""

from __future__ import annotations


class HomeFlowError(Exception):
    problem_type = "internal_error"
    title = "Internal error"
    status = 500
    default_detail = "The request could not be completed."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.default_detail
        super().__init__(self.detail)


class UnauthenticatedError(HomeFlowError):
    problem_type = "unauthenticated"
    title = "Authentication required"
    status = 401
    default_detail = "A valid registered client credential is required."


class ForbiddenError(HomeFlowError):
    problem_type = "forbidden"
    title = "Not permitted"
    status = 403
    default_detail = "This client is not permitted to perform the action."


class ActionAuthorizationRequiredError(HomeFlowError):
    """A HIGH-risk action needs a fresh, server-issued action authorisation."""

    problem_type = "action_authorization_required"
    title = "Additional authorisation required"
    status = 403
    default_detail = (
        "This action requires a fresh device-owner authorisation, which this "
        "deployment does not provide yet."
    )


class DeviceNotFoundError(HomeFlowError):
    problem_type = "device_not_found"
    title = "Device not found"
    status = 404
    default_detail = "No such device."


class CommandNotFoundError(HomeFlowError):
    problem_type = "command_not_found"
    title = "Command not found"
    status = 404
    default_detail = "No such command."


class CapabilityNotSupportedError(HomeFlowError):
    problem_type = "capability_not_supported"
    title = "Unsupported action"
    status = 422
    default_detail = "The device does not support this action."


class InvalidParametersError(HomeFlowError):
    problem_type = "invalid_parameters"
    title = "Invalid parameters"
    status = 422
    default_detail = "The command parameters are invalid."


class ParameterOutOfRangeError(HomeFlowError):
    problem_type = "parameter_out_of_range"
    title = "Parameter out of range"
    status = 422
    default_detail = "The requested value is outside the device's verified range."


class DeviceUnavailableError(HomeFlowError):
    problem_type = "device_unavailable"
    title = "Device unavailable"
    status = 409
    default_detail = "The device is currently offline."


class RateLimitedError(HomeFlowError):
    problem_type = "rate_limited"
    title = "Too many requests"
    status = 429
    default_detail = "Too many requests. Slow down and retry shortly."
