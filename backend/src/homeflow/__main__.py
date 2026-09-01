"""Run the gateway.

Binding defaults to loopback. A deployment that must be reachable from the
private VPN sets HOMEFLOW_API_HOST explicitly; there is never a public listener.
"""

from __future__ import annotations

import uvicorn

from homeflow.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "homeflow.main:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
