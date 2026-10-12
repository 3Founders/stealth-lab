"""
MCP hardening B32 STRICT CLOSURE: "Required public operations (reuse
equivalent existing names where they already exist)" -- the literal 9
named operations. The final adversarial audit found this checked only
informally; two of the 9 literal names (`inspect_procedure`,
`get_run_context`) do not exist verbatim as real @server.tool()
registrations, which needed to be resolved against B32's own explicit
escape hatch ("reuse equivalent existing names") rather than assumed.

Investigation (not a fabricated equivalence -- traced against the real
code):
  - `inspect_procedure` -> `get_procedure` (app/mcp_server/server.py):
    same real capability, an already-existing name reused, permitted
    verbatim by B32's own text.
  - `get_run_context` -> `continue_run`: NOT a separate tool at all --
    `continue_run`'s own real response IS `durable_resume.get_run_
    context`'s full dict (server.py: `context = await _dres.
    get_run_context(...); return json.dumps(context, ...)`), so the
    "operation" already exists, just folded into `continue_run` rather
    than exposed as its own tool -- a legitimate consolidation, not a
    missing capability.

This file pins that mapping, and pins the CURRENT surface, in a real,
automated, offline test (no DATABASE_URL needed -- pure
tool-registration introspection).

**STATUS CHANGE -- this file's original assertion is obsolete.** The v2
MCP tool surface was deliberately REMOVED (commit cb52606, "ship:
remove the v2 MCP surface -- the server is the five v1 tools, two
prompts and their resources"; see also the notice at the top of
backend/README_MCP_SERVER.md). Seven of the nine B32 operations --
find_best_way, continue_run, get_procedure, get_relevant_claims,
verify_completion, report_execution, submit_procedure -- were on that
removed surface and are NOT registered MCP tools today. They are not
"missing"; they were removed by an explicit founder decision.

The original assertion (`srv.server.list_tools()` must contain all 9)
therefore cannot pass, and had been failing unnoticed. Note it was also
reading the WRONG source: `server.list_tools` is monkeypatched to
`_list_tools_progressively` (server.py), which returns only the
progressive-discovery core, so it reported tools as absent that were
still registered. This file now introspects the real registry,
`server._tool_manager.list_tools()`, and asserts two things: the v1
surface is exactly the 9 intended tools, and the 7 B32 v2 operations
remain deliberately absent -- so re-adding any of them has to be a
conscious edit here, not a silent regression.

Deliberately NOT enforcing "hide backend graph mechanics" as a hard
tool-count cap: B32's own text uses "SHOULD" for the minimal-surface
half (not MUST), and this server's ~30 additional tools beyond the 9
named here each map to a separately-tracked, real capability required
by another B-item (B6 host-executed lease, B34 human review packets,
B36 file-intent coordination, B9-B13 recursive execution introspection,
etc.) -- removing them to hit an exact count would regress those items
for the sake of a soft requirement. Documented here rather than
silently ignored.
"""
from __future__ import annotations

import app.mcp_server.server as srv

# The literal B32 list, plus this repo's own real, already-existing
# equivalent name for each of the two that are not registered verbatim.
REQUIRED_OPERATIONS: dict[str, str] = {
    "find_best_way": "find_best_way",
    "continue_run": "continue_run",
    "inspect_procedure": "get_procedure",
    "get_relevant_claims": "get_relevant_claims",
    "get_run_context": "continue_run",
    "verify_completion": "verify_completion",
    "report_execution": "report_execution",
    "submit_procedure": "submit_procedure",
}


# The v1 MCP surface as it exists today, after cb52606 removed the v2 tools.
# Introspect the REAL registry, not `server.list_tools`: the latter is
# monkeypatched to `_list_tools_progressively`, which returns only the
# progressive-discovery core (CORE_TOOLS) and would make every non-core tool
# look unregistered.
EXPECTED_V1_SURFACE: frozenset[str] = frozenset({
    "find_ways", "submit_way", "report_discovery", "recommend_models",
    "report_model_run", "report_result", "call_model", "discover_tools", "use_tool",
})

# The B32 operations whose only implementation lived on the removed v2 surface.
# They must stay absent unless a conscious decision re-adds them.
REMOVED_V2_OPERATIONS: frozenset[str] = frozenset({
    "find_best_way", "continue_run", "get_procedure", "get_relevant_claims",
    "verify_completion", "report_execution", "submit_procedure",
})


def _registered_tools() -> set[str]:
    return {t.name for t in srv.server._tool_manager.list_tools()}


def test_b32_operations_on_the_removed_v2_surface_are_deliberately_absent():
    """The 7 B32 operations carried by the deleted v2 surface must NOT be
    registered. If one reappears, that is a surface change to review, not an
    accident -- this test fails so the decision is made explicitly here."""
    registered = _registered_tools()
    resurrected = sorted(REMOVED_V2_OPERATIONS & registered)
    assert not resurrected, (
        f"B32 v2 operation(s) are registered again: {resurrected}. The v2 MCP "
        f"surface was removed by decision (cb52606) -- if that is being "
        f"reverted, update this test and backend/README_MCP_SERVER.md in the "
        f"same change."
    )


def test_the_v1_surface_is_exactly_the_intended_nine_tools():
    """Pins the live surface. `server.list_tools` is progressive and filtered,
    so a client-visible listing cannot be used to assert the registry -- read
    the registry directly."""
    registered = _registered_tools()
    assert registered == EXPECTED_V1_SURFACE, (
        f"MCP surface drifted from the intended nine: "
        f"unexpected={sorted(registered - EXPECTED_V1_SURFACE)} "
        f"missing={sorted(EXPECTED_V1_SURFACE - registered)}"
    )


def test_progressive_listing_shows_only_the_core_and_keeps_the_rest_callable():
    """The registry is the nine; what a client is LISTED is the smaller
    progressive core, and every deferred tool stays reachable by name."""
    import asyncio

    listed = {t.name for t in asyncio.run(srv.server.list_tools())}
    assert listed <= srv.CORE_TOOLS, (
        f"tools/list must stay within CORE_TOOLS, got extra: {sorted(listed - srv.CORE_TOOLS)}"
    )
    # every v1 tool not listed is still registered (callable by name / use_tool)
    assert EXPECTED_V1_SURFACE - listed <= _registered_tools()


def test_b32_named_operations_that_differ_from_the_literal_name_are_explicitly_tracked():
    """Guards against silently drifting further from the literal names
    without a conscious decision -- if a future rename makes MORE of the
    9 diverge from their literal spelling, this test forces that to be
    a deliberate, reviewed change to REQUIRED_OPERATIONS above, not an
    unnoticed side effect."""
    diverged = {k: v for k, v in REQUIRED_OPERATIONS.items() if k != v}
    assert diverged == {"inspect_procedure": "get_procedure", "get_run_context": "continue_run"}
