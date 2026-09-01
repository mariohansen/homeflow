"""Application factory and process lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import AsyncIterator

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from homeflow import __version__
from homeflow.api.errors import install_error_handlers
from homeflow.api.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware
from homeflow.api.v1 import router as v1_router
from homeflow.api.v1 import ws as v1_ws
from homeflow.config.settings import Environment, Settings, get_settings
from homeflow.container import Container, build_container
from homeflow.devices.models import StateSource
from homeflow.devices.service import DeviceService
from homeflow.integrations.base.provider import DeviceProvider
from homeflow.log import configure_logging, get_logger

_logger = get_logger(__name__)

_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0


async def run_provider_stream(provider: DeviceProvider, devices: DeviceService) -> None:
    """Consume one adapter's event stream, restarting with capped backoff.

    A failing adapter must not take the gateway down or starve other providers
    (CLAUDE.md sections 47 and 48).
    """
    backoff = _INITIAL_BACKOFF_SECONDS
    while True:
        try:
            async for event in provider.subscribe():
                devices.ingest(event.ref, event.state, source=StateSource.PROVIDER_EVENT)
                backoff = _INITIAL_BACKOFF_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("provider.stream_failed", provider=provider.name)
            jitter = random.random()  # noqa: S311 - backoff spread, not security
            await asyncio.sleep(min(backoff + jitter, _MAX_BACKOFF_SECONDS))
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    is_dev = resolved.env is not Environment.PRODUCTION

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container: Container = build_container(resolved)
        app.state.container = container
        await container.devices.bootstrap(list(container.providers.values()))

        tasks = [
            asyncio.create_task(
                run_provider_stream(provider, container.devices),
                name=f"provider-stream-{provider.name}",
            )
            for provider in container.providers.values()
        ]
        _logger.info(
            "gateway.started",
            env=resolved.env.value,
            demo_mode=resolved.demo_mode,
            provider_count=len(container.providers),
            device_count=len(container.devices.list_devices()),
        )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            _logger.info("gateway.stopped")

    app = FastAPI(
        title="HomeFlow",
        version=__version__,
        summary="Canonical household control API",
        lifespan=lifespan,
        # Interactive documentation is a development convenience only.
        docs_url="/docs" if is_dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if is_dev else None,
    )

    # No CORS middleware: the client is a native/first-party application and no
    # browser origin is trusted by default (CLAUDE.md section 73).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(resolved.allowed_hosts))
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    install_error_handlers(app)
    app.include_router(v1_router.router)
    app.include_router(v1_ws.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Liveness probe. Deliberately reveals nothing about the household."""
        return {"status": "ok"}

    return app
