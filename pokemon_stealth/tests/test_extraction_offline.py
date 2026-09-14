from __future__ import annotations

import asyncio

from pokemon_stealth.extraction import ExtractionResult, extract_knowledge, persist_extraction
from pokemon_stealth.knowledge import KnowledgeStore
from pokemon_stealth.llm import MockAgent
from pokemon_stealth.state import GameState


def _state(**overrides) -> GameState:
    base = dict(map_id=0, position=(1, 1), party=[], badges=[], money=0, frame_count=0)
    base.update(overrides)
    return GameState(**base)


def test_extract_knowledge_parses_valid_response():
    payload = (
        '{"claims": [{"statement": "Water beats Rock at Brock gym", "tags": ["brock"]}], '
        '"procedures": [], "failures": []}'
    )
    agent = MockAgent(responses=[payload])
    outcome = asyncio.run(
        extract_knowledge(
            agent, mission_id="brock", trajectory_summary=["did stuff"],
            before=_state(), after=_state(badges=["boulder"]), success=True, failure_reason=None,
        )
    )
    assert outcome.parse_error is None
    assert len(outcome.result.claims) == 1
    assert outcome.result.claims[0].statement == "Water beats Rock at Brock gym"


def test_extract_knowledge_returns_zero_items_on_malformed_response():
    agent = MockAgent(responses=["I think the agent did well, no structured output here."])
    outcome = asyncio.run(
        extract_knowledge(
            agent, mission_id="brock", trajectory_summary=[],
            before=_state(), after=_state(), success=False, failure_reason="party_wiped",
        )
    )
    assert outcome.parse_error is not None
    assert outcome.result == ExtractionResult()  # zero items -- no fabricated fallback


def test_extract_knowledge_zero_items_is_a_valid_response():
    agent = MockAgent(responses=['{"claims": [], "procedures": [], "failures": []}'])
    outcome = asyncio.run(
        extract_knowledge(
            agent, mission_id="brock", trajectory_summary=[],
            before=_state(), after=_state(), success=True, failure_reason=None,
        )
    )
    assert outcome.parse_error is None
    assert outcome.result.claims == []
    assert outcome.result.procedures == []


def test_persist_extraction_writes_new_items_and_reuses_dupes(tmp_path):
    store = KnowledgeStore(tmp_path / "stealth")
    store.write_claim("Water beats Rock at Brock's gym.", scope="pokemon-red", tags=[], evidence=["R-0001"])

    result = ExtractionResult.model_validate(
        {
            "claims": [{"statement": "Water beats Rock at Brock's gym", "tags": []}],  # near-dup of existing
            "procedures": [{"title": "New Procedure", "applicability": [], "method": [], "verification": [], "tags": []}],
            "failures": [],
        }
    )
    written = persist_extraction(store, result, run_id="R-0002")
    assert written["claims"] == ["C-0001"]  # reused, not duplicated
    assert written["procedures"] == ["P-0001"]  # newly created
    assert len(store.load_all_claims()) == 1
    assert len(store.load_all_procedures()) == 1
