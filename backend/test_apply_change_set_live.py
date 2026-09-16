"""
HISTORICAL MARKER -- this probe no longer runs.

`apply_change_set` was removed as a public MCP tool in the post-freeze
security hardening (v1-final-2026-09-03.1). It was the only ungated public
write to the knowledge graph: it applied an arbitrary caller-supplied
change_set with no approval gate, no persisted decision, and no audit row.

Graph mutation THEN went only through the two gated paths:
  - submit_approval  -> app/api/approval.py::decide  (a PENDING_APPROVAL
    debate scorecard's STORED change_set + an `approvals` audit row)
  - decide_decomposition -> app/api/decompose.py::decide  (a
    status='proposed' decompositions row's STORED change_set + a
    status/approver/decided_at update)

2026-09-16 update: both of THOSE were themselves removed from the MCP
surface (see app/mcp_server/server.py's own top docstring) -- no MCP tool
mutates the knowledge graph today. The underlying app/api/approval.py /
app/api/decompose.py modules are untouched and still real over HTTP.

The file is kept (not deleted) so backend/tests/test_live_scripts_not_
collected.py's expectations do not shift.
"""

if __name__ == "__main__":
    raise SystemExit(
        "apply_change_set was removed as a public MCP tool in the post-freeze "
        "security hardening (v1-final-2026-09-03.1). submit_approval / "
        "decide_decomposition (its successors) were themselves removed from "
        "the MCP surface 2026-09-16. No MCP tool mutates the knowledge graph "
        "today. This probe is retained as a historical marker."
    )
