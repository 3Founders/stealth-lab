"""
Offline tests for find_ways' listwise sentence ranker (execution/sentence_ranker.py): the
verbalized context, ranking and citation handling, the reject rule, provider fallback and
never-block behaviour, and the settings switch in find_ways. No DB, no real model.
"""
from __future__ import annotations

import asyncio
import json

import app.execution.goal_resolution as gr
import app.mcp_server.server as srv
from app.execution import repo_facts as rf
from app.execution import sentence_ranker as sr
from app.execution.intent_resolution import GoalCandidate, IntentResolution, NormalizedIntent

FACTS = [
    {"claim_id": "R-001", "statement": "Node 20.11 runtime"},
    {"claim_id": "R-002", "statement": "No Python toolchain in this repository"},
    {"claim_id": "R-003", "statement": "Uses docx-js (npm docx@9.1) for Word output"},
]


def _run(coro):
    return asyncio.run(coro)


def _proc(pid, name, *, pre=None, effects=None):
    return {"id": pid, "procedure_id": f"S-{pid}", "name": name, "goal": name,
            "preconditions": pre or [], "expected_effects": effects or [], "steps": []}


class _Provider:
    """Completion-capable provider fake: returns scripted replies or raises."""

    def __init__(self, reply=None, *, raise_exc=None, name="gemma", completion=True):
        self.reply, self.raise_exc, self.name, self.model = reply, raise_exc, name, "fake-model"
        self._completion = completion
        self.prompts = []

    def supports(self, capability):
        return capability == "completion" and self._completion

    async def complete(self, system, user, max_tokens):
        self.prompts.append((system, user))
        if self.raise_exc:
            raise self.raise_exc
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


PY = _proc("p-py", "Generate DOCX with python-docx",
           pre=[{"subject": "runtime", "predicate": "is", "object": "python"}])
JS = _proc("p-js", "Generate DOCX with docx-js", effects=["a .docx file is written"])


def test_context_is_numbered_sentences_inside_untrusted_markers():
    from app.services.applicability_judge import extract_requirement_conditions
    conds = {str(p["id"]): extract_requirement_conditions(p) + rf.binding_conditions(p) for p in (PY, JS)}
    user, labels = sr.build_context("export a Word document", [PY, JS], FACTS, conds)
    assert labels == {"W1": PY, "W2": JS}
    assert "<untrusted_data>" in user and user.rstrip().endswith("</untrusted_data>")
    assert "R-002: No Python toolchain in this repository" in user
    assert "[REQUIRED] runtime is python" in user
    assert "[EFFECT]" in user and "[REQUIRED] a .docx file is written" not in user   # effects never required


def test_ranking_follows_the_model_and_keeps_only_real_fact_ids():
    reply = {"ranking": [
        {"way": "W2", "verdict": "APPLICABLE", "supporting": ["R-003", "R-999"], "blocking": [],
         "required_contradicted": False, "reason": "docx-js is already used"},
        {"way": "W1", "verdict": "PARTIALLY_APPLICABLE", "supporting": [], "blocking": [],
         "required_contradicted": False, "reason": "python unknown"},
    ]}
    sel = sr.SentenceRankingSelector(claims=FACTS, providers=[_Provider(reply)])
    kept, rejected = _run(sel("export a Word document", _fresh(), depth=0))
    assert [p["id"] for p in kept] == ["p-js", "p-py"] and rejected == []
    fit = kept[0]["_repo_fit"]
    assert fit["supporting_fact_ids"] == ["R-003"]          # invented R-999 dropped
    assert fit["rank"] == 1 and fit["provider"] == "gemma:fake-model"
    assert sel.report()["status"] == "ok" and sel.report()["mode"] == "listwise" and sel.calls == 1


def _fresh():
    return [dict(PY), dict(JS)]


def test_contradicted_required_condition_rejects_but_binding_or_effect_never_does():
    reply = {"ranking": [
        {"way": "W2", "verdict": "APPLICABLE", "supporting": ["R-003"], "blocking": []},
        # PY has a REQUIRED precondition (runtime is python) and R-002 contradicts it: rejected.
        {"way": "W1", "verdict": "INAPPLICABLE", "blocking": ["R-002"], "required_contradicted": True,
         "reason": "no python"},
    ]}
    sel = sr.SentenceRankingSelector(claims=FACTS, providers=[_Provider(reply)])
    kept, rejected = _run(sel("g", _fresh()))
    assert [p["id"] for p in kept] == ["p-js"] and [p["id"] for p in rejected] == ["p-py"]
    assert sel.report()["rejected"][0]["blocking_fact_ids"] == ["R-002"]

    # JS has only an EFFECT: even a model claiming a contradicted requirement cannot reject it.
    reply2 = {"ranking": [{"way": "W1", "verdict": "INAPPLICABLE", "blocking": ["R-001"],
                           "required_contradicted": True}]}
    sel2 = sr.SentenceRankingSelector(claims=FACTS, providers=[_Provider(reply2)])
    kept2, rejected2 = _run(sel2("g", [dict(JS)]))
    assert [p["id"] for p in kept2] == ["p-js"] and rejected2 == []

    # Without a real blocking fact id the reject flag is not trusted.
    reply3 = {"ranking": [{"way": "W1", "verdict": "INAPPLICABLE", "blocking": ["R-404"],
                           "required_contradicted": True}]}
    kept3, rejected3 = _run(sr.SentenceRankingSelector(claims=FACTS, providers=[_Provider(reply3)])("g", [dict(PY)]))
    assert [p["id"] for p in kept3] == ["p-py"] and rejected3 == []


