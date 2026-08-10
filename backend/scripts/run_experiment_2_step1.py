"""
Experiment 2, Step 1: before testing whether the postcondition gate
WORKS, first check whether the thing it's supposed to catch actually
HAPPENS on real data. Zero LLM calls -- pure embedding math against
already-ingested vectors, so this is cheap and fast to run before
committing to anything bigger.

The question: among the 129 real ingested AFTER task_nodes, how many
pairs from DIFFERENT roles cross FULL_MATCH_THRESHOLD (0.90, the real
production threshold from reuse_detection.py) on embedding similarity
alone? Every such pair is a genuine, real instance of the adversarial
pattern Hypothesis B worried about: same-looking task, different role,
would have been wrongly treated as reusable without the gate.

If this comes back with zero or very few real pairs, that's an honest,
important finding in itself -- it would mean the corpus doesn't
naturally produce the failure mode the synthetic test manufactured,
and Step 2/3 (does the gate correctly block/allow) would need
DELIBERATELY CONSTRUCTED adversarial pairs anchored in real task
content, not organically-occurring ones. Report whichever is true;
don't assume.

Run from backend/:
    python scripts/run_experiment_2_step1.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.precondition_gate import extract_postconditions, postconditions_compatible
from app.services.reuse_detection import FULL_MATCH_THRESHOLD


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def parse_pgvector(raw) -> np.ndarray:
    """asyncpg returns pgvector columns as a string like '[0.1,0.2,...]'
    unless a codec is registered -- handle both shapes rather than
    assume one, since this project has been bitten by JSONB/vector
    codec mismatches before (see TECHNICAL_DEEP_DIVE.md Section 7)."""
    if isinstance(raw, str):
        return np.array([float(x) for x in raw.strip("[]").split(",")])
    return np.array(raw)


async def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    rows = await pool.fetch(
        "SELECT id, name, embedding, success_criteria FROM task_nodes "
        "WHERE t_invalid IS NULL AND embedding IS NOT NULL "
        "AND success_criteria->'postconditions' IS NOT NULL"
    )
    await pool.close()

    print(f"found {len(rows)} real task_nodes with both an embedding and stated postconditions")
    if not rows:
        print("nothing to compare -- postconditions may not be populated on this data. "
              "Check ingest_after_skills.py actually ran, and against this same tenant.")
        return

    parsed = []
    for r in rows:
        tags = extract_postconditions(r["success_criteria"])
        roles = {t for t in (tags or []) if t.startswith("role:")}
        if not roles:
            continue
        parsed.append({
            "id": r["id"], "name": r["name"],
            "vec": parse_pgvector(r["embedding"]),
            "roles": roles,
        })
    print(f"{len(parsed)} of those have at least one role: tag (usable for this check)\n")

    cross_role_high_sim = []
    same_role_high_sim = []
    n_cross_role_pairs = 0
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            a, b = parsed[i], parsed[j]
            if a["roles"] & b["roles"]:
                continue  # same role (or overlapping roles) -- not the adversarial case
            n_cross_role_pairs += 1
            sim = cosine_sim(a["vec"], b["vec"])
            if sim >= FULL_MATCH_THRESHOLD:
                gate_blocks = not postconditions_compatible(list(a["roles"]), list(b["roles"]))
                cross_role_high_sim.append((a, b, sim, gate_blocks))

    print(f"=== cross-role pairs checked: {n_cross_role_pairs} ===")
    print(f"=== cross-role pairs at or above FULL_MATCH_THRESHOLD ({FULL_MATCH_THRESHOLD}): "
          f"{len(cross_role_high_sim)} ===\n")

    if not cross_role_high_sim:
        print("REAL FINDING: the adversarial pattern does not occur naturally in this corpus -- "
              "no cross-role pair reaches the real production full-match threshold on embedding "
              "similarity alone. This means the synthetic test's scenario, while a real structural "
              "risk, isn't something the current 129-task AFTER corpus actually exhibits. Step 2/3 "
              "would need deliberately constructed adversarial pairs anchored in real task content, "
              "not organic ones from this corpus.")
    else:
        blocked = sum(1 for *_, gate_blocks in cross_role_high_sim if gate_blocks)
        print(f"REAL FINDING: {len(cross_role_high_sim)} genuine cross-role high-similarity pair(s) "
              f"found. Gate would block {blocked}/{len(cross_role_high_sim)} of them.\n")
        for a, b, sim, gate_blocks in sorted(cross_role_high_sim, key=lambda x: -x[2])[:15]:
            status = "BLOCKED by gate" if gate_blocks else "NOT BLOCKED -- worth investigating"
            print(f"  sim={sim:.3f}  [{status}]")
            print(f"    A ({sorted(a['roles'])}): {a['name'][:80]}")
            print(f"    B ({sorted(b['roles'])}): {b['name'][:80]}")


if __name__ == "__main__":
    asyncio.run(main())
