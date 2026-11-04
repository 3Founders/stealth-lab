"""A search hit that needs a sign-in kell can do itself is offered as runnable through kell ("needs a sign-in first"), so the
agent calls use_tool and the person gets a Sign in button, instead of being told to open the server's connector URL
(Preuve AI in a room, 2026-11-04). Without kell's sign-in (TOOL_GATEWAY_OAUTH off) it stays "cannot run through kell"."""
from app.mcp_server import server as srv
from app.tool_routing.catalog import ToolCard


def card(name, url, probe="live", stars=0, verified=True):
    return ToolCard(id="registry:" + name, source="registry", name=name, title="", description="", server=name,
                    extra={"connector_url": url, "probe": probe, "verified": verified, "owner_count": 1, "stars": stars})


def test_a_sign_in_server_is_offered_through_kell_when_kell_can_sign_in(monkeypatch):
    monkeypatch.setenv("TOOL_GATEWAY_OAUTH", "on")
    preuve = card("ai.preuve/mcp", "https://mcp.preuve.ai/mcp", probe="auth")
    assert srv._via_kell(preuve) == "signin"
    assert srv._VIA_KELL_NOTE["signin"].startswith("needs a sign-in first")


def test_without_kells_sign_in_it_is_not_offered(monkeypatch):
    monkeypatch.setenv("TOOL_GATEWAY_OAUTH", "off")
    assert srv._via_kell(card("ai.preuve/mcp", "https://mcp.preuve.ai/mcp", probe="auth")) == "no"


def test_a_server_that_needs_no_sign_in_is_ready_and_an_unverified_one_is_not(monkeypatch):
    monkeypatch.setenv("TOOL_GATEWAY_OAUTH", "on")
    assert srv._via_kell(card("com.pdfoven/pdfoven", "https://pdfoven.com/mcp")) == "ready"
    assert srv._via_kell(card("com.pdfoven/pdfoven", "https://pdfoven.com/mcp", probe="auth", verified=False)) == "no"
    assert srv._via_kell(card("com.pdfoven/pdfoven", "https://pdfoven.com/mcp", probe="down")) == "no"
