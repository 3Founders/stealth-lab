"""
Applies a debate's proposed change_set to the real graph, using the
existing, already-built KnowledgeUpdater.apply() -- deliberately NOT a
new write path. This has never actually been called by any script in
this project; every prior debate run only proposed and logged.

MANDATORY pre-flight validation before ANY write: a real proposal
(debate_update_results.json) was found to write merged content under
the wrong property key ("statement" instead of the real "content"),
which -- because KnowledgeUpdater's merge REPLACES the whole properties
dict rather than deep-merging -- would have silently deleted the real
"postconditions" key and made the update permanently invisible to
retrieval. The root cause (nothing told the panel the real schema) is
fixed in knowledge_conflict.py; this script's validation is the second,
independent layer -- catches it even if a stale proposal (generated
before that fix) is applied, or if the fix has some gap not yet found.

Run from backend/:
    python scripts/synthetic_tasks/apply_debate_result.py [path_to_results.json]
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.models.change import ChangeSet
from app.services.knowledge_update import ChangeApplicationError, KnowledgeUpdater

HERE = Path(__file__).parent

REQUIRED_CONTENT_KEY = "content"
KEYS_THAT_MUST_BE_PRESERVED = ("postconditions",)


class ValidationError(Exception):
    pass


async def auto_preserve_missing_keys(pool, change_set_dict: dict) -> dict:
    """
    Fills in KEYS_THAT_MUST_BE_PRESERVED with the real current DB value,
    for any update_knowledge_node op that's missing them entirely --
    deterministically, in code, not by asking the LLM to remember to
    carry forward bookkeeping metadata it was never actually asked to
    reason about.

    Real motivation: after fixing the property-key bug, a second real
    run still dropped 'postconditions' -- but only on the op marking
    the losing node as superseded (a short pointer-comment), not on the
    main merge. That's exactly the shape of a mechanical bookkeeping
    gap, not a judgment failure -- "carry the metadata forward
    unchanged" doesn't need LLM reasoning any more than date-overlap
    arithmetic or content-diffing did.

    Only fills in keys that are ENTIRELY ABSENT from the proposal -- if
    the panel explicitly proposed a value for a preserved key (even an
    empty list), that's respected as a deliberate choice, not
    overridden. This mutates nothing the panel actually decided; it
    only prevents silent deletion of things it wasn't asked about.
    """
    for op in change_set_dict.get("ops", []):
        if op.get("op_type") != "update_knowledge_node":
            continue
        new_props = op.get("changes", {}).get("properties")
        if new_props is None:
            continue

        node_id = op["knowledge_node_id"]
        row = await pool.fetchrow(
            "SELECT properties FROM knowledge_nodes WHERE id = $1 AND t_invalid IS NULL", node_id,
        )
        if row is None:
            continue  # preflight_validate will report this as a real problem separately

        old_props = row["properties"] or {}
        for key in KEYS_THAT_MUST_BE_PRESERVED:
            if key in old_props and key not in new_props:
                new_props[key] = old_props[key]
                print(f"  auto-preserved '{key}' on {node_id} (was silently missing from "
                      f"the proposal, carried forward from the real current value unchanged)")
    return change_set_dict


async def preflight_validate(pool, change_set_dict: dict) -> list[str]:
    """
    Returns a list of problems found (empty = safe to apply). Checks,
    for every update_knowledge_node op:
      1. If it writes 'properties', does it use the real 'content' key
         (not an invented substitute like 'statement')?
      2. Does it silently drop any key the node currently has that
         isn't being deliberately addressed?
    This is deliberately independent of whatever the debate prompt was
    told -- it re-checks against the REAL current DB state, not the
    panel's own claims about it.
    """
    problems = []
    for op in change_set_dict.get("ops", []):
        if op.get("op_type") != "update_knowledge_node":
            continue
        node_id = op["knowledge_node_id"]
        new_props = op.get("changes", {}).get("properties")
        if new_props is None:
            continue  # not touching properties at all -- nothing to check

        row = await pool.fetchrow(
            "SELECT properties FROM knowledge_nodes WHERE id = $1 AND t_invalid IS NULL", node_id,
        )
        if row is None:
            problems.append(f"{node_id}: node not found or already superseded -- cannot apply")
            continue

        old_props = row["properties"] or {}
        looks_like_content_update = any(
            isinstance(v, str) and len(v) > 200 for v in new_props.values()
        )
        if looks_like_content_update and REQUIRED_CONTENT_KEY not in new_props:
            problems.append(
                f"{node_id}: proposed properties has a long text value but no "
                f"'{REQUIRED_CONTENT_KEY}' key (has: {sorted(new_props.keys())}) -- "
                f"this looks like the exact wrong-key bug already found once for real"
            )

        for key in KEYS_THAT_MUST_BE_PRESERVED:
            if key in old_props and key not in new_props:
                problems.append(
                    f"{node_id}: real existing key '{key}' would be SILENTLY DELETED "
                    f"(present in current node, absent from the proposed properties, and "
                    f"properties replacement is NOT a deep merge)"
                )
    return problems


async def main():
    results_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "debate_update_results.json"
    if not results_path.exists():
        print(f"{results_path} not found")
        return 1

    data = json.loads(results_path.read_text())
    change_set_dict = data["change_set"]

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        return 1

    pool = await create_pool(os.environ["DATABASE_URL"])

    print("=" * 70)
    print("AUTO-PRESERVE (deterministic bookkeeping, not an LLM decision)")
    print("=" * 70)
    change_set_dict = await auto_preserve_missing_keys(pool, change_set_dict)

    print("\n" + "=" * 70)
    print("PRE-FLIGHT VALIDATION (independent of what the panel claimed)")
    print("=" * 70)
    problems = await preflight_validate(pool, change_set_dict)
    if problems:
        print(f"\n{len(problems)} problem(s) found -- REFUSING to apply:")
        for p in problems:
            print(f"  - {p}")
        await pool.close()
        print("\nFix the change_set (or regenerate it now that the prompt tells the panel "
              "the real schema) before applying.")
        return 1
    print("No problems found -- safe to apply.")

    # Real, existing machinery -- ChangeSet.model_validate does the same
    # discriminated-union parsing every other real caller in this
    # codebase uses, not a bespoke parse for this script.
    change_set = ChangeSet.model_validate(change_set_dict)

    print("\n" + "=" * 70)
    print("APPLYING via the real KnowledgeUpdater")
    print("=" * 70)
    updater = KnowledgeUpdater(pool)
    try:
        applied = await updater.apply(change_set, approver_id="synthetic_task_experiment_apply")
    except ChangeApplicationError as exc:
        print(f"REFUSED by KnowledgeUpdater itself: {exc}")
        await pool.close()
        return 1

    for a in applied:
        print(f"  {a}")

    print("\n" + "=" * 70)
    print("VERIFYING the real, new state")
    print("=" * 70)
    for a in applied:
        if a["op"] == "update_knowledge_node":
            new_row = await pool.fetchrow(
                "SELECT name, properties FROM knowledge_nodes WHERE id = $1", UUID(a["new_id"]),
            )
            print(f"\nnew node {a['new_id']} (superseding {a['old_id']}):")
            print(f"  name: {new_row['name']}")
            print(f"  properties keys: {sorted(new_row['properties'].keys())}")
            content = new_row["properties"].get("content", "")
            print(f"  content ({len(content)} chars): {content[:200]}...")

    await pool.close()
    print("\nDone -- this was a real write to the graph, not a proposal.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
