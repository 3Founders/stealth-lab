"""
Index every AFTER task's task.toml -- no ingestion, just a local report
so you can pick good held-out (Experiment 1) and adversarial cross-role
(Experiment 2) candidates without downloading everything by hand.

Run from backend/:
    python scripts/index_after_tasks.py

Writes after_task_index.json (role, skills, difficulty per task) and
prints two things directly useful right now:
  - Composite tasks (skills list has >1 entry) -- these are Hypothesis B
    candidates (does the system recognize each component individually).
  - Same-skill tasks that appear under DIFFERENT roles -- these are the
    REAL adversarial cross-role candidates for Experiment 2 (no need to
    synthesize a DE-vs-SWE pair anymore; the data has real ones).
"""
import json
import re
from collections import defaultdict

import tomllib
from huggingface_hub import hf_hub_download, list_repo_files

REPO = "DavydenkoGr/AFTER"


def main():
    all_files = list_repo_files(REPO, repo_type="dataset")
    toml_files = sorted(f for f in all_files if re.match(r"^tasks/[^/]+/[^/]+/task\.toml$", f))
    print(f"found {len(toml_files)} tasks")

    index = []
    for f in toml_files:
        path = hf_hub_download(REPO, filename=f, repo_type="dataset")
        data = tomllib.load(open(path, "rb"))
        t = data["task"]
        index.append({
            "path": f, "id": t["id"], "role": t["role"],
            "skills": t.get("skills", []), "difficulty": t.get("difficulty"),
            "tags": t.get("tags", []),
        })

    with open("after_task_index.json", "w") as fh:
        json.dump(index, fh, indent=2)
    print(f"wrote after_task_index.json ({len(index)} tasks)\n")

    composite = [t for t in index if len(t["skills"]) > 1]
    print(f"=== Composite tasks (>1 skill) -- Hypothesis B candidates: {len(composite)} ===")
    for t in composite[:15]:
        print(f"  [{t['role']}] {t['id']}: {t['skills']}")

    by_skill_role: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for t in index:
        for s in t["skills"]:
            by_skill_role[s][t["role"]].append(t["id"])

    print(f"\n=== Skills used across MULTIPLE roles -- real Experiment 2 adversarial candidates ===")
    for skill, roles in sorted(by_skill_role.items()):
        if len(roles) > 1:
            print(f"  {skill}: " + ", ".join(f"{role}({len(ids)})" for role, ids in roles.items()))


if __name__ == "__main__":
    main()
