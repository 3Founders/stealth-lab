"""
Gate 2B: capture the FIRST real canonical candidate procedure --
"Lazy-load MCP tool schemas" (mcp-lazy-tool-schema-loading) -- through the
REAL capture_procedure() write path.

Honest about what this is NOT (same discipline as
seed_canonical_coding_procedures.py): this does NOT fabricate verification.
The row lands via capture_procedure() exactly as every other caller's does
-- verification_state='candidate' (schema default, "nothing is born
verified"), provenance + scope stamped per the V0 gate, the behavioral
contract carried in domain_payload (data, not code) so the runner's
behavioral validation gate (app/execution/behavioral_validation.py) can
enforce it on every real future execution. The procedure earns 'verified'
only through real reuse: record_execution_outcome() accruing real evidence
across distinct contexts.

Usage (from backend/):
    python scripts/capture_mcp_lazy_tool_schema_loading.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.session import create_pool
from app.services.embeddings import Embedder
from app.services.procedures import capture_procedure

DOMAIN = "coding"
PROVENANCE = "prior_library"  # curated canonical set -- same convention
# seed_canonical_coding_procedures.py established for this corpus.
CREATED_BY = "capture_mcp_lazy_tool_schema_loading"

NAME = "mcp-lazy-tool-schema-loading"

GOAL = (
    "Make an MCP-style tool server's default tool listing lazy-load tool "
    "schemas: tools/list returns lightweight metadata only (name, "
    "description), a targeted mechanism retrieves each tool's full JSON "
    "schema on demand, and ordinary tool invocation is unchanged"
)

STEPS = [
    {"order": 0, "goal": "Identify the tool-server module(s) that build the default tools/list payload and where each tool's full JSON schema is defined"},
    {"order": 1, "goal": "Change the default listing to emit lightweight metadata only (name, description) -- no full input schema per entry"},
    {"order": 2, "goal": "Expose or preserve a targeted retrieval mechanism (e.g. get_tool_schema(name)) that returns a tool's full declared JSON schema on demand"},
    {"order": 3, "goal": "Confirm ordinary tool invocation paths still work unchanged (same names, same argument handling, same results)"},
    {"order": 4, "goal": "Run the artifact import check and the behavioral contract checks: no schema keys in the default listing, targeted retrieval returns the declared schema, ordinary invocation succeeds"},
]

# The behavioral contract is DATA carried by the procedure itself, so the
# runner's composed success gate can enforce the procedure's claim on every
# real execution -- a run only counts as success when the produced artifact
# actually does what this procedure says it does.
BEHAVIORAL_CONTRACT = {
    "capability_kind": "lazy_tool_schema",
    "module": "tool_server.py",
    "expected_tools": {
        "echo": {
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    "tool_call": {"name": "echo", "args": {"text": "hello"},
                  "expect_contains": "hello"},
}

DOMAIN_PAYLOAD = {
    "canonical": True,
    "canonical_set": "gate-2b",
    "problem": (
        "MCP tool servers commonly embed every tool's full JSON schema in "
        "the default tools/list response. As tool counts grow, that payload "
        "dominates the agent's context window on every single listing call, "
        "even though any given run invokes a handful of tools at most."
    ),
    "solution": (
        "Default listing carries lightweight metadata only (name, "
        "description). A targeted retrieval mechanism (get_tool_schema) "
        "returns a tool's full JSON schema only when it is actually needed, "
        "and ordinary tool invocation is untouched."
    ),
    "assumptions": [
        "The tool server can be restructured to expose a per-tool schema "
        "retrieval entrypoint in addition to the default listing",
        "Tool names are stable identifiers usable as the targeted-retrieval key",
        "Agents using the server can tolerate a two-phase pattern (list "
        "cheaply, fetch schema only for the tool about to be invoked)",
    ],
    "applicability": [
        "Applies to MCP-style tool servers whose default listing embeds "
        "full per-tool JSON schemas",
        "Applies when token cost of the default listing is material for the "
        "consumer (agent context budgets, high-frequency listing)",
        "Does NOT apply to servers whose tools are so few, or schemas so "
        "small, that the listing payload is not a real cost",
    ],
    "behavioral_contract": BEHAVIORAL_CONTRACT,
    "relationships": {
        # Informal composable-primitive references, same convention
        # seed_canonical_coding_procedures.py uses (real subprocedure_ref
        # composition is a future schema change, not invented here).
        "prerequisites": ["locate_relevant_code", "identify_change_surface"],
    },
}

EXPECTED_EFFECTS = [
    "The default tool-listing payload no longer contains any full per-tool JSON schema",
    "A targeted full-schema retrieval mechanism exists and returns the declared schema for each advertised tool",
    "Ordinary tool invocation behaves exactly as before",
]

POSTCONDITIONS = [
    "The produced tool-server module imports cleanly",
    "The behavioral contract for capability kind lazy_tool_schema passes end to end",
]

FAILURE_CONDITIONS = [
    "The default listing still embeds any full-schema key (input_schema, "
    "inputSchema, parameters, input_schema_json) in any entry",
    "No targeted full-schema retrieval mechanism exists, or it returns a "
    "schema that does not match the declared one",
    "Ordinary tool invocation raises or returns different results than before the change",
]


async def capture(dry_run: bool = False) -> None:
    if dry_run:
        print(f"[dry-run] would capture: {NAME} ({len(STEPS)} steps)")
        print(f"[dry-run] behavioral contract kinds: "
              f"{BEHAVIORAL_CONTRACT['capability_kind']}")
        print("[dry-run] nothing written")
        return

    pool = await create_pool()
    try:
        # Real embedding at storage time -- the same convention
        # submit_procedure (app/mcp_server/server.py) established. WITHOUT
        # this the row can only ever surface as an unranked hard-filter
        # survivor in similarity search (the real rehearsal caught this:
        # the first capture had embedding=None and the runner matched a
        # different, embedded corpus procedure instead).
        embedding = await Embedder().embed_one(GOAL, input_type="document")
        result = await capture_procedure(
            pool,
            name=NAME,
            goal=GOAL,
            steps=STEPS,
            expected_effects=EXPECTED_EFFECTS,
            postconditions=POSTCONDITIONS,
            failure_conditions=FAILURE_CONDITIONS,
            provenance=PROVENANCE,
            domain=DOMAIN,
            domain_payload=DOMAIN_PAYLOAD,
            created_by=CREATED_BY,
            embedding=embedding,
            # scope_type="entity" for the same real reason
            # seed_canonical_coding_procedures.py documents: scope_type
            # "global" combined with the derived entity_id would trip the
            # V0 gate's correct "global scope cannot carry an entity_id"
            # rule; this procedure is scoped to the coding domain entity.
            scope_type="entity",
        )
        print(f"captured: {NAME}")
        print(f"  id           = {result['id']}")
        print(f"  procedure_id = {result['procedure_id']}")
        print("  verification_state = 'candidate' (schema default -- "
              "nothing fabricated as verified)")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be captured without touching the DB")
    args = parser.parse_args()
    asyncio.run(capture(dry_run=args.dry_run))
