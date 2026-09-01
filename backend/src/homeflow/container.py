"""Composition root.

Wiring lives in one place so that what a deployment can reach is auditable by
reading a single file. In demo mode the only registered adapter is the synthetic
one, which makes it structurally impossible for a demo build to touch a real
device (see docs/security/privacy-model.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from homeflow.audit.log import InMemoryAuditLog
from homeflow.auth.registry import ClientRegistry, build_client_registry
from homeflow.auth.tickets import TicketStore
from homeflow.clock import Clock, SystemClock
from homeflow.commands.service import CommandService
from homeflow.config.settings import Settings
from homeflow.devices.registry import DeviceRegistry
from homeflow.devices.service import DeviceService
from homeflow.events.bus import EventBus
from homeflow.integrations.base.provider import DeviceProvider
from homeflow.integrations.demo.provider import DemoProvider
from homeflow.log import get_logger
from homeflow.ratelimit import RateLimiter

_logger = get_logger(__name__)


@dataclass(slots=True)
class Container:
    settings: Settings
    clock: Clock
    bus: EventBus
    audit: InMemoryAuditLog
    clients: ClientRegistry
    tickets: TicketStore
    registry: DeviceRegistry
    devices: DeviceService
    commands: CommandService
    providers: dict[str, DeviceProvider]
    auth_rate_limiter: RateLimiter
    command_rate_limiter: RateLimiter


def build_providers(settings: Settings, clock: Clock) -> dict[str, DeviceProvider]:
    if settings.demo_mode:
        provider = DemoProvider(
            clock=clock,
            failure_rate=settings.demo_failure_rate,
            tick_seconds=settings.demo_tick_seconds,
        )
        return {provider.name: provider}

    # Real adapters arrive with roadmap phases 2 to 4. Until then a non-demo
    # deployment exposes an empty, harmless device list rather than guessing.
    _logger.warning("providers.none_configured", demo_mode=False)
    return {}


def build_container(settings: Settings, *, clock: Clock | None = None) -> Container:
    resolved_clock = clock or SystemClock()
    bus = EventBus(queue_size=settings.event_queue_size)
    audit = InMemoryAuditLog()
    registry = DeviceRegistry(id_salt=settings.effective_id_salt)
    devices = DeviceService(registry=registry, bus=bus, clock=resolved_clock)
    providers = build_providers(settings, resolved_clock)
    commands = CommandService(
        devices=devices,
        providers=providers,
        bus=bus,
        audit=audit,
        clock=resolved_clock,
        settings=settings,
    )
    return Container(
        settings=settings,
        clock=resolved_clock,
        bus=bus,
        audit=audit,
        clients=build_client_registry(settings),
        tickets=TicketStore(resolved_clock),
        registry=registry,
        devices=devices,
        commands=commands,
        providers=providers,
        auth_rate_limiter=RateLimiter(rate_per_minute=30, clock=resolved_clock, burst=10),
        command_rate_limiter=RateLimiter(
            rate_per_minute=settings.command_rate_limit_per_minute,
            clock=resolved_clock,
        ),
    )
