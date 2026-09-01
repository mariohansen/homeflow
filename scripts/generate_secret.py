"""Print a fresh random secret for HOMEFLOW_ID_SALT.

Generates a new value; it never reads or echoes an existing one. Paste the
output into the untracked .env file.
"""

from __future__ import annotations

import secrets


def main() -> None:
    print(secrets.token_hex(32))


if __name__ == "__main__":
    main()
