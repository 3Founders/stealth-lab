"""
Experiment 2, Step 1b -- the corrected version of Step 1. Step 1 (see
run_experiment_2_step1.py) tested the wrong level of data: AFTER's 22
generic skill-library entries, which are deliberately broad and
role-agnostic by design, so finding zero cross-role collisions there
was expected and uninformative, not evidence the adversarial pattern
doesn't exist. index_after_tasks.py's own docstring says so directly:
"Real adversarial precondition differences (Experiment 2) live at the
TASK level, not the skill level."

This tests the real thing: among AFTER's 129 real task INSTRUCTIONS,
restricted to tasks whose skill tag is shared across 2+ different
roles (the real candidates index_after_tasks.py surfaced -- e.g.
"validation": de(5), ds(5), pm(4), swe(5)), do any cross-role pairs
actually share enough surface wording to cross FULL_MATCH_THRESHOLD
(0.90, the real production threshold) on embedding similarity alone?
Every such pair is a genuine, real instance of the failure mode
Hypothesis B worried about -- not constructed, found.

Restricted to the shared-skill subset deliberately, not all 129 tasks
pairwise -- a de task and a pm task with UNRELATED skills were never
going to be a same-surface-different-meaning risk in the first place;
the risk is specifically two tasks that got tagged with the same skill
by AFTER's own authors but come from different roles.

Run from backend/, after index_after_tasks.py has written
after_task_index.json:
    python scripts/run_experiment_2_step1b.py
"""
import asyncio
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from dotenv import load_dotenv
load_dotenv()

from huggingface_hub import hf_hub_download

from app.services.embeddings import Embedder
from app.services.precondition_gate import postconditions_compatible
from app.services.reuse_detection import FULL_MATCH_THRESHOLD

REPO = "DavydenkoGr/AFTER"


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


async def fetch_instructions(tasks: list[dict]) -> dict[str, str]:
    texts = {}
    for t in tasks:
        instr_path = t["path"].replace("task.toml", "instruction.md")
        try:
            local = hf_hub_download(REPO, filename=instr_path, repo_type="dataset")
            texts[t["id"]] = open(local, encoding="utf-8").read()
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: could not fetch instruction for {t['id']}: {exc}")
    return texts


async def main():
    index = json.load(open("after_task_index.json"))

    # Same grouping index_after_tasks.py already prints -- redo it here
    # so this script is self-contained and doesn't require re-parsing
    # its console output by hand.
    by_skill_role: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for t in index:
        for s in t.get("skills", []):
            by_skill_role[s][t["role"]].append(t)

    shared_skills = {s: roles for s, roles in by_skill_role.items() if len(roles) > 1}
    print(f"{len(shared_skills)} skill(s) shared across multiple roles")

    relevant_ids = {
        t["id"] for roles in shared_skills.values() for tasks in roles.values() for t in tasks
    }
    relevant_tasks = [t for t in index if t["id"] in relevant_ids]
    print(f"{len(relevant_tasks)} real tasks involved in a cross-role shared skill "
          f"(out of {len(index)} total) -- fetching instructions for these only\n")

    instructions = await fetch_instructions(relevant_tasks)
    relevant_tasks = [t for t in relevant_tasks if t["id"] in instructions]

    print(f"embedding {len(relevant_tasks)} real task instructions...")
    embedder = Embedder()
    vectors = await embedder.embed_batched(
        [instructions[t["id"]] for t in relevant_tasks], input_type="query",
    )
    vec_by_id = {t["id"]: np.array(v) for t, v in zip(relevant_tasks, vectors)}
    role_by_id = {t["id"]: t["role"] for t in relevant_tasks}

    print(f"embedded {len(vec_by_id)} tasks\n")

    cross_role_high_sim = []
    same_role_high_sim = []
    n_pairs_checked = 0
    n_same_role_pairs_checked = 0
    for skill, roles in shared_skills.items():
        ids = [t["id"] for tasks in roles.values() for t in tasks if t["id"] in vec_by_id]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                id_a, id_b = ids[i], ids[j]
                sim = cosine_sim(vec_by_id[id_a], vec_by_id[id_b])
                if role_by_id[id_a] == role_by_id[id_b]:
                    n_same_role_pairs_checked += 1
                    if sim >= FULL_MATCH_THRESHOLD:
                        role_tag = [f"role:{role_by_id[id_a]}"]
                        gate_passes = postconditions_compatible(role_tag, role_tag)
                        same_role_high_sim.append((skill, id_a, id_b, role_by_id[id_a], sim, gate_passes))
                    continue
                n_pairs_checked += 1
                if sim >= FULL_MATCH_THRESHOLD:
                    role_a, role_b = [f"role:{role_by_id[id_a]}"], [f"role:{role_by_id[id_b]}"]
                    gate_blocks = not postconditions_compatible(role_a, role_b)
                    cross_role_high_sim.append((skill, id_a, id_b, role_by_id[id_a], role_by_id[id_b], sim, gate_blocks))

    print(f"=== real cross-role, shared-skill pairs checked: {n_pairs_checked} ===")
    print(f"=== pairs at or above FULL_MATCH_THRESHOLD ({FULL_MATCH_THRESHOLD}): "
          f"{len(cross_role_high_sim)} ===\n")

    if not cross_role_high_sim:
        print("REAL FINDING: even restricted to real cross-role, shared-skill task pairs, none "
              "reach the production full-match threshold on embedding similarity alone. This is a "
              "much stronger negative result than Step 1's (that tested the wrong level) -- worth "
              "taking seriously as evidence the adversarial pattern, while structurally real, may "
              "not be common in practice on natural task-instruction text, even when two tasks "
              "share an explicit skill tag across roles.")
    else:
        blocked = sum(1 for *_, gate_blocks in cross_role_high_sim if gate_blocks)
        print(f"REAL FINDING: {len(cross_role_high_sim)} genuine cross-role pair(s) found. "
              f"Gate would block {blocked}/{len(cross_role_high_sim)}.\n")
        for skill, id_a, id_b, role_a, role_b, sim, gate_blocks in sorted(
            cross_role_high_sim, key=lambda x: -x[5]
        ):
            status = "BLOCKED by gate" if gate_blocks else "NOT BLOCKED -- investigate"
            print(f"  [{skill}] sim={sim:.3f}  [{status}]")
            print(f"    {role_a}: {id_a}")
            print(f"    {role_b}: {id_b}")

    print(f"\n=== SAME-role, shared-skill pairs checked (false-negative risk check): "
          f"{n_same_role_pairs_checked} ===")
    print(f"=== same-role pairs at or above FULL_MATCH_THRESHOLD: {len(same_role_high_sim)} ===")
    if same_role_high_sim:
        blocked_wrongly = sum(1 for *_, gate_passes in same_role_high_sim if not gate_passes)
        print(f"Gate wrongly blocked {blocked_wrongly}/{len(same_role_high_sim)} genuine same-role "
              f"matches -- {'A REAL PROBLEM if > 0' if blocked_wrongly else 'zero false negatives'}.\n")
        for skill, id_a, id_b, role, sim, gate_passes in sorted(same_role_high_sim, key=lambda x: -x[4]):
            status = "correctly passes" if gate_passes else "WRONGLY BLOCKED"
            print(f"  [{skill}] sim={sim:.3f}  role={role}  [{status}]: {id_a} vs {id_b}")
    else:
        print("no same-role pairs reached the threshold at all in this subset -- can't confirm "
              "zero false negatives from this data, only that none were exposed by it.")


if __name__ == "__main__":
    asyncio.run(main())
