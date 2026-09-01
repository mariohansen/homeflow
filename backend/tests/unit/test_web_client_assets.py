"""Invariants of the shipped web client.

There is no browser in CI, so the properties that would otherwise only fail at
runtime are asserted against the source: the strict Content Security Policy of
ADR 0011 only holds if the client really contains no inline script, style or
event handler, and device names from vendor adapters are untrusted input that
must never reach innerHTML.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[3] / "apps" / "web"

pytestmark = pytest.mark.skipif(not WEB.is_dir(), reason="web client not present")


def _html() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


def _modules() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in (WEB / "js").glob("*.js")}


def _string_keys() -> set[str]:
    source = (WEB / "js" / "strings.js").read_text(encoding="utf-8")
    return set(re.findall(r'"([a-zA-Z0-9_.]+)":', source))


def test_no_inline_script_survives_the_content_security_policy() -> None:
    html = _html()
    assert not re.search(r"<script(?![^>]*\ssrc=)", html), "inline <script> would be blocked"
    assert not re.search(r"<style", html), "inline <style> would be blocked"
    assert not re.search(r'\sstyle="', html), "style attribute would be blocked"
    assert not re.search(r'\son[a-z]+="', html), "inline event handler would be blocked"


def test_untrusted_device_text_never_reaches_inner_html() -> None:
    for name, source in _modules().items():
        assert "innerHTML" not in source, f"{name} uses innerHTML"
        assert "outerHTML" not in source, f"{name} uses outerHTML"
        assert "eval(" not in source, f"{name} uses eval"


def test_no_external_origin_is_referenced() -> None:
    """Everything is same-origin; the policy blocks anything else silently."""
    sources = {"index.html": _html(), "app.css": (WEB / "app.css").read_text(encoding="utf-8")}
    sources.update(_modules())
    for name, source in sources.items():
        for match in re.findall(r"https?://[^\s\"')]+", source):
            pytest.fail(f"{name} references an external origin: {match}")


def test_every_static_string_key_exists() -> None:
    keys = _string_keys()
    missing = {key for key in re.findall(r'data-i18n="([^"]+)"', _html()) if key not in keys}
    assert not missing, f"missing translations: {sorted(missing)}"


def test_every_translation_call_resolves() -> None:
    keys = _string_keys()
    missing: set[str] = set()
    for source in _modules().values():
        missing |= {key for key in re.findall(r't\("([a-zA-Z0-9_.]+)"', source) if key not in keys}
    assert not missing, f"missing translations: {sorted(missing)}"


def test_every_referenced_element_exists() -> None:
    html = _html()
    ids = set(re.findall(r'id="([^"]+)"', html))
    referenced = set(re.findall(r'getElementById\("([^"]+)"\)', _modules()["app.js"]))
    assert referenced <= ids, f"missing elements: {sorted(referenced - ids)}"


def test_every_module_import_resolves() -> None:
    for name, source in _modules().items():
        for spec in re.findall(r'from "\./([^"]+)"', source):
            assert (WEB / "js" / spec).is_file(), f"{name} imports missing module {spec}"


def test_the_service_worker_precache_list_is_accurate() -> None:
    source = (WEB / "sw.js").read_text(encoding="utf-8")
    listed = [asset for asset in re.findall(r'^\s+"([^"]+)",', source, re.M) if asset != "."]
    for asset in listed:
        assert (WEB / asset).is_file(), f"service worker precaches missing file {asset}"


def test_the_service_worker_never_caches_household_state() -> None:
    source = (WEB / "sw.js").read_text(encoding="utf-8")
    assert '"/v1"' in source or "'/v1'" in source, "the /v1 API must be excluded from caching"


def test_the_manifest_points_at_icons_that_exist() -> None:
    import json

    manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert (WEB / icon["src"]).is_file(), f"missing icon {icon['src']}"
