"""
G13 P1 -- pure-logic tests for the `.stealth/` file formats
(`app.stealth.format`) and the run-scoped page builders
all from fixture dicts, no database.

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


def _run(coro):
    return asyncio.run(coro)


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


# ------------------------------------------------------------- T11 budget


# ------------------------------------------------------------- index.md


# ------------------------------------------------------------- goals.md