def test_unknown_repeated_or_missing_ways_are_handled_safely():
    reply = {"ranking": [{"way": "W9", "verdict": "APPLICABLE"}, {"way": "W2", "verdict": "APPLICABLE"},
                         {"way": "W2", "verdict": "INAPPLICABLE", "required_contradicted": True}]}
    kept, rejected = _run(sr.SentenceRankingSelector(claims=FACTS, providers=[_Provider(reply)])("g", _fresh()))
    assert [p["id"] for p in kept] == ["p-js", "p-py"] and rejected == []   # W1 omitted -> appended
    assert "_repo_fit" not in kept[1]


def test_falls_back_to_the_next_provider_and_never_blocks_when_all_fail():
    good = {"ranking": [{"way": "W1", "verdict": "APPLICABLE"}, {"way": "W2", "verdict": "UNKNOWN"}]}
    first, second = _Provider(raise_exc=RuntimeError("429")), _Provider(good, name="vertex")
    sel = sr.SentenceRankingSelector(claims=FACTS, providers=[first, second])
    kept, _ = _run(sel("g", _fresh()))
    assert [p["id"] for p in kept] == ["p-py", "p-js"] and kept[0]["_repo_fit"]["provider"] == "vertex:fake-model"

    bad = sr.SentenceRankingSelector(claims=FACTS, providers=[_Provider("not json at all")])
    procs = _fresh()
    kept, rejected = _run(bad("g", procs))
    assert kept == procs and rejected == [] and bad.report()["status"] == "not_checked"

    none = sr.SentenceRankingSelector(claims=FACTS, providers=[])
    kept, _ = _run(none("g", _fresh()))
    assert [p["id"] for p in kept] == ["p-py", "p-js"] and "no completion-capable" in none.report()["detail"]


def test_skips_trivial_subgoal_decisions_and_respects_the_call_budget():
    prov = _Provider({"ranking": []})
    sel = sr.SentenceRankingSelector(claims=FACTS, providers=[prov])
    plain = _proc("p-1", "just do it")
    kept, _ = _run(sel("sub", [plain], depth=1))
    assert kept == [plain] and prov.prompts == []          # nothing to decide: no call
    sel.calls = rf.MAX_JUDGE_CALLS
    _run(sel("g", _fresh()))
    assert "budget" in sel.report()["detail"]


def test_from_settings_uses_only_completion_capable_providers(monkeypatch):
    jev = _Provider(name="jev", completion=False)
    gemma = _Provider(name="gemma")
    monkeypatch.setattr("app.services.semantic.providers.build_provider_chain", lambda *a, **k: [jev, gemma])
    sel = sr.SentenceRankingSelector.from_settings(FACTS)
    assert [p.name for p in sel.providers] == ["gemma"]


def test_real_providers_declare_completion_and_jev_never_does():
    from app.services.semantic.providers import ALL_CAPS, CAP_COMPLETION, OpenAICompatProvider

    assert CAP_COMPLETION not in ALL_CAPS
    assert OpenAICompatProvider("gemma", [], "m").supports(CAP_COMPLETION)


# ---------------------------------------------------------------- the find_ways switch
class _RC:
    lifespan_context = {"pool": None}


class _Ctx:
    request_context = _RC()


CLAIMS_MD = """# claims.md
CLAIM|R-001|current|stack|repository|Node 20.11 runtime|source=.nvmrc:1#sha=9f2c|version=1
CLAIM|R-002|current|deps|repository|Uses docx-js (npm docx@9.1) for Word output|source=package.json:23#sha=a41b|version=1
"""


def _cand(gid, name, score):
    return GoalCandidate(goal={"id": gid, "canonical_name": name}, score=score, lexical_overlap=0.5,
                         scope_match=0.5, status_score=1.0, fusion_position_score=1.0, rationale="")


def test_find_ways_uses_the_listwise_ranker_only_when_configured(monkeypatch):
    captured = {}

    async def fake_intent(pool, query, **kw):
        # Same shape as test_repo_facts_offline's wiring test: an ambiguous pair the repo facts decide.
        return IntentResolution(
            raw_input=query, outcome="ambiguous",
            normalized=NormalizedIntent(raw_input=query, outcome=query, used_fallback=True),
            candidates=[_cand("G-py", "Generate a Word document with python-docx", 0.70),
                        _cand("G-js", "Generate a Word document with docx-js npm", 0.68)],
        )

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        captured["selector"] = context.get("_procedure_selector")
        return gr.ResolvedGoalNode(goal_id=goal_id, goal_name="docx", depth=0, chosen="unresolved",
                                   unresolved_reason="none")

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_intent)
    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    monkeypatch.setattr("app.services.semantic.providers.build_provider_chain", lambda *a, **k: [])

    for mode, expected in (("pairwise", rf.RepoFactsProcedureSelector), ("listwise", sr.SentenceRankingSelector)):
        monkeypatch.setattr(srv.settings, "find_ways_ranker", mode)
        captured.clear()
        _run(srv.find_ways(query="add docx export", ctx=_Ctx(), use_llm=False, semantic=False,
                           repo_claims=CLAIMS_MD))
        assert isinstance(captured.get("selector"), expected), (mode, captured)
