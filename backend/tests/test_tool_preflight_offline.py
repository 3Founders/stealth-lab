"""A server the registry calls live that now asks for a sign-in is checked at search time and demoted before the agent tries it
(app/mcp_server/server.py _preflight_tools). No network: the card lookup and the server call are faked."""
from __future__ import annotations

import asyncio

from app.mcp_server import server as sv
from app.tool_routing import gateway as g
from app.tool_routing.catalog import ToolCard


def _entry(name, via=True):
    return {"name": name, "server": name, "source": "registry", "via_kell": via, "call": "use_tool(...)", "available": "ready"}


def test_a_server_that_asks_for_a_sign_in_is_demoted_and_moved_behind_the_ones_that_run(monkeypatch):
    urls = {"a/needs-login": "https://a.example/mcp", "b/open": "https://b.example/mcp"}

    async def card(server):
        return ToolCard(id="r:" + server, source="registry", name=server, title="", description="", server=server,
                        extra={"connector_url": urls[server]})

    async def list_tools(url):
        if url.startswith("https://a."):
            raise g.GatewayRefused("the server needs a sign-in, which the gateway does not do yet")
        return [{"name": "t"}]

    monkeypatch.setattr(sv, "_gateway_card", card)
    monkeypatch.setattr(g, "list_tools", list_tools)
    sv._PREFLIGHT.clear()
    g.NEEDS_SIGNIN.clear()
    out = asyncio.run(sv._preflight_tools([_entry("a/needs-login"), _entry("b/open")]))
    assert [t["name"] for t in out] == ["b/open", "a/needs-login"]
    assert out[0]["via_kell"] is True and out[1]["via_kell"] is False
    assert "sign-in" in out[1]["available"] and "call" not in out[1]


def test_a_slow_or_failing_server_is_left_as_it_was(monkeypatch):
    async def card(server):
        return ToolCard(id="r:" + server, source="registry", name=server, title="", description="", server=server,
                        extra={"connector_url": "https://slow.example/mcp"})

    async def list_tools(url):
        raise TimeoutError("slow")

    monkeypatch.setattr(sv, "_gateway_card", card)
    monkeypatch.setattr(g, "list_tools", list_tools)
    sv._PREFLIGHT.clear()
    g.NEEDS_SIGNIN.clear()
    out = asyncio.run(sv._preflight_tools([_entry("s/slow")]))
    assert out[0]["via_kell"] is True


def test_an_answer_is_remembered(monkeypatch):
    calls = []

    async def card(server):
        return ToolCard(id="r:" + server, source="registry", name=server, title="", description="", server=server,
                        extra={"connector_url": "https://ok.example/mcp"})

    async def list_tools(url):
        calls.append(url)
        return []

    monkeypatch.setattr(sv, "_gateway_card", card)
    monkeypatch.setattr(g, "list_tools", list_tools)
    sv._PREFLIGHT.clear()
    g.NEEDS_SIGNIN.clear()
    asyncio.run(sv._preflight_tools([_entry("o/ok")]))
    asyncio.run(sv._preflight_tools([_entry("o/ok")]))
    assert len(calls) == 1
