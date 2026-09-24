"""
Offline tests for repo facts in find_ways (final_thing.md, option B +
tie-break): claims.md parsing, the cheap Goal tie-break, per-Procedure fact
selection, the judge-backed Procedure selector, the resolve_goal hook, and the
find_ways wiring. No DB, no real judge.
"""
from __future__ import annotations

import asyncio
import json

import app.execution.goal_resolution as gr
import app.mcp_server.server as srv
from app.execution import repo_facts as rf
from app.execution.intent_resolution import GoalCandidate, IntentResolution, NormalizedIntent
from app.services.access import AccessScope
from app.services.applicability_judge import ApplicabilityJudgment

CLAIMS_MD = """# claims.md
CLAIM|R-001|current|stack|repository|Node 20.11 runtime|source=.nvmrc:1#sha=9f2c|version=1
CLAIM|R-002|current|deps|repository|Uses docx-js (npm docx@9.1) for Word output|source=package.json:23#sha=a41b|version=1
CLAIM|R-003|current|testing|repository|Tests run with pnpm test|source=CLAUDE.md:88#sha=77de|version=1
"""


def _run(coro):
    return asyncio.run(coro)


def _cand(gid, name, score):
    return GoalCandidate(goal={"id": gid, "canonical_name": name}, score=score, lexical_overlap=0.5,
                         scope_match=0.5, status_score=1.0, fusion_position_score=1.0, rationale="")


def _judgment(cid, verdict, *, contra=0.0, sup=(), block=()):
    return ApplicabilityJudgment(candidate_id=cid, goal_or_query="g", applicability_probability=0.5,
                                 contradiction_probability=contra, preconditions_met_probability=0.5,
                                 verdict=verdict, supporting_claim_ids=list(sup), blocking_claim_ids=list(block),
                                 reason="r", model="fake")


class _Judge:
    def __init__(self, judgments=None, raise_exc=None):
        self.judgments, self.raise_exc, self.calls = judgments or {}, raise_exc, []

    async def judge_batch(self, goal, candidates):
        self.calls.append((goal, candidates))
        if self.raise_exc:
            raise self.raise_exc
        return [self.judgments[c.candidate_id] for c in candidates if c.candidate_id in self.judgments]


def _proc(pid, name, *, pre=None, goal=None):
    return {"id": pid, "procedure_id": f"S-{pid}", "name": name, "goal": goal or name,
            "preconditions": pre or [], "steps": []}


REQ = [{"subject": "runtime", "predicate": "is", "object": "python"}]


# ---------------------------------------------------------------- parsing
def test_parse_reuses_claims_grammar():
    facts, truncated = rf.parse_repo_claims(CLAIMS_MD)
    assert [f["claim_id"] for f in facts] == ["R-001", "R-002", "R-003"]
    assert facts[1]["topic"] == "deps" and "docx" in facts[1]["statement"] and not truncated


def test_parse_truncates_oversized_input():
    many = "\n".join(f"CLAIM|R-{i}|current|t|repository|fact number {i}|source=x|version=1" for i in range(250))
    facts, truncated = rf.parse_repo_claims(many)
    assert len(facts) == rf.MAX_REPO_CLAIMS and truncated


# ---------------------------------------------------------------- goal tie-break
def test_tiebreak_picks_the_goal_the_repo_matches():
    facts, _ = rf.parse_repo_claims(CLAIMS_MD)
    cands = [_cand("G-py", "Generate a Word document with python-docx", 0.70),
             _cand("G-js", "Generate a Word document with docx-js npm", 0.68)]
    winner, detail = rf.goal_tiebreak(cands, facts, margin=0.12)
    assert winner is not None and winner.goal["id"] == "G-js"
    assert detail[0]["best_fact"] == "R-002"


def test_tiebreak_stays_ambiguous_when_repo_does_not_separate_them():
    facts, _ = rf.parse_repo_claims(CLAIMS_MD)
    cands = [_cand("G-a", "Deploy to kubernetes", 0.70), _cand("G-b", "Deploy to nomad", 0.69)]
    winner, _ = rf.goal_tiebreak(cands, facts, margin=0.12)
    assert winner is None


