"""
Ingest AFTER's skills/ directory as the task_node library for Experiment 1.

Run from backend/, with .env pointing at the target database:
    pip install huggingface_hub pyyaml   # if not already installed
    python scripts/ingest_after_skills.py

Each of AFTER's 22 skills/{name}/SKILL.md becomes one TaskSpec:
  - name, description: read straight from the SKILL.md YAML frontmatter
    (the description field is written densely and specifically -- exactly
    what you want driving the embedding, better than inventing our own).
  - skill_ref: the skill folder name, so a later step can trace a matched
    node back to which AFTER skill it came from.
  - success_criteria.postconditions: the list of AFTER roles (from
    SKILL_MATRIX.md) that use this skill. Not a formal precondition in
    the logical sense -- a pragmatic, DATA-DERIVED starting tag rather
    than an invented one. Real adversarial precondition differences
    (Experiment 2) live at the TASK level, not the skill level -- see
    index_after_tasks.py for finding those.

This is deliberately the library only. Held-out task instructions
(tasks/{role}/{task}/instruction.md) are NOT ingested here -- they are
the QUERIES Experiment 1 tests retrieval against, not library content.
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

import yaml
from huggingface_hub import hf_hub_download, list_repo_files

from app.db.session import create_pool
from app.onboarding.seed import KnowledgeSpec, Onboarder, TaskSpec, WorkflowSpec

REPO = "DavydenkoGr/AFTER"


def parse_skill_matrix(text: str) -> dict[str, list[str]]:
    """SKILL_MATRIX.md -> {skill_name: [roles that use it]}."""
    header_cols: list[str] = []
    roles_by_skill: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] == "#":
            header_cols = cells[2:]  # skip '#' and 'skill' columns
            continue
        if set(cells[0]) <= {"-", ":"}:  # markdown separator row
            continue
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        skill_name = cells[1]
        marks = cells[2:]
        roles_by_skill[skill_name] = [
            header_cols[i] for i, m in enumerate(marks) if m == "✓" and i < len(header_cols)
        ]
    return roles_by_skill


def parse_skill_md(text: str) -> dict:
    """SKILL.md -> {name, description, dependencies}. Frontmatter is
    '---\\nYAML\\n---\\nmarkdown body'."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        raise ValueError("SKILL.md missing YAML frontmatter")
    front = yaml.safe_load(m.group(1))
    deps = (front.get("metadata") or {}).get("dependencies", [])
    return {"name": front["name"], "description": front.get("description", ""), "dependencies": deps}


async def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    all_files = list_repo_files(REPO, repo_type="dataset")
    skill_md_files = sorted(f for f in all_files if re.match(r"^skills/[^/]+/SKILL\.md$", f))
    print(f"found {len(skill_md_files)} skills")

    matrix_path = hf_hub_download(REPO, filename="skills/SKILL_MATRIX.md", repo_type="dataset")
    roles_by_skill = parse_skill_matrix(open(matrix_path, encoding="utf-8").read())

    tasks = []
    for f in skill_md_files:
        skill_dir = f.split("/")[1]
        path = hf_hub_download(REPO, filename=f, repo_type="dataset")
        parsed = parse_skill_md(open(path, encoding="utf-8").read())
        roles = roles_by_skill.get(skill_dir, [])
        tasks.append(TaskSpec(
            key=skill_dir,
            name=parsed["name"],
            description=parsed["description"],
            skill_ref=skill_dir,
            success_criteria={
                "postconditions": [f"role:{r.lower()}" for r in roles],
                "after_dependencies": parsed["dependencies"],
            },
        ))
        print(f"  parsed {skill_dir}: roles={roles}")

    spec = WorkflowSpec(workflow_name="after_skills_library", tasks=tasks)
    problems = spec.validate_spec()
    if problems:
        print("spec validation failed:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    onboarder = Onboarder(pool)
    result = await onboarder.seed(spec, created_by="after_ingestion")
    await pool.close()

    print(f"\nSeeded {len(result.task_ids)} skill task_nodes, {result.embedded} embedded.")
    if result.embedding_error:
        print(f"Embedding error (nodes created but not embedded — run backfill_embeddings.py): {result.embedding_error}")


if __name__ == "__main__":
    asyncio.run(main())
