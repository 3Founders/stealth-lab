"""Offline tests for `.stealth/library.md` / `routing.md` (app.stealth.library), the library context in
find_ways (app.services.library_context + retrieval_service.search_goals + goal_choice), and the local
OBS conditioning of the model plan (routing.service). No database, no network.

The fixtures in packaging/npm/test/fixtures/library are shared with the npm client's tests
(packaging/npm/test/library.test.mjs): both implementations must turn the same inputs into the same
canonical text, idx and parse.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.stealth import library as L

FIX = Path(__file__).resolve().parents[2] / "packaging" / "npm" / "test" / "fixtures" / "library"
GID = "2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11"


def _run(coro):
    return asyncio.run(coro)


def _entry(eid="L-7f3a1c", **kw):
    base = dict(id=eid, title="Fix the parser", unit=".", g=None, verified_at="2026-10-07",
                procs=[L.LibProc(1, "patch", solution=f"solutions/{eid}.diff",
                                 touches=[L.Touch("src/p.py", "1a2b3c4")],
                                 steps=[L.LibStep(1, "action", "run tests", "pytest -q")])])
    base.update(kw)
    return L.LibEntry(**base)


# ============================================================ grammar

def test_round_trip_keeps_pipes_newlines_percent_and_csv_separators_exactly():
    e = _entry(title="Fix KeyError | in parser (90% %7C)", unit="packages/api", g=GID, tags=["a,b", "c#d"],
               procs=[L.LibProc(1, "patch | it", touches=[L.Touch("src/p,q.py", "1a2b3c4"), L.Touch("x#y.py", "dead")],
                                steps=[L.LibStep(1, "action", "edit\nmultiline", "pytest -q | tail -3")])])
    text = L.render_library([e])
    assert L.parse_library(text).entries == [e]
    for line in text.splitlines():
        if line.startswith(("GOAL", "PROC", "STEP")):
            assert "\n" not in line
    assert "check=pytest -q %7C tail -3" in text            # the shell pipe survives, encoded


def test_lines_group_by_id_not_position_and_render_is_canonical():
    a, b = _entry("L-aaaaaa"), _entry("L-bbbbbb", title="Second")
    lines = L.render_library([a, b]).splitlines()
    shuffled = "\n".join(reversed([ln for ln in lines if ln and not ln.startswith("#")]))
    lib = L.parse_library(shuffled)
    assert [e.id for e in lib.entries] == ["L-aaaaaa", "L-bbbbbb"]
    assert L.render_library(lib) == L.render_library([a, b])


def test_conflict_keeps_later_verified_at_and_reports_it():
    old = L.render_goal_line(_entry(verified_at="2026-09-01"))
    new = L.render_goal_line(_entry(verified_at="2026-10-01", status="stale"))
    for text in (old + "\n" + new, new + "\n" + old):
        lib = L.parse_library(text)
        assert lib.entries[0].status == "stale"
        assert any("conflict" in p for p in lib.problems)


def test_orphans_and_foreign_lines_are_reported_never_invented():
    lib = L.parse_library("STEP|L-abcdef.p1:1|action|x|check=-\nPROC|L-abcdef.p1|n|p=-|solution=-|touches=-\nhello\n")
    assert lib.entries == []
    assert sum("orphan" in p for p in lib.problems) == 2
    assert any("not a library line" in p for p in lib.problems)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_real_git_union_merge_of_two_branches_parses_to_both_entries(tmp_path):
    """merge=union is what .stealth/.gitattributes sets for library.md: prove a real union merge of two
    branches that each appended an entry, while one edited a third, parses to all three, edited."""
    base_e = _entry("L-111111", title="Base entry")
    ours = [base_e, _entry("L-222222", title="Ours")]
    theirs = [dataclasses.replace(base_e, status="stale", verified_at="2026-10-09"), _entry("L-0a0a0a", title="Theirs")]
    paths = {}
    for name, entries in (("base", [base_e]), ("ours", ours), ("theirs", theirs)):
        paths[name] = tmp_path / f"{name}.md"
        paths[name].write_text(L.render_library(entries), encoding="utf-8", newline="\n")
    subprocess.run(["git", "merge-file", "--union", str(paths["ours"]), str(paths["base"]), str(paths["theirs"])],
                   check=True)
    lib = L.parse_library(paths["ours"].read_text(encoding="utf-8"))
    by = lib.by_id()
    assert sorted(by) == ["L-0a0a0a", "L-111111", "L-222222"]
    assert by["L-111111"].status == "stale"               # the edit (later verified_at) wins over the base line


# ============================================================ shared fixtures (JS conformance)

def test_fixture_union_merged_canonicalises_to_the_shared_expected_text_and_idx():
    lib = L.parse_library((FIX / "union_merged.md").read_text(encoding="utf-8"))
    canonical = L.render_library(lib)
    assert canonical == (FIX / "canonical.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert L.render_library_idx(canonical) == (FIX / "canonical.idx").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert lib.problems == ["conflict: two versions of L-b00c1e; kept one", "line 16: not a library line, skipped",
                            "orphan: L-ffffff.p1:1 has no PROC line; skipped"]


def test_fixture_routing_parses_to_the_shared_expected_json():
    routes, obs = L.parse_routing((FIX / "routing.md").read_text(encoding="utf-8"))
    got = {"routes": [dataclasses.asdict(r) for r in routes], "obs": [dataclasses.asdict(o) for o in obs],
           "local_obs_b00c1e": L.local_obs_for_goal(routes, obs, GID),
           "rendered": [L.render_route_line(r) for r in routes] + [L.render_obs_line(o) for o in obs]}
    assert got == json.loads((FIX / "routing.expected.json").read_text(encoding="utf-8"))


# ============================================================ idx

def test_idx_rows_point_at_exact_block_ranges_with_their_hash():
    canonical = (FIX / "canonical.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = canonical.split("\n")
    for row in L.library_idx_rows(canonical):
        block = lines[row.start - 1:row.end]
        assert block[0].startswith(f"GOAL|{row.id}|")
        assert all(ln.split("|")[1].split(".")[0] == row.id for ln in block)
        assert L.short_hash("\n".join(block)) == row.block_sha


def test_parse_library_rows_round_trips_idx_and_truncates_at_the_byte_cap():
    idx = (FIX / "canonical.idx").read_text(encoding="utf-8")
    rows, truncated = L.parse_library_rows(idx)
    assert [r.id for r in rows] == ["L-0a91f2", "L-77d3e0", "L-b00c1e"] and not truncated
    assert rows[0].title.startswith("Fix KeyError | when")
    rows, truncated = L.parse_library_rows(idx, max_bytes=len(idx.encode()) - 20)
    assert truncated and [r.id for r in rows] == ["L-0a91f2", "L-77d3e0"]


# ============================================================ routing.md from model plans

def test_routes_from_current_plan_shape_never_invents_per_rung_numbers():
    plan = {"status": "ok", "ladder": ["gpt-oss-120b|kel", "claude-sonnet-5-5|claude-code"], "params_version": 4,
            "recommended": {"p_success": 0.86, "p_success_q05": 0.75, "p_success_q95": 0.93, "expected_cost_usd": 0.061}}
    (r,) = L.routes_from_model_plan(plan, route_id="R-abcdef", goal=None, g=GID, as_of="2026-10-07")
    line = L.render_route_line(r)
    assert "ladder=gpt-oss-120b::kel > claude-sonnet-5-5::claude-code|" in line
    assert line.endswith("whole=p=0.86[0.75,0.93]:$0.0610")
    assert L.parse_routing(line)[0] == [r]


def test_routes_from_plan_5_2_shape_one_route_per_step():
    plan = {"basis": "prior", "fit_id": "f-9", "as_of": "2026-10-07", "steps": [
        {"step": "*", "ladder": [{"unit": "a|s", "p_ok_mean": 0.5, "p_ok_q05": 0.4, "p_ok_q95": 0.6, "cost_mean": 0.01}]},
        {"step": 2, "ladder": [{"unit": "b|s", "p_ok_mean": 0.7, "p_ok_q05": 0.6, "p_ok_q95": 0.8, "cost_mean": 0.02}]}]}
    routes = L.routes_from_model_plan(plan, route_id="R-abcdef", goal="L-abcdef", g=GID, as_of="x")
    assert [(r.step, r.basis, r.fit, r.ladder[0].unit) for r in routes] == [("*", "prior", "f-9", "a|s"),
                                                                           ("2", "prior", "f-9", "b|s")]
    assert L.routes_from_model_plan({"status": "no_candidates"}, route_id="R-abcdef", goal=None, g=GID,
                                    as_of="x") == []


# ============================================================ local OBS -> posterior draw weights

def test_local_obs_reweighting_moves_weight_to_draws_that_explain_the_local_outcomes():
    from app.routing.service import local_obs_loglik, reweight_by_local_obs

    s, n_nodes = 400, 5
    rng = np.random.default_rng(0)
    p_true = rng.uniform(0.05, 0.95, size=s)                       # per-draw success of one unit
    p = np.repeat(p_true[:, None, None], n_nodes, axis=2)           # (S, U=1, N), no eps dependence
    eps_w = np.full(n_nodes, 1 / n_nodes)
    zero = np.zeros(s)
    prior = np.full(s, 1 / s)
    good, _ = reweight_by_local_obs(prior, local_obs_loglik(p, eps_w, zero, zero, [(0, 10, 10)]))
    bad, _ = reweight_by_local_obs(prior, local_obs_loglik(p, eps_w, zero, zero, [(0, 10, 0)]))
    assert good @ p_true > prior @ p_true + 0.25 > bad @ p_true + 0.5
    # no local evidence: the weights are exactly the prior weights
    same, ev = reweight_by_local_obs(prior, local_obs_loglik(p, eps_w, zero, zero, []))
    assert np.allclose(same, prior) and ev["ess"] == pytest.approx(s)


def test_local_obs_likelihood_uses_the_check_model():
    """A check that accepts every wrong answer (alpha=1) makes an acceptance uninformative."""
    from app.routing.service import local_obs_loglik

    p = np.linspace(0.1, 0.9, 9)[:, None, None]
    ll = local_obs_loglik(p, np.array([1.0]), np.ones(9), np.zeros(9), [(0, 5, 5)])
    assert np.allclose(ll, ll[0])


# ============================================================ library context

def test_repo_identity_parsing_and_strength():
    from app.services.library_context import parse_repo_identity

    ident, problem = parse_repo_identity({"repo_id": "r:0123456789abcdef", "public_name": "Django/Django"})
    assert problem is None and ident.public_name == "django/django" and not ident.weak
    assert parse_repo_identity({"repo_id": "c:0123456789abcdef", "strength": "weak"})[0].weak
    assert parse_repo_identity({"repo_id": "p:0123456789abcdef"})[0].weak          # p: ids are always weak
    assert parse_repo_identity({"repo_id": "django/django"})[0] is None
    assert parse_repo_identity({"repo_id": "r:0123456789abcdef", "public_name": "not a name"})[1]
    assert parse_repo_identity(None) == (None, None)


def test_weak_identity_never_queries_other_repos_goals():
    from app.services import library_context as lc

    class Pool:
        async def fetch(self, *a):
            raise AssertionError("a weak identity must not look up other Goals")

    ident, _ = lc.parse_repo_identity({"repo_id": "c:0123456789abcdef", "public_name": "a/b", "strength": "weak"})
    assert _run(lc.same_repo_goal_ids(Pool(), ident)) == []
    ctx = lc.LibraryContext(identity=ident)
    assert not ctx.active                                   # nothing to pin, no search change at all


def _ctx(n_rows=3, **ident):
    from app.services import library_context as lc
    from app.services.retrieval_service import Hit

    idx = (FIX / "canonical.idx").read_text(encoding="utf-8")
    ctx = lc.build(ident or None, idx)
    return ctx, lc.make_local_hit_factory(Hit)


def test_select_local_skips_failed_attempts_and_pins_named_goals():
    ctx, make = _ctx()
    ctx.select_local("csv export breaks on quoted commas", make)
    assert [h.id for h in ctx.local_hits] == ["L-0a91f2", "L-b00c1e"]      # L-77d3e0 is outcome=fail
    assert ctx.pinned == {GID: "library"}
    assert ctx.local_hits[1].text == "Make the CSV export handle commas inside quoted fields (in packages/api)"


def test_select_local_preselects_by_overlap_when_over_budget():
    from app.services import library_context as lc
    from app.services.retrieval_service import Hit

    rows = "\n".join(f"L-{i:06x}|current|pass|.|-|2026-10-0{i % 9 + 1}|1|1|x|entry about topic{i}" for i in range(20))
    ctx = lc.build(None, rows + "\nL-0000ff|current|pass|.|-|-|1|1|x|csv quoting export\n")
    ctx.select_local("fix csv quoting", lc.make_local_hit_factory(Hit), k=6)
    ids = [h.id for h in ctx.local_hits]
    # the overlapping entry first, then the most recently verified to fill the judge budget
    assert ids[0] == "L-0000ff" and len(ids) == 6
    assert all(i != "L-0000ff" for i in ids[1:])


def test_select_local_stems_and_splits_identifiers():
    from app.services import library_context as lc
    from app.services.retrieval_service import Hit

    rows = "\n".join(f"L-{i:06x}|current|pass|.|-|2026-10-09|1|1|x|entry about topic{i}" for i in range(20))
    ctx = lc.build(None, rows + "\nL-0000aa|current|pass|.|-|2026-01-01|1|1|x|throttle loginHandler requests\n")
    ctx.select_local("add throttling to the login handler", lc.make_local_hit_factory(Hit), k=6)
    assert ctx.local_hits[0].id == "L-0000aa"      # older than all the others, but it shares stemmed words


def test_matches_order_and_unjudged_labelling():
    ctx, make = _ctx()
    ctx.select_local("csv", make)
    a, b = ctx.local_hits
    b.judged, b.relation, b.confidence = True, "matches", 0.9
    a.judged, a.relation, a.confidence = True, "unrelated", 0.9
    assert [m["id"] for m in ctx.matches()] == ["L-b00c1e"]
    a.judged = False
    got = ctx.matches()
    assert [m["id"] for m in got] == ["L-b00c1e", "L-0a91f2"] and got[1]["judged"] is False
    assert got[0]["read"] == "rg '^(GOAL|PROC|STEP)\\|L-b00c1e' .stealth/library.md"


def test_tiebreak_only_for_a_single_library_pinned_goal_inside_the_margin():
    from app.services.library_context import apply_tiebreak
    from app.services.retrieval_service import Hit

    def hit(i, conf, pin=None):
        h = Hit(i, i, i, "K0", confidence=conf, judged=True, relation="matches")
        if pin:
            h.extra["pin"] = pin
        return h

    assert apply_tiebreak([hit("a", .9), hit("b", .85, "library")], .1).id == "b"
    assert apply_tiebreak([hit("a", .9), hit("b", .85, "same_repo")], .1) is None
    assert apply_tiebreak([hit("a", .9, "library"), hit("b", .85, "library")], .1) is None
    assert apply_tiebreak([hit("a", .9), hit("b", .7, "library")], .1) is None


# ============================================================ retrieval: judged with, never instead of, the top-k

class _Verdict:
    def __init__(self, relation, confidence):
        self.ok, self.provider = True, "fake"
        self.value = {"relation": relation, "confidence": confidence}


class _Judge:
    def __init__(self, verdicts):
        self.verdicts, self.seen = verdicts, []

    async def judge_identity(self, kind, ctx_text, text):
        self.seen.append(text)
        return _Verdict(*self.verdicts.get(text, ("unrelated", 0.9)))


def _patch_search(monkeypatch, cands, pinned_rows=()):
    import app.services.retrieval_service as rs

    async def legs(*a, **k):
        return list(cands), len(cands), 0

    async def nothing(*a, **k):
        return None

    async def targets(pool, table):
        return [(object(), table)]

    calls = []

    async def leg_rows(targets_, sql_for, args, *, key, limit):
        calls.append(sql_for("goal_search_index"))
        return [dict(r) for r in pinned_rows]

    monkeypatch.setattr(rs, "_legs", legs)
    monkeypatch.setattr(rs, "_catch_up_projection", nothing)
    monkeypatch.setattr(rs, "_leg_targets", targets)
    monkeypatch.setattr(rs, "_leg_rows", leg_rows)
    return calls


def _cands(n):
    from app.services.retrieval_service import Hit

    return [Hit(f"00000000-0000-4000-8000-{i:012d}", f"goal {i}", f"goal {i}", "K0", rrf=1 / (i + 1)) for i in range(n)]


def test_search_goals_without_library_judges_exactly_the_top_k(monkeypatch):
    import app.services.retrieval_service as rs
    from app.services.access import AccessScope

    calls = _patch_search(monkeypatch, _cands(12))
    judge = _Judge({})
    ctx = _run(rs.build_query_context("do it"))
    _run(rs.search_goals(object(), ctx, scope=AccessScope.unrestricted(), judge=judge))
    assert judge.seen == [f"goal {i}" for i in range(8)] and calls == []


def test_library_entries_and_pinned_goal_are_judged_in_addition_to_the_top_k(monkeypatch):
    import app.services.retrieval_service as rs
    from app.services.access import AccessScope

    pinned_row = {"id": GID, "name": "CSV export quoting", "text": "CSV export quoting", "home_shard_id": "K0"}
    calls = _patch_search(monkeypatch, _cands(12), [pinned_row])
    ctx_lib, make = _ctx()
    ctx_lib.select_local("csv export quoted commas", make)
    judge = _Judge({"CSV export quoting": ("matches", 0.8), "goal 0": ("matches", 0.8),
                    "Make the CSV export handle commas inside quoted fields (in packages/api)": ("matches", 0.9)})
    qctx = _run(rs.build_query_context("csv export quoted commas"))
    found = _run(rs.search_goals(object(), qctx, scope=AccessScope.unrestricted(), judge=judge, library=ctx_lib))
    assert judge.seen[:8] == [f"goal {i}" for i in range(8)]                  # the top-8 are all still judged
    assert "CSV export quoting" in judge.seen and len(judge.seen) == 8 + 1 + 2
    assert [h.id for h in found.resolved][:2] == [GID, "00000000-0000-4000-8000-000000000000"]  # tie -> pinned first
    assert all(h.id != "L-b00c1e" for h in found.resolved)                     # local entries never resolve globally
    assert ctx_lib.matches()[0]["id"] == "L-b00c1e"
    assert len(calls) == 1 and "goal_id = ANY($1::uuid[])" in calls[0]          # only the missing pin was fetched


def test_strong_identity_judges_the_repo_own_nearest_goals(monkeypatch):
    import app.services.retrieval_service as rs
    from app.services import library_context as lc
    from app.services.access import AccessScope

    near = {"id": "00000000-0000-4000-8000-0000000000aa", "name": "repo goal", "text": "repo goal",
            "home_shard_id": "K0", "r": 1.0}
    calls = _patch_search(monkeypatch, _cands(3), [near])

    async def repo_ids(pool, identity):
        return ["00000000-0000-4000-8000-0000000000aa"]

    monkeypatch.setattr(lc, "same_repo_goal_ids", repo_ids)
    ctx_lib = lc.build({"repo_id": "r:0123456789abcdef", "public_name": "o/n"}, "")
    judge = _Judge({"repo goal": ("matches", 0.9)})
    qctx = _run(rs.build_query_context("do the repo thing"))
    found = _run(rs.search_goals(object(), qctx, scope=AccessScope.unrestricted(), judge=judge, library=ctx_lib))
    assert found.resolved[0].id == near["id"] and found.resolved[0].extra["pin"] == "same_repo"
    assert "scope_entity_id = ANY($2::text[])" in calls[0] and ctx_lib.same_repo_goals == 1


# ============================================================ find_ways: absent args change nothing

class _RC:
    def __init__(self):
        self.lifespan_context = {"pool": None}


class _Ctx:
    request_context = _RC()


def _patch_find_ways(monkeypatch, outcome="resolved"):
    import app.mcp_server.server as srv

    seen = {}

    async def choice(pool, query, facts, *, scope, embedder, top_k, collect=None, **kw):
        seen["kw"] = kw
        lib = kw.get("library")
        if lib is not None:
            for h in lib.local_hits:
                h.judged, h.relation, h.confidence = True, "matches", 0.9
        if outcome == "no_match":
            return "no_match", None, {"goal_judgment": {}, "candidates": [], "proposed_goal": {}, "rationale": "x"}
        return "resolved", {"id": GID, "canonical_name": "g"}, {"goal_judgment": {"mode": "contextual"}}

    async def resolve_goal(pool, goal_id, *, context, scope, max_depth=6, embedder=None):
        from app.execution.goal_resolution import ResolvedGoalNode
        return ResolvedGoalNode(goal_id=goal_id, goal_name="g", depth=0, chosen="procedure",
                                procedure={"id": "V", "procedure_id": "P", "name": "p", "version": 1, "steps": []},
                                children=[], rationale="r")

    async def attribution(pool, body):
        return None

    async def record(*a, **k):
        return None

    async def build_qc(query, claims, embedder=None, cfg=None):
        from app.services.retrieval_service import QueryContext
        return QueryContext(query=query, claims=[], text=query)

    monkeypatch.setattr(srv, "_find_ways_goal_choice_impl", choice)
    monkeypatch.setattr("app.execution.goal_resolution.resolve_goal", resolve_goal)
    monkeypatch.setattr("app.services.license_attribution.attach_attribution", attribution)
    monkeypatch.setattr("app.services.retrieval_service.build_query_context", build_qc)
    monkeypatch.setattr(srv, "_record_find_ways", record)
    monkeypatch.setattr("app.mcp_server.find_ways_governor.governor", lambda: None)
    return srv, seen


def test_find_ways_without_library_args_is_unchanged(monkeypatch):
    srv, seen = _patch_find_ways(monkeypatch)
    body = json.loads(_run(srv.find_ways(query="make the csv export handle quotes", ctx=_Ctx(),
                                         use_llm=False, semantic=False)))
    assert seen["kw"] == {}                                  # the goal choice saw no library argument at all
    assert "library_matches" not in body and "library" not in body and "routing_rows" not in body


def test_find_ways_with_library_rows_returns_this_repos_matches_first(monkeypatch):
    srv, seen = _patch_find_ways(monkeypatch, outcome="no_match")
    body = json.loads(_run(srv.find_ways(query="csv export quoted commas break", ctx=_Ctx(), use_llm=False,
                                         semantic=False,
                                         library_rows=(FIX / "canonical.idx").read_text(encoding="utf-8"))))
    assert seen["kw"]["library"] is not None
    assert [m["id"] for m in body["library_matches"]] == ["L-0a91f2", "L-b00c1e"]
    assert body["library"]["rows"] == 3 and body["outcome"] == "no_match"


def test_governor_cache_key_includes_library_arguments(monkeypatch):
    import app.mcp_server.server as srv
    from app.mcp_server.find_ways_governor import FindWaysGovernor

    srv_, _ = _patch_find_ways(monkeypatch)
    gov = FindWaysGovernor()
    monkeypatch.setattr("app.mcp_server.find_ways_governor.governor", lambda: gov)
    monkeypatch.setattr(srv, "_find_ways_caller", lambda ctx: "session:x")
    q = "make the csv export handle quotes"
    first = json.loads(_run(srv.find_ways(query=q, ctx=_Ctx(), use_llm=False, semantic=False)))
    with_lib = json.loads(_run(srv.find_ways(query=q, ctx=_Ctx(), use_llm=False, semantic=False,
                                             library_rows=(FIX / "canonical.idx").read_text(encoding="utf-8"))))
    assert "governor" not in with_lib and "library_matches" in with_lib      # not served the library-less cache
    again = json.loads(_run(srv.find_ways(query=q, ctx=_Ctx(), use_llm=False, semantic=False)))
    assert again.get("governor") == {"cached": True} and "library_matches" not in first


def test_model_plan_gets_local_obs_and_routing_rows_only_with_library_args(monkeypatch):
    import app.routing.plan as plan_mod

    srv, _ = _patch_find_ways(monkeypatch)
    got = []

    async def model_plan(pool, **kw):
        got.append(kw)
        return {"status": "ok", "ladder": ["gpt-oss-120b|kel"], "params_version": 4,
                "recommended": {"p_success": 0.8, "p_success_q05": 0.7, "p_success_q95": 0.9, "expected_cost_usd": 0.01}}

    monkeypatch.setattr(plan_mod, "model_plan", model_plan)
    monkeypatch.setattr(plan_mod, "wants_plan", lambda c: True)
    q = "make the csv export handle quotes"
    plain = json.loads(_run(srv.find_ways(query=q, ctx=_Ctx(), use_llm=False, semantic=False, candidates=["a|b"])))
    assert "local_obs" not in got[0] and "routing_rows" not in plain
    routing = (FIX / "routing.md").read_text(encoding="utf-8")
    body = json.loads(_run(srv.find_ways(query=q, ctx=_Ctx(), use_llm=False, semantic=False, candidates=["a|b"],
                                         route_obs=routing)))
    assert got[1]["local_obs"] == [{"unit": "claude-sonnet-5-5|claude-code", "n": 1, "ok": 1},
                                   {"unit": "gpt-oss-120b|kel", "n": 5, "ok": 3}]
    assert body["routing_rows"][0].startswith(f"ROUTE|R-b00c1e|goal=-|g={GID}|fit=4|")   # the existing route is reused


# ============================================================ MCP surface text

def test_library_format_resource_carries_the_real_grammar():
    from app.mcp_server.resources import library_format_resource

    text = _run(library_format_resource())
    assert L.LIBRARY_HEADER in text and L.ROUTING_HEADER in text and L.IDX_HEADER in text
    assert "stealthlab-mcp library add" in text


def test_plan_and_run_reads_this_repos_library_first_and_writes_back():
    from app.mcp_server.prompts import plan_and_run

    text = plan_and_run("t")
    for needle in ("SUMMARY.md", "terms.idx", "stealthlab-mcp library payload", "library_matches",
                   "stealthlab-mcp library route --from-reply", "stealthlab-mcp library obs",
                   "stealthlab-mcp library add"):
        assert needle in text, needle
    assert text.index("library_matches") < text.index('"resolved"')     # local before global


def test_knowledge_layer_round_trips_and_matches_the_client():
    """linked.md is what the npm client writes after `library link` (shared fixture): Python parses and renders the
    same Goals (with parents), Ways and Steps byte for byte, and every passing entry is linked to them."""
    text = (FIX / "linked.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    lib = L.parse_library(text)
    assert lib.problems == []
    assert L.render_library(lib) == text
    assert len(lib.goals) >= 2 and len(lib.ways) >= 2
    goal_ids, way_ids = {g.id for g in lib.goals}, {w.id for w in lib.ways}
    for e in lib.entries:
        if e.outcome != "fail":
            assert e.goal in goal_ids and all(p.way in way_ids for p in e.procs)
    assert all(w.goal in goal_ids and w.steps for w in lib.ways)


def test_a_way_step_without_its_way_is_reported_not_invented():
    lib = L.parse_library(L.LIBRARY_HEADER + "S|W-0123abcd:1|action|do it|check=-\n")
    assert lib.ways == [] and any("orphan: W-0123abcd:1" in p for p in lib.problems)