# ---------------------------------------------------------------- fact selection
def test_select_claims_bounded_and_related_first():
    facts = [{"claim_id": f"R-{i}", "statement": f"unrelated fact {i}"} for i in range(30)]
    facts.append({"claim_id": "R-docx", "statement": "Uses docx for Word output"})
    picked = rf.select_claims_for(_proc("P-1", "Create a docx Word file"), facts)
    assert picked[0]["claim_id"] == "R-docx"
    assert rf.CLAIMS_PER_PROCEDURE_MIN <= len(picked) <= rf.CLAIMS_PER_PROCEDURE_MAX


# ---------------------------------------------------------------- selector
def test_selector_without_judge_leaves_order_and_says_not_checked():
    facts, _ = rf.parse_repo_claims(CLAIMS_MD)
    sel = rf.RepoFactsProcedureSelector(claims=facts, judge=None)
    feasible = [_proc("P-1", "a"), _proc("P-2", "b")]
    kept, rejected = _run(sel(feasible_goal := "g", feasible))
    assert kept == feasible and rejected == [] and sel.report()["status"] == "not_checked"


def test_selector_judge_failure_never_blocks():
    facts, _ = rf.parse_repo_claims(CLAIMS_MD)
    sel = rf.RepoFactsProcedureSelector(claims=facts, judge=_Judge(raise_exc=RuntimeError("chain down")))
    feasible = [_proc("P-1", "a"), _proc("P-2", "b")]
    kept, _ = _run(sel("g", feasible))
    assert kept == feasible and sel.report()["status"] == "not_checked"


def test_selector_drops_contradicted_required_and_reorders_by_verdict():
    facts, _ = rf.parse_repo_claims(CLAIMS_MD)
    judge = _Judge({
        "P-py": _judgment("P-py", "INAPPLICABLE", contra=0.9, block=["R-001"]),
        "P-a": _judgment("P-a", "UNKNOWN"),
        "P-b": _judgment("P-b", "APPLICABLE", sup=["R-002"]),
    })
    sel = rf.RepoFactsProcedureSelector(claims=facts, judge=judge)
    feasible = [_proc("P-py", "python way", pre=REQ), _proc("P-a", "a"), _proc("P-b", "docx js way")]
    kept, rejected = _run(sel("g", feasible))
    assert [p["id"] for p in kept] == ["P-b", "P-a"]
    assert [p["id"] for p in rejected] == ["P-py"]
    assert kept[0]["_repo_fit"]["supporting_fact_ids"] == ["R-002"]
    # judge only ever sees the caller's own facts, bounded per procedure
    _, inputs = judge.calls[0]
    assert all(len(i.claims) <= rf.CLAIMS_PER_PROCEDURE_MAX for i in inputs)


def test_selector_skips_single_unconditioned_child_but_judges_root():
    facts, _ = rf.parse_repo_claims(CLAIMS_MD)
    judge = _Judge({"P-1": _judgment("P-1", "APPLICABLE")})
    sel = rf.RepoFactsProcedureSelector(claims=facts, judge=judge)
    _run(sel("child", [_proc("P-1", "x")], depth=2))
    assert judge.calls == []
    _run(sel("root", [_proc("P-1", "x")], depth=0))
    assert len(judge.calls) == 1


def test_selector_call_budget():
    facts, _ = rf.parse_repo_claims(CLAIMS_MD)
    judge = _Judge({"P-1": _judgment("P-1", "APPLICABLE"), "P-2": _judgment("P-2", "APPLICABLE")})
    sel = rf.RepoFactsProcedureSelector(claims=facts, judge=judge)
    for _ in range(rf.MAX_JUDGE_CALLS + 2):
        _run(sel("g", [_proc("P-1", "x"), _proc("P-2", "y")]))
    assert len(judge.calls) == rf.MAX_JUDGE_CALLS and sel.report()["status"] == "not_checked"


# ---------------------------------------------------------------- resolve_goal hook
class _Pool:
    async def fetchrow(self, sql, *args):
        return {"id": "G-1", "canonical_name": "make a docx", "verification_requirement": {}}


