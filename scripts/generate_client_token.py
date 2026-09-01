"""Print a fresh development client credential.

Development and test environments only: HOMEFLOW_DEV_CLIENT_TOKEN is refused in
production, where a client must be registered explicitly.
"""

from __future__ import annotations

import secrets


def main() -> None:
    print(secrets.token_urlsafe(32))


if __name__ == "__main__":
    main()
