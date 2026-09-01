"""Run the client's rendering checks, which need a JavaScript engine.

The rule they guard is a product rule, not a cosmetic one: a control appears
only for a capability the gateway released, and a value the gateway reports is
always shown. Conflating the two once hid the heater, filter and bubble state of
a working controller behind an empty card.

Skipped where node is unavailable so the Python suite still runs anywhere.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parents[1] / "web" / "render_pool.test.mjs"

pytestmark = pytest.mark.skipif(
    NODE is None or not SCRIPT.is_file(),
    reason="node or the client rendering checks are not available",
)


def test_the_pool_card_shows_state_and_gates_controls() -> None:
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - fixed executable, fixed script path
        [NODE, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
