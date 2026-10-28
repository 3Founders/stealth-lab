"""
Offline tests for POST /v1/contributions: a PERSON adding a tool, script, prompt or tip from the website. The route and the MCP
`submit` tool share app/services/contributions.py, so the same rules apply. No DB, no network: the gate, rate limiter, content
screen, goal lookup, source registry and claim store are faked.
"""
from __future__ import annotations

import asyncio
import json

from starlette.requests import Request

from app.mcp_server import contribution_routes as cr


class _FakeServer:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods, include_in_schema=False):
        def deco(fn):
            self.routes[path] = fn
            return fn
        return deco


class _Gate:
    def __init__(self, subject="sub-1", refusal=None):
        self._r = (subject, refusal)

    async def subject(self, request):
        return self._r if self._r[1] is None else (None, self._r[1])


class _Verdict:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def as_dict(self):
        return {"allowed": self.allowed}


def _request(body, method="POST", origin="https://www.kell.org.in"):
    raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({"type": "http", "method": method, "path": "/v1/contributions", "query_string": b"",
                    "headers": [(b"origin", origin.encode()), (b"content-type", b"application/json")]}, receive)


def _setup(monkeypatch, *, gate=None, pool=object(), allowed=True, goal_result=None):
    cap = {}
    server = _FakeServer()

    async def goal(pool_, **kw):
        cap["goal_call"] = kw
        return goal_result or {"goal_id": "G-9", "canonical_name": kw["goal"], "created": True}

    async def proc(*a, **k):
        raise AssertionError("no procedure expected")

    cr.register(server, gate or _Gate(), lambda: pool, find_or_create_goal=goal, resolve_procedure=proc)

    class FakeLimiter:
        def __init__(self, pool_, limits):
            pass

        async def check_and_record(self, key, path):
            cap.setdefault("limiter", []).append(key)

    async def screen(body):
        cap["screened"] = body
        return _Verdict(allowed)

    async def source(pool_, **kw):
        cap["source"] = kw
        return {"id": "S-1"}

    async def capture(pool_, **kw):
        cap["claim"] = kw
        return "C-3"

    monkeypatch.setattr("app.services.governance.RateLimiter", FakeLimiter)
    monkeypatch.setattr("app.economy.content_screen.screen_contribution", screen)
    monkeypatch.setattr("app.services.sources.register_source", source)
    monkeypatch.setattr("app.services.claims.capture_claim", capture)
    return server.routes["/v1/contributions"], cap


BODY = {"kind": "script", "title": "bulk rename", "content": "for f in *.txt; do mv \"$f\" \"${f%.txt}.md\"; done",
        "why": "renamed 40 files in one pass", "goal_id": "G-1"}


def _call(handler, body):
    resp = asyncio.run(handler(_request(body)))
    return resp.status_code, json.loads(resp.body), resp.headers


def test_a_person_adds_a_script_and_it_is_stored_private_as_theirs(monkeypatch):
    handler, cap = _setup(monkeypatch)
    status, out, headers = _call(handler, BODY)
    assert status == 201 and out["outcome"] == "stored" and out["claim_id"] == "C-3"
    claim = cap["claim"]
    assert claim["owner_id"] == "sub-1" and claim["visibility"] == "private" and claim["created_by"] == "website:sub-1"
    assert claim["predicate"] == "suggested_script" and claim["properties"]["suggested_by"] == "user"
    assert headers["access-control-allow-origin"] == "https://www.kell.org.in"


def test_a_person_cannot_claim_the_agent_made_it(monkeypatch):
    handler, cap = _setup(monkeypatch)
    _call(handler, {**BODY, "suggested_by": "agent"})
    assert cap["claim"]["properties"]["suggested_by"] == "user"


def test_a_goal_in_words_is_found_or_created(monkeypatch):
    handler, cap = _setup(monkeypatch)
    body = {k: v for k, v in BODY.items() if k != "goal_id"}
    status, out, _ = _call(handler, {**body, "goal": "Rename many files", "goal_objective": "all files have the new suffix"})
    assert status == 201 and out["goal"]["goal_id"] == "G-9" and cap["goal_call"]["goal"] == "Rename many files"


def test_the_screen_rejecting_it_is_a_200_with_nothing_stored(monkeypatch):
    handler, cap = _setup(monkeypatch, allowed=False)
    status, out, _ = _call(handler, BODY)
    assert status == 200 and out["outcome"] == "rejected_by_screen" and "claim" not in cap


def test_an_ambiguous_goal_is_a_200_with_candidates(monkeypatch):
    handler, cap = _setup(monkeypatch, goal_result={"outcome": "goal_ambiguous", "candidates": [{"goal_id": "G-2"}]})
    body = {k: v for k, v in BODY.items() if k != "goal_id"}
    status, out, _ = _call(handler, {**body, "goal": "Rename", "goal_objective": "done"})
    assert status == 200 and out["outcome"] == "goal_ambiguous" and "claim" not in cap


def test_bad_fields_are_400_with_a_plain_sentence(monkeypatch):
    handler, _ = _setup(monkeypatch)
    status, out, _ = _call(handler, {**BODY, "kind": "vibes"})
    assert status == 400 and "kind must be one of" in out["error"]
    assert _call(handler, {**BODY, "title": ""})[0] == 400
    assert _call(handler, {**BODY, "step_order": "two"})[0] == 400
    assert _call(handler, [1, 2])[0] == 400


def test_not_json_is_400(monkeypatch):
    handler, _ = _setup(monkeypatch)
    resp = asyncio.run(handler(_request(b"not json")))
    assert resp.status_code == 400


def test_the_gate_decides_who_may_call(monkeypatch):
    handler, cap = _setup(monkeypatch, gate=_Gate(refusal=("sign in first", 401)))
    status, out, _ = _call(handler, BODY)
    assert status == 401 and out == {"error": "sign in first"} and "claim" not in cap


def test_a_starting_server_is_503(monkeypatch):
    handler, _ = _setup(monkeypatch, pool=None)
    assert _call(handler, BODY)[0] == 503


def test_preflight_answers_for_an_allowed_origin_only(monkeypatch):
    handler, _ = _setup(monkeypatch)
    ok = asyncio.run(handler(_request({}, method="OPTIONS")))
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == "https://www.kell.org.in"
    bad = asyncio.run(handler(_request({}, method="OPTIONS", origin="https://evil.example")))
    assert "access-control-allow-origin" not in bad.headers
