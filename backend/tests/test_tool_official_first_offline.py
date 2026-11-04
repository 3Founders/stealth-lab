"""A product's own server is preferred over someone else's server for it (Exa through Pollinations was offered while Exa's own, which needs
no sign-in, counted as unverified, 2026-11-04), and of two that fit as well, the one that needs no sign-in comes first."""
from app.mcp_server import server as srv
from app.tool_routing.catalog import ToolCard, official_for, product, verified_publisher
from app.tool_routing.retrieve import Hit


def card(name, url, probe="live", verified=True, stars=5000):
    return ToolCard(id="registry:" + name, source="registry", name=name, title="", description="web search", server=name,
                    extra={"connector_url": url, "probe": probe, "verified": verified, "owner_count": 1, "stars": stars})


def hit(c, score=1.0):
    return Hit(card=c, score=score, bm25_rank=1, vector_rank=1, goal_match=True)


def test_a_company_server_on_its_own_domain_is_verified():
    assert verified_publisher("ai.exa/exa", "https://github.com/exa-labs/exa-mcp-server", "", "https://mcp.exa.ai/mcp")
    assert not verified_publisher("ai.exa/exa", "https://github.com/exa-labs/exa-mcp-server", "", "https://exa.example.com/mcp")


def test_product_names_and_the_official_server():
    exa, copy = card("ai.exa/exa", "https://mcp.exa.ai/mcp"), card("io.github.pollinations/exa", "https://gen.pollinations.ai/mcp/exa", probe="auth")
    assert product(copy) == product(exa) == "exa" and product(card("io.github.x/exa-mcp-server", "")) == "exa"
    assert official_for(copy, [exa]) is exa and official_for(exa, [copy]) is None
    assert official_for(copy, [card("ai.exa/exa", "https://mcp.exa.ai/mcp", verified=False)]) is None


def test_the_official_server_takes_the_copys_place_and_the_copy_goes_last(monkeypatch):
    monkeypatch.setenv("TOOL_GATEWAY_OAUTH", "on")
    exa, copy, other = card("ai.exa/exa", "https://mcp.exa.ai/mcp"), card("io.github.pollinations/exa", "https://gen.pollinations.ai/mcp/exa", probe="auth"), \
        card("io.github.firecrawl/firecrawl-mcp-server", "https://mcp.firecrawl.dev/v2/mcp")
    hits, copies = srv._official_first([hit(copy), hit(other)], [exa])
    assert [h.card.name for h in hits] == ["ai.exa/exa", "io.github.pollinations/exa", "io.github.firecrawl/firecrawl-mcp-server"]
    assert copies == {"io.github.pollinations/exa": "ai.exa/exa"}


def test_of_two_that_fit_as_well_the_one_without_a_sign_in_comes_first(monkeypatch):
    monkeypatch.setenv("TOOL_GATEWAY_OAUTH", "on")
    needs, free = card("ai.hyperresearch/hyperresearch", "https://mcp.hyperresearch.ai/mcp", probe="auth"), card("ai.exa/exa", "https://mcp.exa.ai/mcp")
    assert srv._via_kell(free) == "ready" and srv._via_kell(needs) == "signin"
    order = sorted([hit(needs), hit(free)], key=lambda h: {"ready": 0, "signin": 1}.get(srv._via_kell(h.card), 2))
    assert [h.card.name for h in order] == ["ai.exa/exa", "ai.hyperresearch/hyperresearch"]
