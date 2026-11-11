"""POST /v1/tools/signin/status: a closed window is not a sign-in. The route reads what actually finished -- a usable OAuth
token (refreshed when due) or the recorded consent grant -- without calling the tool server. No database: the two lookups
are replaced, so the test is about which answer the route trusts."""
from __future__ import annotations

import json

import pytest
from starlette.requests import Request

import app.tool_routing.gateway as gateway
import app.tool_routing.oauth_client as oauth
from app.mcp_server import room_agent_routes


class Server:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods, include_in_schema=False):
        def deco(fn):
            self.routes[path] = fn
            return fn

        return deco


class Gate:
    async def subject(self, request):
        return "person-1", None


def _route():
    server = Server()
    room_agent_routes.register(server, Gate(), lambda: object(), model_call=lambda *a: None,
                               find_tools=lambda *a: None, use_tool=lambda *a: None)
    return server.routes["/v1/tools/signin/status"]


def _request(body):
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/v1/tools/signin/status",
                    "headers": [(b"authorization", b"Bearer t")], "path_params": {}, "client": ("127.0.0.1", 1),
                    "query_string": b"", "server": ("t", 80)}, receive)


@pytest.mark.asyncio
async def test_an_oauth_link_is_not_finished_without_a_usable_token(monkeypatch):
    async def no_token(*_a, **_k):
        return None

    monkeypatch.setattr(oauth, "token", no_token)
    r = await _route()(_request({"server": "ai.exa/exa", "link": "https://provider.example/authorize?redirect_uri=https://kell.example/tools/connect/callback"}))
    assert r.status_code == 200 and json.loads(r.body)["signed_in"] is False


@pytest.mark.asyncio
async def test_an_oauth_link_is_finished_when_the_token_is_usable(monkeypatch):
    async def a_token(*_a, **_k):
        return "access-token"

    monkeypatch.setattr(oauth, "token", a_token)
    r = await _route()(_request({"server": "ai.exa/exa", "link": "https://provider.example/authorize"}))
    assert json.loads(r.body)["signed_in"] is True


@pytest.mark.asyncio
async def test_a_consent_link_is_finished_only_with_the_grant(monkeypatch):
    async def no_grant(*_a, **_k):
        return False

    monkeypatch.setattr(gateway, "has_grant", no_grant)
    r = await _route()(_request({"server": "ai.exa/exa", "link": "https://kell.example/tools/consent/abc"}))
    assert json.loads(r.body)["signed_in"] is False

    async def granted(*_a, **_k):
        return True

    monkeypatch.setattr(gateway, "has_grant", granted)
    r = await _route()(_request({"server": "ai.exa/exa", "link": "https://kell.example/tools/consent/abc"}))
    assert json.loads(r.body)["signed_in"] is True
