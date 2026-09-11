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

This file pins that mapping in a real, automated, offline test (no
DATABASE_URL needed -- pure tool-registration introspection via the
real MCPServer.list_tools()), so a future accidental rename/removal of
any of the 9 required operations (or their real equivalent) is caught
immediately.

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

import asyncio

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
    "submit_implementation": "submit_implementation",
}


def test_every_b32_required_operation_has_a_real_registered_tool():
    async def _run():
        tools = await srv.server.list_tools()
        registered = {t.name for t in tools}
        missing = [
            (literal_name, real_name) for literal_name, real_name in REQUIRED_OPERATIONS.items()
            if real_name not in registered
        ]
        assert not missing, (
            f"B32 required operation(s) have no real registered tool: {missing} "
            f"(registered tools: {sorted(registered)})"
        )

    asyncio.run(_run())


def test_b32_named_operations_that_differ_from_the_literal_name_are_explicitly_tracked():
    """Guards against silently drifting further from the literal names
    without a conscious decision -- if a future rename makes MORE of the
    9 diverge from their literal spelling, this test forces that to be
    a deliberate, reviewed change to REQUIRED_OPERATIONS above, not an
    unnoticed side effect."""
    diverged = {k: v for k, v in REQUIRED_OPERATIONS.items() if k != v}
    assert diverged == {"inspect_procedure": "get_procedure", "get_run_context": "continue_run"}
