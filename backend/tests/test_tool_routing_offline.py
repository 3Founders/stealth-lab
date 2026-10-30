"""The tool router (app/tool_routing): cards, goal tagging, quality, BM25 + embedding retrieval -- no network."""
from __future__ import annotations

import asyncio
import hashlib

import numpy as np

from app.tool_routing.catalog import ToolCard, card_from_registry, installed_cards, quality, tag_goals
from app.tool_routing.goals import TOOL_GOALS
from app.tool_routing.retrieve import BM25, best_goal, rank


class FakeEmbedder:
    """Hashed bag of words with a few synonyms folded together, so meaning-level matches can be tested offline."""
    SYN = {"renders": "browser", "page": "browser", "screenshot": "browser", "login": "browser",
           "500": "errors", "production": "errors", "crash": "errors", "exception": "errors"}

    def __init__(self, fail: bool = False):
        self.fail = fail

    def embedding_model_id(self) -> str:
        return "fake:bow"

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(256, dtype=np.float32)
        for w in str(text).lower().split():
            w = self.SYN.get(w.strip(".,?"), w.strip(".,?"))
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
        return v.tolist()

    async def embed_one(self, text, input_type="query"):
        if self.fail:
            raise RuntimeError("provider down")
        return self._vec(text)

    async def embed(self, texts, input_type="document"):
        if self.fail:
            raise RuntimeError("provider down")
        return [self._vec(t) for t in texts]


def reg(name, description, **kw):
    return card_from_registry({"name": name, "description": description, "repository": "https://github.com/x/y",
                               "remotes": ["streamable-http"], **kw})


CARDS = [
    reg("io.github.microsoft/playwright-mcp", "Drive a real browser with Playwright: open pages, click, screenshot, e2e tests"),
    reg("io.github.getsentry/sentry-mcp", "Read Sentry issues, errors and stack traces from production"),
    reg("ai.waystation/postgres", "Connect to your PostgreSQL database to query data and schemas"),
    reg("com.avalara/returns", "Access Avalara Global Returns API for tax returns filing"),
    reg("ai.smithery/cc25a-openai-api-agent-project12312312", "Look up stock prices"),
]


def run(coro):
    return asyncio.run(coro)


def test_goals_are_fifteen_with_descriptions_and_markers():
    assert len(TOOL_GOALS) == 15
    assert all(desc and markers for desc, markers in TOOL_GOALS.values())


def test_cards_are_tagged_from_their_own_words():
    assert tag_goals("Drive a browser with Playwright and take a screenshot")[0] == "browser"
    assert "database" in tag_goals("Query PostgreSQL schemas")
    assert tag_goals("nothing relevant here") == ()


def test_quality_demotes_test_and_template_servers_and_empty_cards():
    good, junk = CARDS[0], CARDS[4]
    assert quality(junk) < quality(good)
    bare = card_from_registry({"name": "x.demo/hello", "description": "hi"})
    assert quality(bare) == 0.0
    assert quality(ToolCard(id="kell:a", source="kell", name="a", title="", description="")) == 1.0


def test_bm25_prefers_the_card_with_the_rare_word():
    s = BM25([["postgres", "query"], ["query", "web"], ["web"]]).scores(["postgres", "query"])
    assert int(np.argmax(s)) == 0


def test_named_tools_are_found_by_keywords_alone():
    hits, info = run(rank("query the postgres database for users created today", CARDS, k=3))
    assert hits[0].card.name == "ai.waystation/postgres"
    assert info["semantic"] is False
    assert info["goal"] == "database"


def test_meaning_finds_the_tool_a_request_does_not_name():
    # by words alone "returns 500" matched the Avalara *returns* API (2026-10-30); the vectors fold "500"/"production"
    # into errors, as a real embedding relates them
    hits, info = run(rank("find why the API returns 500 in production", CARDS, embedder=FakeEmbedder(), k=3))
    assert info["semantic"] is True
    assert hits[0].card.name == "io.github.getsentry/sentry-mcp"


