"""A company's own verified domain is enough for the gateway (no GitHub stars needed); a GitHub namespace still needs stars."""
from app.tool_routing.catalog import ToolCard
from app.tool_routing.gateway import gateway_allowed, own_domain


def card(name, url, stars=0, probe="live"):
    return ToolCard(id="registry:" + name, source="registry", name=name, title="", description="", server=name,
                    extra={"connector_url": url, "probe": probe, "verified": True, "owner_count": 1, "stars": stars})


def test_own_domain_is_allowed_without_stars():
    c = card("com.pdfoven/pdfoven", "https://pdfoven.com/mcp")
    assert own_domain(c) and gateway_allowed(c)
    assert gateway_allowed(card("com.ainetcafe/x", "https://api.ainetcafe.com/mcp"))


def test_a_different_host_or_a_github_namespace_needs_stars():
    assert not gateway_allowed(card("com.pdfoven/pdfoven", "https://evil.example/mcp"))
    assert not gateway_allowed(card("io.github.someone/tool", "https://someone.dev/mcp", stars=3))
    assert gateway_allowed(card("io.github.someone/tool", "https://someone.dev/mcp", stars=40))


def test_a_server_that_needs_sign_in_is_never_allowed():
    assert not gateway_allowed(card("com.pdfoven/pdfoven", "https://pdfoven.com/mcp", probe="auth"))
