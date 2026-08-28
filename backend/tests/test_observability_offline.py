"""Observability: off by default, and never ships trace content off-box.

The scrubbing half is the load-bearing part. trace_redaction.py declares
NEVER_SEND_EXTERNALLY_BY_DEFAULT = True precisely because a caller on an
external path must handle it explicitly -- and Sentry is the most
dangerous external path in this repo, since an exception's local variables
and request body routinely carry the exact tool_input/tool_output payloads
the collector spent effort redacting.

send_default_pii=False does NOT cover that: it governs headers, cookies,
and user identity, not an application dict holding a transcript. These
tests pin the two mechanisms that do.

Fully offline: no DSN, no network, no sentry_sdk import required.
"""
from __future__ import annotations

import pytest

from app import observability as obs


# ------------------------------------------------------- off by default

def test_init_is_a_noop_without_a_dsn(monkeypatch):
    """A clean clone, local work, and CI must be unaffected."""
    monkeypatch.setattr(obs.get_settings(), "sentry_dsn", None, raising=False)
    assert obs.init("api") is False
    assert obs.is_enabled() is False


def test_component_names_cover_all_three_surfaces():
    """Three entry points, not one -- instrumenting only the FastAPI app
    would leave the MCP tools and the headless worker dark."""
    import app.main  # noqa: F401
    import app.mcp_server.server  # noqa: F401
    for path, tag in [
        ("app/main.py", '"api"'),
        ("app/mcp_server/server.py", '"mcp"'),
        ("scripts/run_ingestion.py", '"worker"'),
    ]:
        src = open(path, encoding="utf-8").read()
        assert f"observability.init({tag})" in src, f"{path} not instrumented"


# ------------------------------------------------------------ scrubbing

def test_known_token_in_a_nested_event_is_redacted():
    event = {"extra": {"ctx": {"cmd": "export KEY=AKIAABCDEFGHIJKLMNOP"}}}
    out = obs._scrub(event)
    assert "AKIAABCDEFGHIJKLMNOP" not in str(out)


def test_anthropic_key_in_a_stack_local_is_redacted():
    event = {"exception": {"values": [{"stacktrace": {"frames": [
        {"vars": {"auth": "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"}}
    ]}}]}}
    out = obs._scrub(event)
    assert "sk-ant-api03" not in str(out)


@pytest.mark.parametrize("key", sorted(obs.DROP_KEYS_ENTIRELY))
def test_high_risk_keys_are_dropped_wholesale_at_any_depth(key):
    """A partial pattern match on a 32KB tool payload is not worth the
    residual risk, so these are dropped rather than scrubbed."""
    secret = "some real transcript content that must never leave the box"
    event = {"extra": {"deeply": {"nested": {key: secret}}}}
    out = obs._scrub(event)
    assert secret not in str(out)
    assert obs._REDACTED in str(out)


def test_drop_applies_inside_lists_too():
    event = {"breadcrumbs": [{"data": {"tool_output": "raw transcript"}}]}
    out = obs._scrub(event)
    assert "raw transcript" not in str(out)


def test_ordinary_diagnostic_content_survives():
    """Scrubbing must not destroy the signal it exists to deliver."""
    event = {
        "exception": {"values": [{"type": "ValueError", "value": "bad input"}]},
        "tags": {"component": "worker"},
    }
    out = obs._scrub(event)
    assert out["exception"]["values"][0]["type"] == "ValueError"
    assert out["tags"]["component"] == "worker"


def test_scrubbing_failure_drops_the_event_rather_than_sending_it(monkeypatch):
    """Fail closed. Sending an unscrubbed event is the one outcome that is
    worse than losing it."""
    def boom(*a, **k):
        raise RuntimeError("scrubber exploded")

    monkeypatch.setattr(obs, "redact_value", boom)
    assert obs._scrub({"extra": {"x": "y"}}) is None


def test_scrub_returns_an_event_on_the_happy_path():
    """The inverse of the above: normal events must NOT be dropped, or the
    module trades a leak risk for total blindness."""
    assert obs._scrub({"tags": {"component": "api"}}) is not None


def test_never_send_externally_constant_is_still_true():
    """If trace_redaction ever lowers that posture, this module's
    assumption needs re-reviewing -- so the test fails loudly rather than
    the assertion firing only at runtime with a DSN configured."""
    from app.services.trace_redaction import NEVER_SEND_EXTERNALLY_BY_DEFAULT
    assert NEVER_SEND_EXTERNALLY_BY_DEFAULT is True