def test_installed_tools_rank_above_registry_servers_for_the_same_need():
    inst = installed_cards([{"server": "playwright", "tools": [{"name": "browser_take_screenshot", "description": "Take a screenshot of the page in a browser"}]}])
    hits, _ = run(rank("take a screenshot of the page", [*inst, *CARDS], embedder=FakeEmbedder(), k=3))
    assert hits[0].card.source == "installed"
    assert hits[0].card.id == "installed:playwright/browser_take_screenshot"


def test_a_failing_embedder_falls_back_to_keywords_and_says_so():
    hits, info = run(rank("query the postgres database", CARDS, embedder=FakeEmbedder(fail=True), k=3))
    assert hits and hits[0].card.name == "ai.waystation/postgres"
    assert info["semantic"] is False and "embedding unavailable" in info["degraded"]


def test_the_triage_goal_wins_over_the_guess_and_lifts_matching_cards():
    hits, info = run(rank("look at the page", CARDS, goal="browser", k=2))
    assert info["goal"] == "browser"
    assert hits[0].goal_match


def test_best_goal_by_words():
    assert best_goal(["stripe", "invoice"]) == "payments"
    assert best_goal(["zzz"]) is None


def test_installed_cards_ignore_malformed_entries():
    assert installed_cards([{"server": "s", "tools": [{"description": "no name"}, "x"]}, "junk", None]) == []


def test_the_reranker_reorders_the_first_stage_and_its_failure_keeps_that_order():
    async def sentry_first(query, docs):
        return [0.9 if "Sentry" in d else 0.1 for d in docs]

    async def down(query, docs):
        return None

    hits, info = run(rank("returns errors", CARDS, k=3, reranker=sentry_first))
    assert info["reranked"] is True and hits[0].card.name == "io.github.getsentry/sentry-mcp" and hits[0].rerank == 0.9
    plain, _ = run(rank("returns errors", CARDS, k=3))
    kept, info2 = run(rank("returns errors", CARDS, k=3, reranker=down))
    assert info2["reranked"] is False and [h.card.id for h in kept] == [h.card.id for h in plain]


def test_liveness_publishers_and_real_tools_shape_a_card():
    base = {"name": "io.github.getsentry/sentry-mcp", "description": "Read Sentry issues and errors from production",
            "repository": "https://github.com/getsentry/sentry-mcp", "remotes": ["streamable-http"],
            "remote_urls": [{"type": "streamable-http", "url": "https://mcp.sentry.dev/mcp"}]}
    live = card_from_registry(base, probe={"status": "live", "tool_count": 2,
                              "tools": [{"name": "search_issues", "description": "Search issues"}, {"name": "get_event", "description": "One event"}]})
    unprobed = card_from_registry(base)
    dead = card_from_registry(base, probe={"status": "dead"})
    assert live.tools == ("search_issues", "get_event") and "search issues" in live.text
    assert live.extra["verified"] is True
    assert quality(live) == 1.0 > quality(unprobed) > quality(dead)
    bulk = card_from_registry({**base, "name": "com.spam/sentry-clone", "repository": ""}, owner_count=120)
    assert quality(bulk) < quality(unprobed)
    assert card_from_registry({**base, "name": "io.github.someoneelse/x"}).extra["verified"] is False
    assert card_from_registry({**base, "name": "com.cloudflare/mcp", "website": "https://developers.cloudflare.com/x"}).extra["verified"] is True


