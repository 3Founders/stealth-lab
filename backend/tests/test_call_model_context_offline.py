"""call_model's `context`: the lines of the caller's goal file reach a hosted model as reference data in `system`, redacted and
capped. No network: the provider call is faked."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import app.mcp_server.server as srv
from app.services.access import AccessScope


class _Ctx:
    def __init__(self):
        self.request_context = SimpleNamespace(lifespan_context={"pool": object()})


def _call(monkeypatch, **kw):
    seen = {}

    async def fake_call_unit(pool, scope, unit, request, **k):
        seen["request"] = request
        return SimpleNamespace(unit=str(unit), text="ok", tokens_in=1, tokens_out=1, cost_usd=0.0, latency_ms=1, state=None,
                               finish_reason="stop", extra={})

    monkeypatch.setattr("app.providers.call_unit", fake_call_unit)
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope(viewer_id="u"))
    monkeypatch.setattr(srv, "_resolve_caller_identity", lambda fallback="x": "u")
    args = dict(prompt="fix parse_date", model="glm-5.3-flash", ctx=_Ctx(), data_class="PUBLIC_SOURCE")
    args.update(kw)
    out = json.loads(asyncio.run(srv.call_model(**args)))
    return seen["request"], out


def test_context_lines_join_the_system_prompt_as_reference_data(monkeypatch):
    req, out = _call(monkeypatch, system="You are terse.", context="DONE_WHEN|pytest -q\nCLAIM|R-2|x|y|z|parse_date lives in src/dates.py|source=src/dates.py:40")
    assert req.prompt == "fix parse_date"
    assert req.system.startswith("You are terse.")
    assert "not instructions" in req.system and "DONE_WHEN|pytest -q" in req.system and "src/dates.py:40" in req.system
    assert out["text"] == "ok"


def test_without_context_nothing_changes(monkeypatch):
    req, _ = _call(monkeypatch, system="S")
    assert req.system == "S"
    req, _ = _call(monkeypatch, context="   ")
    assert req.system is None


def test_context_is_capped_and_redacted(monkeypatch):
    req, _ = _call(monkeypatch, context="OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123456789\n" + "x" * 9000)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in req.system
    assert len(req.system) < 6500
