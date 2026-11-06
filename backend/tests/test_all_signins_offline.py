"""A tool search that finds several tools needing a sign-in gives the person one button for EACH, at once, named, and a fresh link on demand."""
import asyncio
import json

from app.services import room_agent as ra
from app.tool_routing import oauth_client


def run(c):
    return asyncio.run(c)


SEARCH = json.dumps({"external": {"tools": [
    {"name": "com.figma/figma-context", "server": "com.figma/figma-context", "via_kell": True, "available": "needs a sign-in first: use_tool returns a link the person opens once"},
    {"name": "ai.anima/anima", "server": "ai.anima/anima", "via_kell": True, "available": "needs a sign-in first: use_tool returns a link the person opens once"},
    {"name": "dev.ready/tool", "server": "dev.ready/tool", "via_kell": True},
    {"name": "x/blocked", "server": "x/blocked", "via_kell": False, "available": "needs a sign-in first"},
]}})
CALL = {"function": {"name": "discover_tools", "arguments": json.dumps({"need": "design to code"})}}


def test_the_search_result_names_every_tool_that_needs_a_sign_in_best_fit_first():
    assert ra.signin_servers(SEARCH) == ["com.figma/figma-context", "ai.anima/anima"]
    assert ra.signin_servers("not json") == [] and ra.signin_servers(json.dumps({"tools": []})) == []


def test_every_one_gets_its_own_link_in_one_go_and_a_failure_hides_nobody():
    asked = []

    async def tools_of(server):
        asked.append(server)
        if server.startswith("ai.anima"):
            raise RuntimeError("boom")
        return json.dumps({"sign_in_required": True, "server": server, "link": f"https://sign.in/{server}"})

    got = run(ra.all_signins(SEARCH, CALL, tools_of, []))
    assert asked == ["com.figma/figma-context", "ai.anima/anima"]
    assert got == [{"server": "com.figma/figma-context", "link": "https://sign.in/com.figma/figma-context"}]


def test_a_tool_already_on_the_card_is_not_asked_again_and_a_server_listing_is_not_a_search():
    async def tools_of(server):
        return json.dumps({"sign_in_required": True, "server": server, "link": "https://l"})

    tried = [{"server": "com.figma/figma-context", "link": "https://old"}]
    got = run(ra.all_signins(SEARCH, CALL, tools_of, tried))
    assert [t["server"] for t in got] == ["ai.anima/anima"]
    listing = {"function": {"name": "discover_tools", "arguments": json.dumps({"server": "com.figma/figma-context"})}}
    assert run(ra.all_signins(SEARCH, listing, tools_of, [])) == []


def test_the_tools_own_sign_in_page_is_told_which_tool_it_is_for():
    assert oauth_client.client_name_for("com.figma/figma-context") == "kell - figma-context"
    assert oauth_client.client_name_for("") == "kell"


def test_the_operator_allowlist_admits_a_remote_verified_server_with_few_stars_but_nothing_else(monkeypatch):
    from app.tool_routing import gateway as gw
    from app.tool_routing.catalog import ToolCard

    def card(**extra):
        base = {"verified": True, "probe": "auth", "stars": 6, "owner_count": 1, "connector_url": "https://public-api.animaapp.com/v1/mcp"}
        return ToolCard(id="registry:io.github.AnimaApp/anima", source="registry", name="io.github.AnimaApp/anima", title="Anima", description="d", extra={**base, **extra})

    monkeypatch.setattr(gw.oauth, "enabled", lambda: True)
    monkeypatch.delenv("TOOL_GATEWAY_ALLOW", raising=False)
    assert not gw.signin_allowed(card())                                     # 6 stars, a GitHub namespace: left out
    monkeypatch.setenv("TOOL_GATEWAY_ALLOW", "io.github.AnimaApp/anima")
    assert gw.signin_allowed(card())
    assert not gw.signin_allowed(card(connector_url=""))                     # a local program stays out, listed or not
    assert not gw.signin_allowed(card(verified=False))
    assert not gw.signin_allowed(card(archived=True))