def test_stars_and_an_official_publisher_lift_a_relevant_server_but_not_an_irrelevant_one():
    from app.tool_routing.catalog import popularity, publisher

    official = card_from_registry({"name": "io.github.microsoft/playwright-mcp", "description": "Drive a real browser with Playwright",
                                   "repository": "https://github.com/microsoft/playwright-mcp", "remotes": ["streamable-http"]},
                                  repo={"stars": 38000})
    wrapper = card_from_registry({"name": "com.paybrowser/agent", "description": "A real Chromium browser for agents, pay per action",
                                  "repository": "https://github.com/microsoft/playwright-mcp", "remotes": ["streamable-http"]},
                                 repo={"stars": 38000})
    assert popularity(official) > 0.9 and popularity(wrapper) <= 0.5     # a famous repo does not make a copycat famous
    assert popularity(card_from_registry({"name": "a/b", "description": "x"}, repo={"stars": 9000, "archived": True})) == 0.0
    assert publisher(official) == "microsoft" and publisher(reg("com.stripe/mcp", "Stripe")) == "stripe"

    async def wrapper_slightly_ahead(query, docs):
        return [0.80 if "pay per action" in d else 0.75 if "Playwright" in d else 0.01 for d in docs]

    hits, _ = run(rank("check the page in a real browser", [wrapper, official, *CARDS[1:]], k=2, reranker=wrapper_slightly_ahead))
    assert hits[0].card.id == official.id
    stripe = card_from_registry({"name": "com.stripe/mcp", "description": "Payments API", "website": "https://stripe.com"})
    other = reg("io.github.someone/payments", "Payments API")

    async def same(query, docs):
        return [0.6 for _ in docs]

    hits, _ = run(rank("add a stripe subscription checkout", [other, stripe], k=2, reranker=same))
    assert hits[0].card.id == stripe.id


def test_an_installed_tool_reaches_the_reranker_and_comes_first_when_it_is_relevant():
    many = [reg(f"io.github.p{i}/browser-{i}", f"Open a real browser page {i}, click and screenshot") for i in range(60)]
    installed = installed_cards([{"server": "playwright", "tools": [{"name": "playwright", "description": "package @playwright/mcp"}]}])

    async def knows_playwright(query, docs):
        return [0.6 if "playwright" in d.lower() else 0.7 for d in docs]

    hits, _ = run(rank("check that the login page renders in a real browser", [*many, *installed], goal="browser", k=3,
                       reranker=knows_playwright))
    assert hits[0].card.source == "installed"

    async def irrelevant(query, docs):
        return [0.01 if "playwright" in d.lower() else 0.7 for d in docs]

    hits, _ = run(rank("check that the login page renders in a real browser", [*many, *installed], goal="database", k=3,
                       reranker=irrelevant))
    assert all(h.card.source != "installed" for h in hits)


def test_the_server_builds_missing_vectors_and_resumes_a_checkpoint(tmp_path, monkeypatch):
    import numpy as np

    from app.tool_routing import vectors as V

    out = tmp_path / "registry_vectors.npz"
    monkeypatch.setattr(V, "VECTORS_FILE", out)
    monkeypatch.setattr(V, "registry_cards", lambda: tuple(CARDS))
    monkeypatch.setattr(V, "registry_vectors", type("C", (), {"cache_clear": staticmethod(lambda: None)})())
    calls = []

    class Emb(FakeEmbedder):
        def available(self):
            return True

        def embedding_model_id(self):
            return "fake@8"

        async def embed(self, texts, input_type="document"):
            calls.append(len(texts))
            return await super().embed(texts, input_type)

    ckpt = out.with_name(out.stem + ".partial.npz")
    first = run(Emb().embed([CARDS[0].text]))
    np.savez(ckpt, ids=np.array([CARDS[0].id]), vectors=np.array(first, dtype=np.float16), model=np.array("fake@8"))
    assert run(V.build_registry_vectors(batch=2, say=lambda m: None, embedder=Emb())) is True
    with np.load(out) as z:
        assert [str(i) for i in z["ids"]] == [c.id for c in CARDS] and str(z["model"]) == "fake@8"
    assert sum(calls[1:]) == len(CARDS) - 1 and not ckpt.exists()      # the checkpointed card was not embedded again
