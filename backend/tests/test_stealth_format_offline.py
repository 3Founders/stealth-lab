"""
G13 P1 -- pure-logic tests for the `.stealth/` file formats
(`app.stealth.format`) and the run-scoped page builders
(`app.stealth.generator._build_*`), all from fixture dicts, no database.

Covers the T11 navigation contract at the format layer:
  * an index row's (file, start, end) resolves to exactly the block it
    names -- `sed -n 'start,end p' file` gives you that object and only
    that object;
  * the root router stays within its byte budget even when the working
    set is large;
  * `|` / newlines in any field can never break the single-`split`
    parser or inject a second row.
"""
from __future__ import annotations

import asyncio
import json

from app.stealth.format import (
    IDX_SEP,
    ROOT_IDX_MAX_BYTES,
    IdxRow,
    MdBlock,
    RootRow,
    RunIdxRow,
    kv,
    parse_idx,
    render_idx,
    render_md_page,
    render_root_idx,
)
from app.stealth.generator import (
    _build_claims_page,
    _build_goals_page,
    _build_implementations_page,
    _build_procedures_page,
    _build_run_page,
    _gather_index_groups,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeClaimsPool:
    """Answers exactly the one query `_build_claims_page` issues:
    `SELECT id, properties, scope_type, t_invalid FROM knowledge_nodes
    WHERE id = ANY($1::uuid[]) AND node_type = 'claim'`."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    async def fetch(self, sql, *params):
        ids = {str(i) for i in params[0]}
        return [r for r in self._rows if str(r["id"]) in ids]

_FAKE_CONTEXT = {
    "procedure_run_id": "run-1", "procedure_id": "proc-1", "procedure_version": 3,
    "status": "pending", "current_phase_or_node": "node:0", "objective": "do the thing",
    "scope_type": "global",
    "required_preconditions": [
        {"subject": "project:1", "predicate": "lang", "object": "python", "status": "UNKNOWN"},
        {"subject": "db:1", "predicate": "engine", "object": "postgres", "status": "TRUE"},
    ],
    "recommended_implementations": [
        {"implementation_id": "impl-1", "role": "primary", "source": "procedure_implementation_binding"},
    ],
    "blocking_unknowns": [], "waiting_child": None,
    "nodes": [
        {"id": "aaaaaaaa-0000-4000-8000-000000000000", "node_order": 0, "status": "succeeded",
         "goal": "enumerate callers", "deps": [], "verification_state": "passed", "implementation_id": None},
        {"id": "bbbbbbbb-0000-4000-8000-000000000000", "node_order": 1, "status": "running",
         "goal": "classify deps", "deps": [0], "verification_state": None, "implementation_id": None},
    ],
    "parent_run_id": None, "root_run_id": "run-1",
}
_FAKE_PROC = {
    "name": "Do the thing", "goal": "do the thing",
    "verification_state": "verified", "domain": "testing", "scope_type": "global",
    "preconditions": _FAKE_CONTEXT["required_preconditions"],
    "postconditions": ["the thing is done"],
    "steps": [
        {"order": 0, "goal": "enumerate callers", "action": "run rg across the repo"},
        {"order": 1, "goal": "classify deps", "action": "read each caller",
         "verification": "every caller is classified"},
    ],
}
_FAKE_VERIF = {"overall_state": "inconclusive",
               "criteria": [{"criterion_id": "postcondition:0", "state": "inconclusive"}]}


# ---------------------------------------------------------------- codecs
def test_idx_row_round_trips_through_parse():
    row = IdxRow(obj_id="G-1", version="v4", scope="global", status="SUPPORTED",
                 tags=("migration", "compat"), file="claims.md", start=10, end=25,
                 summary="mixed-version compatibility")
    parsed = parse_idx(render_idx([row], header="h"))
    assert parsed == [["G-1", "v4", "global", "SUPPORTED", "migration,compat",
                       "claims.md", "10", "25", "mixed-version compatibility"]]


def test_pipe_and_newline_in_fields_cannot_forge_a_row():
    row = IdxRow(obj_id="G-1", version="-", scope="-", status="-",
                 tags=("a|b", "c\nd"), file="claims.md", start=1, end=2,
                 summary="line one\nDROP TABLE|evil")
    out = render_idx([row], header="h")
    body = [ln for ln in out.splitlines() if not ln.startswith("#")]
    assert len(body) == 1                      # exactly one row, no injected second line
    assert parse_idx(out)[0].__len__() == 9    # still 9 fields, not more


def test_root_idx_within_budget_and_parseable():
    rows = [RootRow("claims", "claims.idx", "facts"), RootRow("run", "run.idx", "current work")]
    text = render_root_idx(rows)
    assert len(text.encode("utf-8")) <= ROOT_IDX_MAX_BYTES
    assert parse_idx(text) == [["claims", "claims.idx", "facts"], ["run", "run.idx", "current work"]]


def test_run_idx_row_shape():
    r = RunIdxRow(node_id="N2", status="RUNNING", owner="agent-B", deps=("N1",),
                  write_globs=("src/payments/**",), file="run.md", start=36, end=54,
                  summary="classify semantic dependency")
    fields = r.render().split(IDX_SEP)
    assert fields[0] == "N2" and fields[1] == "RUNNING" and fields[2] == "agent-B"
    assert fields[3] == "N1" and fields[4] == "src/payments/**"


# ------------------------------------------------------- md page ranges
def test_render_md_page_ranges_point_at_the_named_block():
    blocks = [
        MdBlock(obj_id="A", heading="CLAIM A", body=[kv("statement", "a is a"), kv("status", "OK")]),
        MdBlock(obj_id="B", heading="CLAIM B", body=[kv("statement", "b is b")]),
    ]
    page = render_md_page("claims.md", blocks)
    lines = page.text.splitlines()
    for oid in ("A", "B"):
        start, end = page.ranges[oid]
        window = lines[start - 1:end]              # 1-based inclusive -> slice
        assert window[0] == f"## CLAIM {oid}"
        assert all(f"## CLAIM {'B' if oid == 'A' else 'A'}" not in ln for ln in window)


# ---------------------------------------------------- run-scoped builders
class _FakeProceduresPool:
    """Answers `_build_procedures_page`'s extra-ids query: SELECT * FROM
    procedures WHERE procedure_id = ANY($1::uuid[]) AND t_invalid IS NULL."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    async def fetch(self, sql, *params):
        ids = {str(i) for i in params[0]}
        return [r for r in self._rows if str(r["procedure_id"]) in ids]


def test_build_procedures_page_pipe_grammar_and_index_brackets_the_block():
    md, rows = _run(_build_procedures_page(_FakeProceduresPool([]), _FAKE_CONTEXT, _FAKE_PROC))
    assert len(rows) == 1
    r = rows[0]
    window = md.splitlines()[r.start - 1:r.end]
    assert window[0] == "PROCEDURE|proc-1|verified|testing|global|Do the thing|version=3"
    assert any(ln.startswith("STEP|proc-1|S0|0|enumerate callers|") for ln in window)
    assert any(ln.startswith("STEP|proc-1|S1|1|classify deps|") and "deps=S0" in ln for ln in window)
    assert any(ln.startswith("VERIFY_REQ|proc-1|S1|") and "every caller is classified" in ln for ln in window)
    assert r.summary  # non-empty


def test_build_procedures_page_merges_faulted_extra_procedures():
    extra_pool = _FakeProceduresPool([
        {"procedure_id": "proc-2", "version": 1, "name": "Other thing", "goal": "other",
         "verification_state": "candidate", "domain": "-", "scope_type": "global", "steps": []},
    ])
    md, rows = _run(_build_procedures_page(extra_pool, _FAKE_CONTEXT, _FAKE_PROC, extra_ids=("proc-2",)))
    ids = {r.obj_id for r in rows}
    assert ids == {"proc-1", "proc-2"}
    lines = [ln for ln in md.splitlines() if ln.startswith("PROCEDURE|")]
    assert len(lines) == 2
    # each row's range brackets only its own block
    for r in rows:
        window = md.splitlines()[r.start - 1:r.end]
        assert window[0].startswith(f"PROCEDURE|{r.obj_id}|")
        other = "proc-2" if r.obj_id == "proc-1" else "proc-1"
        assert not any(f"PROCEDURE|{other}|" in ln for ln in window)


def test_build_claims_page_one_pipe_row_per_faulted_global_claim():
    global_claims = (
        MdBlock(obj_id="c1111111-0000-4000-8000-000000000001", heading="CLAIM c1 (global)"),
        MdBlock(obj_id="c2222222-0000-4000-8000-000000000002", heading="CLAIM c2 (global)"),
    )
    pool = _FakeClaimsPool([
        {"id": "c1111111-0000-4000-8000-000000000001",
         "properties": json.dumps({"statement": "auth lives in src/auth", "claim_status": "ACTIVE",
                                    "claim_type": "fact", "source_id": "AGENTS.md"}),
         "scope_type": "repo", "t_invalid": None},
        {"id": "c2222222-0000-4000-8000-000000000002",
         "properties": json.dumps({"statement": "generated code comes from schema/api.yaml", "claim_status": "ACTIVE",
                                    "claim_type": "invariant", "source_id": "CLAUDE.md"}),
         "scope_type": "repo", "t_invalid": None},
    ])
    md, rows = _run(_build_claims_page(pool, global_claims))
    assert len(rows) == 2
    assert {r.obj_id for r in rows} == {b.obj_id for b in global_claims}
    lines = [ln for ln in md.splitlines() if ln.startswith("CLAIM|")]
    assert len(lines) == 2
    assert any("auth lives in src/auth" in ln and "source=AGENTS.md" in ln for ln in lines)
    for r in rows:
        head = md.splitlines()[r.start - 1]
        assert r.obj_id in head
        assert r.start == r.end  # one pipe-delimited record is exactly one line


def test_build_claims_page_no_faulted_claims_is_honest_not_fabricated():
    md, rows = _run(_build_claims_page(_FakeClaimsPool([]), ()))
    assert rows == []
    assert "not canonical" in md and "(no claims)" in md


def test_build_implementations_page_missing_is_flagged():
    ctx = dict(_FAKE_CONTEXT, recommended_implementations=[])
    md, rows = _build_implementations_page(ctx)
    assert rows == []
    assert "MISSING_IMPLEMENTATION, not fabricated" in md


class _FakeRunPagePool:
    """Answers `_build_run_page`'s batched queries: implementations
    (kind by id), goals (by normalized_name), verification_results (by
    execution_run_node_id), run_collaboration_records (by
    execution_run_id -- empty by default, matching "no collaboration
    records on this run yet" as the common offline case)."""

    def __init__(self, impl_rows=(), goal_rows=(), verify_rows=(), collab_rows=()):
        self._impl_rows = list(impl_rows)
        self._goal_rows = list(goal_rows)
        self._verify_rows = list(verify_rows)
        self._collab_rows = list(collab_rows)

    async def fetch(self, sql, *params):
        n = " ".join(sql.split())
        if "FROM implementations" in n:
            ids = {str(i) for i in params[0]}
            return [r for r in self._impl_rows if str(r["id"]) in ids]
        if "FROM goals" in n:
            names = set(params[0])
            return [r for r in self._goal_rows if r["normalized_name"] in names]
        if "FROM verification_results" in n:
            ids = {str(i) for i in params[0]}
            return [r for r in self._verify_rows if str(r["execution_run_node_id"]) in ids]
        if "FROM run_collaboration_records" in n:
            return list(self._collab_rows)
        raise AssertionError(f"unexpected fetch: {n[:80]}")


def test_build_run_page_row_per_node_plus_dep_edges():
    md, rows = _run(_build_run_page(_FakeRunPagePool(), _FAKE_CONTEXT, _FAKE_PROC))
    node_rows = [r for r in rows if r.node_id.startswith("N")]
    assert [r.node_id for r in node_rows] == ["N0", "N1"]
    assert node_rows[1].deps == ("N0",)
    window = md.splitlines()[node_rows[1].start - 1:node_rows[1].end]
    assert window[0].startswith("NODE|N1|running|classify deps|goal=-|step=proc-1:S1|impl=-|executor=frontier|deps=N0")


def test_build_run_page_resolves_real_goal_and_executor(monkeypatch):
    pool = _FakeRunPagePool(
        impl_rows=[{"id": "iiiiiiii-0000-4000-8000-000000000000", "kind": "deterministic"}],
        goal_rows=[{"id": "gggggggg-0000-4000-8000-000000000000", "normalized_name": "enumerate callers",
                    "canonical_name": "Enumerate callers", "expected_outcome": "a full caller list", "scope_type": "global"}],
        verify_rows=[{"execution_run_node_id": "aaaaaaaa-0000-4000-8000-000000000000",
                      "criterion_id": "step:0:verification", "state": "verified",
                      "method": "deterministic_check", "statement": "all callers found", "evidence_refs": ["E-1"]}],
    )
    ctx = dict(_FAKE_CONTEXT)
    ctx["nodes"] = [
        dict(_FAKE_CONTEXT["nodes"][0], implementation_id="iiiiiiii-0000-4000-8000-000000000000"),
        _FAKE_CONTEXT["nodes"][1],
    ]
    md, rows = _run(_build_run_page(pool, ctx, _FAKE_PROC))
    lines = md.splitlines()
    node0 = next(ln for ln in lines if ln.startswith("NODE|N0|"))
    assert "goal=gggggggg-0000-4000-8000-000000000000" in node0
    assert "impl=iiiiiiii-0000-4000-8000-000000000000" in node0
    assert "executor=deterministic" in node0
    assert "GOAL|N0|gggggggg-0000-4000-8000-000000000000|Enumerate callers" in lines
    assert any(ln.startswith("VERIFY|N0|step:0:verification|verified|deterministic_check|all callers found")
               for ln in lines)


# ------------------------------------------------------------- T11 budget
def test_root_router_stays_bounded_even_with_a_large_working_set():
    # hundreds of faulted-in global claims -> claims.idx grows, but the
    # ROOT router an agent greps first is still tiny and each object
    # stays reachable by its own exact line (pipe format: one record per
    # line, so start == end for every row) without reading the whole page.
    ids = [f"c{i:08d}-0000-4000-8000-{i:012d}" for i in range(400)]
    global_claims = tuple(MdBlock(obj_id=cid, heading=f"CLAIM {cid}") for cid in ids)
    pool = _FakeClaimsPool([
        {"id": cid, "properties": json.dumps({"statement": f"claim number {i}", "claim_status": "ACTIVE"}),
         "scope_type": "repo", "t_invalid": None}
        for i, cid in enumerate(ids)
    ])
    md, rows = _run(_build_claims_page(pool, global_claims))
    assert len(rows) == 400
    # distinct, non-overlapping ranges (each is a single line: start == end)
    spans = sorted((r.start, r.end) for r in rows)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 < s2
    # a mid-corpus object is found by grep on the index alone
    target = rows[200]
    head = md.splitlines()[target.start - 1]
    assert target.obj_id in head

    root = render_root_idx([RootRow("claims", "claims.idx", "x"), RootRow("run", "run.idx", "y")])
    assert len(root.encode("utf-8")) <= ROOT_IDX_MAX_BYTES


# ------------------------------------------------------------- index.md


class _FakeImplGoalsPool:
    """Answers `_gather_index_groups`'s two queries: implementations (by
    id) and goals (by normalized_name, the real `goals.py::
    normalize_goal_name` key)."""

    def __init__(self, impl_rows: list[dict] = (), goal_rows: list[dict] = ()):
        self._impl_rows = list(impl_rows)
        self._goal_rows = list(goal_rows)

    async def fetch(self, sql, *params):
        n = " ".join(sql.split())
        if "FROM implementations" in n:
            ids = {str(i) for i in params[0]}
            return [r for r in self._impl_rows if str(r["id"]) in ids]
        if "FROM goals" in n:
            names = set(params[0])
            return [r for r in self._goal_rows if r["normalized_name"] in names]
        raise AssertionError(f"unexpected fetch: {n[:80]}")


def test_gather_index_groups_claim_and_procedure_groups_reuse_idx_tags():
    claims_rows = [
        IdxRow(obj_id="C-1", version="1", scope="repo", status="ACTIVE", tags=("fact",),
               file="claims.md", start=1, end=1, summary="s"),
        IdxRow(obj_id="C-2", version="1", scope="repo", status="ACTIVE", tags=("invariant",),
               file="claims.md", start=2, end=2, summary="s"),
    ]
    procedures_rows = [
        IdxRow(obj_id="P-1", version="1", scope="global", status="verified", tags=("testing",),
               file="procedures.md", start=1, end=3, summary="s"),
    ]
    claim_groups, procedure_groups, implementation_groups, goal_groups, run_states, _ = _run(_gather_index_groups(
        _FakeImplGoalsPool(), context={"nodes": []}, claims_rows=claims_rows,
        procedures_rows=procedures_rows, recommended_implementations=[],
    ))
    assert {g.topic: g.ids for g in claim_groups} == {"fact": ["C-1"], "invariant": ["C-2"]}
    assert {g.topic: g.ids for g in procedure_groups} == {"testing": ["P-1"]}
    assert implementation_groups == []
    assert goal_groups == []
    assert {s.state: s.node_ids for s in run_states} == {"READY": [], "RUNNING": [], "BLOCKED": [], "DONE": []}


def test_gather_index_groups_implementation_groups_by_real_goal_column():
    pool = _FakeImplGoalsPool(impl_rows=[
        {"id": "I-1", "goal": "verification"},
        {"id": "I-2", "goal": "verification"},
        {"id": "I-3", "goal": None},
    ])
    _, _, implementation_groups, _, _, _ = _run(_gather_index_groups(
        pool, context={"nodes": []}, claims_rows=[], procedures_rows=[],
        recommended_implementations=[
            {"implementation_id": "I-1"}, {"implementation_id": "I-2"}, {"implementation_id": "I-3"},
        ],
    ))
    by_topic = {g.topic: sorted(g.ids) for g in implementation_groups}
    assert by_topic == {"verification": ["I-1", "I-2"], "-": ["I-3"]}


def test_gather_index_groups_goal_groups_by_real_tags_column():
    pool = _FakeImplGoalsPool(goal_rows=[
        {"id": "G-1", "normalized_name": "find references", "canonical_name": "Find references",
         "expected_outcome": None, "scope_type": "global", "tags": ["reference-search"]},
    ])
    context = {"nodes": [{"node_order": 0, "status": "running", "goal": "find references"}]}
    _, _, _, goal_groups, _, _ = _run(_gather_index_groups(
        pool, context=context, claims_rows=[], procedures_rows=[], recommended_implementations=[],
    ))
    assert {g.topic: g.ids for g in goal_groups} == {"reference-search": ["G-1"]}


def test_gather_index_groups_buckets_nodes_by_real_status():
    context = {"nodes": [
        {"node_order": 0, "status": "succeeded"},
        {"node_order": 1, "status": "running"},
        {"node_order": 2, "status": "pending"},
        {"node_order": 3, "status": "blocked"},
        {"node_order": 4, "status": "resumable"},
        {"node_order": 5, "status": "failed"},
    ]}
    _, _, _, _, run_states, _ = _run(_gather_index_groups(
        _FakeImplGoalsPool(), context=context, claims_rows=[], procedures_rows=[],
        recommended_implementations=[],
    ))
    by_state = {s.state: s.node_ids for s in run_states}
    assert by_state["DONE"] == ["N0", "N5"]
    assert by_state["RUNNING"] == ["N1"]
    assert by_state["READY"] == ["N2", "N4"]
    assert by_state["BLOCKED"] == ["N3"]


def test_index_md_end_to_end_real_grammar():
    from app.stealth.pipe_format import render_index_md

    pool = _FakeImplGoalsPool(
        impl_rows=[{"id": "I-1", "goal": "verification"}],
        goal_rows=[{"id": "G-1", "normalized_name": "find references", "canonical_name": "Find references",
                    "expected_outcome": None, "scope_type": "global", "tags": ["reference-search"]}],
    )
    claim_groups, procedure_groups, implementation_groups, goal_groups, run_states, _ = _run(_gather_index_groups(
        pool,
        context={"nodes": [{"node_order": 0, "status": "running", "goal": "find references"}]},
        claims_rows=[IdxRow(obj_id="C-1", version="1", scope="repo", status="ACTIVE", tags=("fact",),
                             file="claims.md", start=1, end=1, summary="s")],
        procedures_rows=[],
        recommended_implementations=[{"implementation_id": "I-1"}],
    ))
    md = render_index_md(
        repo="StealthLab", revision=42, active_run="R-1",
        claim_groups=claim_groups, procedure_groups=procedure_groups,
        implementation_groups=implementation_groups, goal_groups=goal_groups, run_states=run_states,
    )
    lines = md.splitlines()
    assert "REPO|StealthLab" in lines
    assert "REVISION|42" in lines
    assert "ACTIVE_RUN|R-1" in lines
    assert "CLAIM_GROUP|fact|C-1" in lines
    assert "GOAL_GROUP|reference-search|G-1" in lines
    assert "IMPLEMENTATION_GROUP|verification|I-1" in lines
    assert "RUN_STATE|RUNNING|N0" in lines


# ------------------------------------------------------------- goals.md


def test_build_goals_page_renders_and_indexes_resolved_goal_rows():
    goal_rows = {
        "find references": {
            "id": "gggggggg-0000-4000-8000-000000000000", "status": "active", "scope_type": "global",
            "canonical_name": "Find references", "version": 3, "expected_outcome": "a caller list",
            "verification_requirement": "manual review", "aliases": ["find usages"],
        },
    }
    md, rows = _run(_build_goals_page(goal_rows))
    lines = md.splitlines()
    assert "GOAL|gggggggg-0000-4000-8000-000000000000|active|global|Find references|version=3" in lines
    assert any(ln.startswith("GOAL_DETAIL|gggggggg-0000-4000-8000-000000000000|") for ln in lines)
    assert any(ln.startswith("ALIASES|gggggggg-0000-4000-8000-000000000000|") for ln in lines)
    assert len(rows) == 1
    r = rows[0]
    window = md.splitlines()[r.start - 1:r.end]
    assert window[0].startswith("GOAL|gggggggg-0000-4000-8000-000000000000|")


def test_build_goals_page_empty_is_honest():
    md, rows = _run(_build_goals_page({}))
    assert rows == []
    assert "(no goals)" in md
