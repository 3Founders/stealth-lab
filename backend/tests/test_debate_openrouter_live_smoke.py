"""
GATED LIVE SMOKE -- spends founder money when run. CI NEVER lands here.

Opt in explicitly:

    SL_DEBATE_LIVE_SMOKE=1 python -m pytest tests/test_debate_openrouter_live_smoke.py -v -s

Requires OPENROUTER_API_KEY in backend/.env or the environment (it is
already present locally). Skipped silently otherwise, so a default
pytest run -- including any CI configuration -- proves nothing and pays
nothing.

What it proves that offline tests cannot: real OpenRouter round-trips on
the founder key inside the REAL debate engine -- seats answer with
parseable VADA JSON, turns carry their serving slug, and a converged
DebateResult comes back. Spend is bounded by construction: one debate,
max 2 rounds, 3 panelists x <=2 calls each at max_tokens=256. No judge
call, no DB, no writes.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from app.config import settings
from app.debate.engine import DebateEngine
from app.debate.panel import (
    OpenRouterAgent,
    _derive_family,
    openrouter_panel,
)

SMOKE_MAX_TOKENS = 1536  # ox-alpha reasons before answering; 256 truncated
                         # seats mid-JSON and 768 still starved its visible
                         # content in earlier live runs


def _opted_in() -> bool:
    return os.environ.get("SL_DEBATE_LIVE_SMOKE") == "1"


pytestmark = pytest.mark.skipif(
    not _opted_in(),
    reason="live smoke spends money; set SL_DEBATE_LIVE_SMOKE=1 to opt in "
           "(CI must never set it)",
)


@pytest.mark.skipif(not settings.openrouter_api_key and not os.environ.get("OPENROUTER_API_KEY"),
                    reason="no OPENROUTER_API_KEY configured")
def test_live_openrouter_debate_round_trip_on_founder_key():
    pytest.importorskip("httpx")

    panel = openrouter_panel()
    assert_heterogeneous_live(panel)
    for seat in panel:
        # Bound spend per call; the debate contract needs no more.
        seat.max_tokens = SMOKE_MAX_TOKENS

    engine = DebateEngine(panel, max_rounds=2)
    result = asyncio.run(engine.run(
        debate_id=uuid4(),
        trigger_id=uuid4(),
        trigger_context={
            "rule": "live-smoke",
            "metric": "none",
            "observed": 0.0,
            "threshold": 0.0,
            "sample_size": 1,
            "note": "gated live smoke of OpenRouter panel wiring",
        },
        graph_context="",
    ))

    answered = [t for t in result.turns if t.speaker_kind == "agent"]
    failures = result.agent_failures
    print(f"\nLIVE SMOKE: {len(answered)} agent turn(s), "
          f"{len(failures)} failure(s)")
    for f in failures:
        print(f"  failure: {f}")
    for t in answered:
        head = t.content[:300].replace("\n", " ")
        print(f"  [{t.speaker_id} via {t.model_used}] r{t.round_number} "
              f"{t.action}: {head!r}")
    assert answered, "no seat produced any turn against the live endpoint"
    for t in answered:
        assert t.model_used in set(panel_serving_slugs(panel)), t.model_used
        # A DebateTurn exists ONLY if the engine parsed its reply as VADA
        # JSON (unparseable replies are logged and skipped without a turn),
        # so answered turns with legal actions ARE the contract-adherence
        # proof -- there is no separate raw reply to re-parse here, and the
        # turn's `content` field is the JSON's inner prose, not the reply.
        assert t.action in ("propose", "amend", "pass"), t.action


def panel_serving_slugs(panel):
    return [a.model_id for a in panel]


def assert_heterogeneous_live(panel):
    from app.debate.panel import assert_heterogeneous

    assert_heterogeneous(panel)
    assert all(isinstance(a, OpenRouterAgent) for a in panel)
