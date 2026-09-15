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
    _build_implementations_page,
    _build_procedures_page,
    _build_run_page,
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
        {"node_order": 0, "status": "succeeded", "goal": "enumerate callers", "deps": [], "verification_state": "passed"},
        {"node_order": 1, "status": "running", "goal": "classify deps", "deps": [0], "verification_state": None},
    ],
    "parent_run_id": None, "root_run_id": "run-1",
}
_FAKE_PROC = {"name": "Do the thing", "goal": "do the thing",
              "preconditions": _FAKE_CONTEXT["required_preconditions"],
              "postconditions": ["the thing is done"]}
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
def test_build_procedures_page_index_brackets_the_block():
    md, rows = _build_procedures_page(_FAKE_CONTEXT, _FAKE_PROC, _FAKE_VERIF)
    assert len(rows) == 1
    r = rows[0]
    window = md.splitlines()[r.start - 1:r.end]
    assert window[0].startswith("## PROCEDURE proc-1 v3")
    assert any("enumerate callers" in ln for ln in window)   # steps rendered in the block
    assert r.summary  # non-empty


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


def test_build_run_page_row_per_node_plus_dep_edges():
    md, rows = _build_run_page(_FAKE_CONTEXT)
    node_rows = [r for r in rows if r.node_id.startswith("N")]
    assert [r.node_id for r in node_rows] == ["N0", "N1"]
    assert node_rows[1].deps == ("N0",)
    window = md.splitlines()[node_rows[1].start - 1:node_rows[1].end]
    assert window[0] == "## NODE N1"


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