def _patch_feasible(monkeypatch, procs):
    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        return [(p, True) for p in procs]

    async def fake_children(pool, goal, proc, **kw):
        return []

    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)
    monkeypatch.setattr(gr, "_resolve_procedure_children", fake_children)


def test_resolve_goal_uses_selector_and_reports_repo_fit(monkeypatch):
    _patch_feasible(monkeypatch, [_proc("P-a", "a"), _proc("P-b", "b")])

    async def selector(goal_name, feasible, *, depth=0):
        feasible[1]["_repo_fit"] = {"verdict": "APPLICABLE", "supporting_fact_ids": ["R-002"]}
        return [feasible[1], feasible[0]], []

    tree = _run(gr.resolve_goal(_Pool(), "G-1", context={"_procedure_selector": selector}, scope=AccessScope.unrestricted()))
    assert tree.procedure["id"] == "P-b"
    assert tree.procedure["repo_fit"]["supporting_fact_ids"] == ["R-002"]


def test_resolve_goal_all_contradicted_is_honest_unresolved(monkeypatch):
    p = _proc("P-py", "python way", pre=REQ)
    p["_repo_fit"] = {"blocking_fact_ids": ["R-001"]}
    _patch_feasible(monkeypatch, [p])

    async def selector(goal_name, feasible, *, depth=0):
        return [], feasible

    tree = _run(gr.resolve_goal(_Pool(), "G-1", context={"_procedure_selector": selector}, scope=AccessScope.unrestricted()))
    assert tree.chosen == "unresolved"
    assert "contradicted by repo facts" in tree.unresolved_reason and "R-001" in tree.unresolved_reason


def test_resolve_goal_without_selector_unchanged(monkeypatch):
    _patch_feasible(monkeypatch, [_proc("P-a", "a"), _proc("P-b", "b")])
    tree = _run(gr.resolve_goal(_Pool(), "G-1", context={}, scope=AccessScope.unrestricted()))
    assert tree.procedure["id"] == "P-a" and "repo_fit" not in tree.procedure


# ---------------------------------------------------------------- find_ways wiring
class _RC:
    lifespan_context = {"pool": None}


class _Ctx:
    request_context = _RC()


def test_find_ways_tiebreak_resolves_ambiguous_and_wires_selector(monkeypatch):
    captured = {}

    async def fake_intent(pool, query, *, context=None, client=None, embedder=None, scope=None,
                          tenant_scope=None, status=None, top_k=5):
        return IntentResolution(
            raw_input=query, outcome="ambiguous",
            normalized=NormalizedIntent(raw_input=query, outcome=query, used_fallback=True),
            candidates=[_cand("G-py", "Generate a Word document with python-docx", 0.70),
                        _cand("G-js", "Generate a Word document with docx-js npm", 0.68)],
        )

    async def fake_resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        captured["goal_id"] = goal_id
        captured["selector"] = context.get("_procedure_selector")
        return gr.ResolvedGoalNode(goal_id=goal_id, goal_name="docx", depth=0, chosen="unresolved",
                                   unresolved_reason="none")

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_intent)
    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", fake_resolve_goal)
    raw = _run(srv.find_ways(query="add docx export", ctx=_Ctx(), use_llm=False,
                             semantic=False, repo_claims=CLAIMS_MD))
    out = json.loads(raw)
    assert out["outcome"] == "resolved" and captured["goal_id"] == "G-js"
    assert out["repo_facts"]["count"] == 3 and out["repo_facts"]["goal_tiebreak"]["resolved"] is True
    assert isinstance(captured["selector"], rf.RepoFactsProcedureSelector)
    assert out["repo_facts"]["procedure_check"] is not None


def test_find_ways_without_repo_claims_has_no_repo_facts(monkeypatch):
    async def fake_intent(pool, query, **kw):
        return IntentResolution(raw_input=query, outcome="no_match",
                                normalized=NormalizedIntent(raw_input=query, outcome=query, used_fallback=True),
                                proposed_goal={"canonical_name": query})

    monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_intent)
    out = json.loads(_run(srv.find_ways(query="x", ctx=_Ctx(), use_llm=False, semantic=False)))
    assert out["repo_facts"] is None
