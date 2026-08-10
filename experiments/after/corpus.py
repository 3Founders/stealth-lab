"""
AFTER corpus loader, reading the HuggingFace cache directly.

Uses the already-downloaded snapshot rather than datasets.load_dataset():
AFTER is a file tree (skills/*/SKILL.md, tasks/<role>/<task>/), not a
tabular dataset, and the loader script the repo ships is absent from the
cache (.no_exist/AFTER.py). Reading the snapshot is what actually works.

Gold labels come from each task's task.toml `skills = [...]` field. That
field is the whole reason this benchmark can grade retrieval without an
execution sandbox -- AFTER ships NO validators (verified: zero test*.py
files in the entire dataset), so anything requiring pass/fail on generated
output is ungradeable here. Retrieval against these labels is not.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_HF_HUB = Path.home() / ".cache" / "huggingface" / "hub"
_REPO = "datasets--DavydenkoGr--AFTER"


def snapshot_dir() -> Path:
    snaps = _HF_HUB / _REPO / "snapshots"
    if not snaps.exists():
        raise FileNotFoundError(
            f"AFTER snapshot not found at {snaps}. Fetch it with:\n"
            f"  huggingface-cli download DavydenkoGr/AFTER --repo-type dataset"
        )
    candidates = sorted(snaps.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for c in candidates:
        if (c / "skills").exists() and (c / "tasks").exists():
            return c
    raise FileNotFoundError(f"no usable AFTER snapshot under {snaps}")


@dataclass
class Skill:
    name: str
    body: str

    def text(self) -> str:
        """What gets embedded / stored as the node's searchable content."""
        return f"{self.name}\n{self.body}"


@dataclass
class Task:
    task_id: str
    role: str
    gold_skills: list[str]
    instruction: str
    difficulty: str

    @property
    def is_composite(self) -> bool:
        return len(self.gold_skills) >= 2


def load_skills(variant: str = "SKILL.md", max_chars: int = 8000) -> list[Skill]:
    """
    The 22 library skills.

    `variant` is pinned rather than defaulted-and-forgotten: SKILL.md and
    SKILL_HANDCRAFT.md are two versions of the same content, and a library
    mixing them is incoherent. Whichever is used gets recorded in the run
    manifest.

    Bodies are truncated because a few skills carry long reference
    appendices; embedding 20K characters of appendix dilutes the signal the
    skill name and summary carry, and costs tokens on a rate-limited key.
    """
    root = snapshot_dir() / "skills"
    skills: list[Skill] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        f = d / variant
        if not f.exists():
            continue
        skills.append(Skill(name=d.name, body=f.read_text(encoding="utf-8")[:max_chars]))
    return skills


def load_tasks(max_chars: int = 6000) -> list[Task]:
    """All 129 tasks with their gold skill labels."""
    root = snapshot_dir() / "tasks"
    tasks: list[Task] = []
    for role_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for task_dir in sorted(p for p in role_dir.iterdir() if p.is_dir()):
            toml_path = task_dir / "task.toml"
            instr_path = task_dir / "instruction.md"
            if not toml_path.exists() or not instr_path.exists():
                continue
            meta = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            t = meta.get("task", {})
            tasks.append(Task(
                task_id=t.get("id") or task_dir.name,
                role=role_dir.name,
                gold_skills=list(t.get("skills", [])),
                instruction=instr_path.read_text(encoding="utf-8")[:max_chars],
                difficulty=t.get("difficulty", "unknown"),
            ))
    return tasks


def summary() -> dict:
    skills = load_skills()
    tasks = load_tasks()
    referenced = {s for t in tasks for s in t.gold_skills}
    known = {s.name for s in skills}
    return {
        "snapshot": str(snapshot_dir()),
        "skills": len(skills),
        "tasks": len(tasks),
        "composite_tasks": sum(1 for t in tasks if t.is_composite),
        "gold_components": sum(len(t.gold_skills) for t in tasks if t.is_composite),
        "roles": sorted({t.role for t in tasks}),
        # A gold label naming a skill that is not in the library would make
        # that task unscoreable; must be empty.
        "unknown_gold_labels": sorted(referenced - known),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2))
