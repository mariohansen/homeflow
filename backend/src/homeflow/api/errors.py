"""Safe problem details for every failure path (see SECURITY.md).

Clients receive a stable machine-readable ``type`` and an authored ``detail``.
Stack traces, provider bodies, hostnames and internal identifiers stay in the
sanitized local log, correlated through ``correlationId``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from homeflow.errors import HomeFlowError
from homeflow.log import get_logger

_logger = get_logger(__name__)

_STATUS_TITLES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Bad request"),
    401: ("unauthenticated", "Authentication required"),
    403: ("forbidden", "Not permitted"),
    404: ("not_found", "Not found"),
    405: ("method_not_allowed", "Method not allowed"),
    409: ("conflict", "Conflict"),
    413: ("payload_too_large", "Payload too large"),
    429: ("rate_limited", "Too many requests"),
}


class ProblemDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    title: str
    status: int
    detail: str
    correlationId: str


def _correlation_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else "unknown"


def _problem(request: Request, *, type_: str, title: str, status: int, detail: str) -> JSONResponse:
    problem = ProblemDetail(
        type=type_,
        title=title,
        status=status,
        detail=detail,
        correlationId=_correlation_id(request),
    )
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(),
        media_type="application/problem+json",
        headers={"X-Correlation-Id": problem.correlationId},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HomeFlowError)
    async def _homeflow_error(request: Request, exc: HomeFlowError) -> JSONResponse:
        _logger.info(
            "api.error",
            result_code=exc.problem_type,
            correlation_id=_correlation_id(request),
            path=request.url.path,
        )
        return _problem(
            request,
            type_=exc.problem_type,
            title=exc.title,
            status=exc.status,
            detail=exc.detail,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # The offending payload is deliberately not echoed back.
        _logger.info(
            "api.validation_error",
            correlation_id=_correlation_id(request),
            path=request.url.path,
            error_count=len(exc.errors()),
        )
        return _problem(
            request,
            type_="invalid_request",
            title="Invalid request",
            status=422,
            detail="The request body or parameters are invalid.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        type_, title = _STATUS_TITLES.get(exc.status_code, ("error", "Request failed"))
        return _problem(
            request,
            type_=type_,
            title=title,
            status=exc.status_code,
            detail=title + ".",
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        _logger.exception(
            "api.unhandled_error",
            correlation_id=_correlation_id(request),
            path=request.url.path,
        )
        return _problem(
            request,
            type_="internal_error",
            title="Internal error",
            status=500,
            detail="The request could not be completed.",
        )
