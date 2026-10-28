"""
Offline tests for `submit`: a tool, a better prompt or a tip suggested while working a goal is stored as a private
candidate claim linked to the goal. No DB: the rate limiter, content screen, goal lookup, source registry and claim store
are faked.
"""
from __future__ import annotations

import asyncio
import json

import app.mcp_server.server as srv
from app.services.access import AccessScope


def _run(coro):
    return asyncio.run(coro)


class _RC:
    def __init__(self, pool=None):
        self.lifespan_context = {"pool": pool}


class _Ctx:
    def __init__(self, pool=None):
        self.request_context = _RC(pool)


class _Verdict:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def as_dict(self):
        return {"allowed": self.allowed, "reason": "link"}


def _wire(monkeypatch, *, viewer="user-1", allowed=True, limited=False, goal_result=None):
    cap = {"limiter": [], "screened": None}
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope(viewer_id=viewer))

    class FakeLimiter:
        def __init__(self, pool, limits):
            cap["limits"] = limits

        async def check_and_record(self, key, path):
            from app.services.governance import RateLimitExceeded
            cap["limiter"].append((key, path))
            if limited:
                raise RateLimitExceeded(key, path, 30) if False else _exc()

    def _exc():
        from app.services.governance import RateLimitExceeded
        try:
            return RateLimitExceeded("k", "p", 30)
        except TypeError:
            e = RateLimitExceeded.__new__(RateLimitExceeded)
            e.retry_after_seconds = 30
            return e

    async def fake_screen(body):
        cap["screened"] = body
        return _Verdict(allowed)

    async def fake_goal(pool, **kw):
        cap["goal_call"] = kw
        return goal_result or {"goal_id": "G-new", "canonical_name": kw["goal"], "created": True, "matched": "created"}

    async def fake_source(pool, **kw):
        cap["source"] = kw
        return {"id": "S-1"}

    async def fake_capture(pool, **kw):
        cap["claim"] = kw
        return "C-7"

    monkeypatch.setattr("app.services.governance.RateLimiter", FakeLimiter)
    monkeypatch.setattr("app.economy.content_screen.screen_contribution", fake_screen)
    monkeypatch.setattr(srv, "_find_or_create_submitted_goal", fake_goal)
    monkeypatch.setattr("app.services.sources.register_source", fake_source)
    monkeypatch.setattr("app.services.claims.capture_claim", fake_capture)
    return cap


def _submit(**kw):
    args = dict(kind="prompt", title="Ask for the diff first", content="List the files you will change, then change them.",
                why="The model stopped editing unrelated files; the check passed.", ctx=_Ctx(), goal_id="G-1")
    args.update(kw)
    return _run(srv.submit(**args))


def test_submit_is_on_the_v1_surface_and_scoped_as_a_write():
    assert "submit" in srv.V1_TOOLS
    assert "submit" in {t.name for t in srv.server._tool_manager.list_tools()}
    assert srv._TOOL_SCOPES.get("submit", srv._WRITE) == srv._WRITE
    assert srv._V1_ANNOTATIONS["submit"]["read_only_hint"] is False


def test_a_suggested_prompt_is_stored_private_and_linked_to_the_goal(monkeypatch):
    cap = _wire(monkeypatch)
    out = json.loads(_submit(suggested_by="user", step_order=2, repo="github.com/o/r"))
    assert out["outcome"] == "stored" and out["claim_id"] == "C-7" and out["visibility"] == "private"
    claim = cap["claim"]
    assert claim["visibility"] == "private" and claim["owner_id"] == "user-1"
    assert claim["subject"] == "goal:G-1#step2" and claim["predicate"] == "suggested_prompt"
    assert claim["properties"]["suggested_by"] == "user" and claim["properties"]["share"] is False
    assert claim["properties"]["content"].startswith("List the files")
    assert claim["scope_type"] == "repository" and claim["scope_entity_id"] == "github.com/o/r"
    assert cap["source"]["visibility"] == "private"
    assert [p for _, p in cap["limiter"]] == ["mcp/submit", "mcp/submit/day", "mcp/submit/all"]


def test_a_tool_suggestion_names_its_kind(monkeypatch):
    cap = _wire(monkeypatch)
    _submit(kind="tool", title="ripgrep", content="rg -n PATTERN src/", why="found the caller in one command")
    assert cap["claim"]["predicate"] == "suggested_tool" and cap["claim"]["properties"]["submit_kind"] == "tool"


def test_a_goal_in_words_is_looked_up_before_it_is_created(monkeypatch):
    cap = _wire(monkeypatch)
    out = json.loads(_submit(goal_id=None, goal="Export a docx from node", goal_objective="a docx file opens in Word"))
    assert cap["goal_call"]["goal"] == "Export a docx from node"
    assert out["goal"]["goal_id"] == "G-new" and out["goal"]["created"] is True
    assert cap["claim"]["subject"] == "goal:G-new"


def test_an_ambiguous_goal_writes_nothing(monkeypatch):
    cap = _wire(monkeypatch, goal_result={"outcome": "goal_ambiguous", "candidates": [{"goal_id": "G-2"}]})
    out = json.loads(_submit(goal_id=None, goal="Export a docx", goal_objective="opens"))
    assert out["outcome"] == "goal_ambiguous" and "claim" not in cap


def test_the_screen_rejects_and_nothing_is_stored(monkeypatch):
    cap = _wire(monkeypatch, allowed=False)
    out = json.loads(_submit(content="see https://evil.example"))
    assert out["outcome"] == "rejected_by_screen" and "claim" not in cap


def test_secrets_are_redacted_before_storage(monkeypatch):
    cap = _wire(monkeypatch)
    out = json.loads(_submit(kind="tip", content="export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123456789 first"))
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in cap["claim"]["statement"]
    assert out["redacted"] is True


def test_refusals(monkeypatch):
    _wire(monkeypatch)
    assert _submit(kind="vibes").startswith("REFUSED:")
    assert json.loads(_submit(kind="script", title="fix.py", content="print(1)", why="runs"))["kind"] == "script"
    assert _submit(title="  ").startswith("REFUSED:")
    assert _submit(suggested_by="robot").startswith("REFUSED:")
    assert _submit(goal_id=None).startswith("REFUSED:")                       # neither goal_id nor goal
    assert _submit(goal="x", goal_objective="y").startswith("REFUSED:")       # both
    assert _submit(goal_id=None, goal="a goal in words").startswith("REFUSED:")  # no objective
    assert _submit(content="x" * 4001).startswith("REFUSED:")


def test_it_needs_a_real_user_identity(monkeypatch):
    cap = _wire(monkeypatch, viewer=None)
    out = _submit()
    assert out.startswith("REFUSED:") and "sign in" in out and "claim" not in cap
