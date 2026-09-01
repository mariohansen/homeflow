"""Shared test fixtures.

Tests never reach the household: only the synthetic demo provider and explicit
doubles are wired in (see docs/security/privacy-model.md).
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from homeflow.config.settings import Environment, Settings
from homeflow.main import create_app

sys.path.insert(0, str(Path(__file__).parent))

DEV_TOKEN = "test-token-0123456789abcdef"


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's local configuration out of the test run.

    Without this, an exported HOMEFLOW_* variable or the untracked .env would
    silently change what the fail-closed configuration tests actually assert.
    """
    for name in list(os.environ):
        if name.startswith("HOMEFLOW_"):
            monkeypatch.delenv(name, raising=False)


def make_settings(**overrides: object) -> Settings:
    """Build settings without reading any .env file."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Execute a coroutine in a fresh event loop."""
    return asyncio.run(coro)


@pytest.fixture
def settings() -> Settings:
    return make_settings(
        env=Environment.TEST,
        demo_mode=True,
        dev_client_token=SecretStr(DEV_TOKEN),
        command_timeout_seconds=2.0,
        reconcile_timeout_seconds=1.0,
        # Effectively disable the background simulation so tests are stable.
        demo_tick_seconds=3600.0,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEV_TOKEN}"}
