"""Tool servers are checked again daily and learned from real calls (app/tool_routing/liveness.py): the newest check overrides the
snapshot's probe, a dead server is not offered even with a sign-in, a server in DOWN counts as failing until a call succeeds."""
import pytest

from app.tool_routing import gateway as g
from app.tool_routing import liveness as L
from app.tool_routing.catalog import ToolCard


def card(name="ai.exa/exa", probe="live"):
    return ToolCard(id="registry:" + name, source="registry", name=name, title="", description="web search", server=name,
                    extra={"connector_url": "https://mcp.exa.ai/mcp", "probe": probe, "verified": True, "owner_count": 1, "stars": 5000})


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(L, "CURRENT", {})
    monkeypatch.setattr(L, "DOWN", set())
    monkeypatch.setenv("TOOL_GATEWAY_OAUTH", "on")


def test_the_newest_check_overrides_the_snapshot():
    L.CURRENT["ai.exa/exa"] = "dead"
    c = L.apply(card())
    assert c.extra["probe"] == "dead" and not g.gateway_allowed(c) and not g.signin_allowed(c)
    L.CURRENT["ai.exa/exa"] = "auth"
    c = L.apply(card())
    assert not g.gateway_allowed(c) and g.signin_allowed(c)


def test_a_server_whose_calls_all_failed_is_failing_until_one_succeeds():
    L.DOWN.add("ai.exa/exa")
    assert g.failing("ai.exa/exa")
    g.note_ok("ai.exa/exa")
    assert not g.failing("ai.exa/exa")


class Conn:
    def __init__(self, live, down):
        self.live, self.down = live, down

    async def fetch(self, sql, *a):
        return self.down if "tool_gateway_calls" in sql else self.live


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *a):
                return False
        return Ctx()


@pytest.mark.asyncio
async def test_refresh_reads_both_at_most_every_few_minutes(monkeypatch):
    monkeypatch.setattr(L, "_LOADED_AT", -1e9)
    pool = Pool(Conn([{"server": "ai.exa/exa", "status": "auth"}], [{"server": "x.y/z"}]))
    await L.refresh(pool, now=1000.0)
    assert L.CURRENT == {"ai.exa/exa": "auth"} and L.DOWN == {"x.y/z"}
    pool.conn.live = []
    await L.refresh(pool, now=1000.0 + L.RELOAD_S - 1)            # too soon: unchanged
    assert L.CURRENT == {"ai.exa/exa": "auth"}
