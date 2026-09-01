"""Typed application configuration.

The security policy requires that development conveniences cannot be activated
by accident in production. The validators below fail closed at startup rather
than logging a warning and continuing.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: Fixed, deliberately public salt used for demo-mode device identifiers so that
#: screenshots, fixtures and tests are reproducible. Synthetic data only.
DEMO_ID_SALT = "homeflow-demo-mode-public-salt"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOMEFLOW_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.DEVELOPMENT

    #: Serve only synthetic devices. Must be off in production.
    demo_mode: bool = True

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"

    #: Host header allowlist. A wildcard is refused in production because a
    #: permissive Host check is what makes DNS rebinding work.
    #: NoDecode keeps pydantic-settings from trying to read the environment
    #: value as JSON, so a plain comma-separated list works.
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ("*",)

    #: Secret used to derive HomeFlow device UUIDs from provider identifiers.
    #: Keeps provider ids out of public identifiers without a database.
    id_salt: SecretStr | None = None

    #: Local development client credential. Rejected in production.
    dev_client_token: SecretStr | None = None

    #: Hard ceiling for a single provider command, in seconds.
    command_timeout_seconds: float = Field(default=8.0, gt=0.0, le=60.0)
    #: Bounded budget for the read-back that follows a command timeout.
    reconcile_timeout_seconds: float = Field(default=4.0, gt=0.0, le=30.0)

    #: State older than this is reported as stale to clients.
    stale_after_seconds: int = Field(default=120, ge=5)

    #: Per-client request budget (token bucket) for the command endpoint.
    command_rate_limit_per_minute: int = Field(default=60, ge=1)
    #: Per-connection inbound WebSocket message budget.
    websocket_rate_limit_per_minute: int = Field(default=120, ge=1)
    #: Bounded per-subscriber event queue; overflow triggers a resync hint.
    event_queue_size: int = Field(default=256, ge=8)

    #: Directory of the installable web client. When unset the gateway looks
    #: for ``apps/web`` next to the backend and serves it if present.
    web_client_dir: Path | None = None

    # -- Bestway AirJet ---------------------------------------------------
    #: Off by default: the adapter talks to real hardware.
    bestway_enabled: bool = False
    bestway_host: str | None = None
    bestway_port: int = Field(default=12416, ge=1, le=65535)
    bestway_poll_seconds: float = Field(default=15.0, ge=2.0, le=300.0)
    #: Built-in datapoint layout to start from.
    bestway_profile: str = "airjet-candidate"
    #: Operator-supplied layout, which overrides the built-in one.
    bestway_profile_path: Path | None = None
    #: Set only after decoded values have been compared with the physical panel.
    #: Until then the controller is not exposed as a device at all.
    bestway_trust_profile: bool = False
    #: Datapoints released for writing, one at a time, each after the physical
    #: effect has been observed. Comma separated, e.g. "HEATER,FILTER_PUMP".
    bestway_write_enabled: Annotated[tuple[str, ...], NoDecode] = ()

    #: Demo simulation only. Ignored unless demo_mode is enabled.
    demo_tick_seconds: float = Field(default=2.0, gt=0.0)
    demo_failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("allowed_hosts", "bestway_write_enabled", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        """Accept a comma-separated string, which is how an env var carries a list."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @model_validator(mode="after")
    def _fail_closed(self) -> Self:
        if self.env is Environment.PRODUCTION:
            if self.demo_mode:
                raise ValueError(
                    "HOMEFLOW_DEMO_MODE must be false in production: demo mode serves "
                    "synthetic devices and would hide real household state."
                )
            if self.dev_client_token is not None:
                raise ValueError(
                    "HOMEFLOW_DEV_CLIENT_TOKEN must not be set in production. "
                    "Register a client instead."
                )
            if "*" in self.allowed_hosts:
                raise ValueError(
                    "HOMEFLOW_ALLOWED_HOSTS must list the exact private hostnames the "
                    "gateway answers on; a wildcard enables DNS rebinding."
                )
        if self.bestway_enabled and not self.bestway_host:
            raise ValueError("HOMEFLOW_BESTWAY_HOST is required when the adapter is enabled")
        if self.bestway_write_enabled and not self.bestway_trust_profile:
            raise ValueError(
                "HOMEFLOW_BESTWAY_WRITE_ENABLED requires HOMEFLOW_BESTWAY_TRUST_PROFILE: a "
                "capability cannot be released for writing while the datapoint layout is "
                "still unverified."
            )
        if self.bestway_enabled and self.demo_mode:
            raise ValueError(
                "Demo mode must not run alongside the Bestway adapter: a demo build must "
                "not be able to reach real hardware."
            )
        if not self.demo_mode and self.id_salt is None:
            raise ValueError(
                "HOMEFLOW_ID_SALT is required outside demo mode so that public device "
                "identifiers cannot be reversed to provider identifiers."
            )
        return self

    @property
    def effective_id_salt(self) -> str:
        if self.id_salt is not None:
            return self.id_salt.get_secret_value()
        return DEMO_ID_SALT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
