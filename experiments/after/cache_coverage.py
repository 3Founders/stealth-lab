"""How much of the corpus is already embedded, without spending anything."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.after.corpus import load_skills, load_tasks
from experiments.after.embed_cache import CachedEmbedder

e = CachedEmbedder()
tasks = load_tasks()
skills = load_skills()

single = [t for t in tasks if len(t.gold_skills) == 1]
comp = [t for t in tasks if t.is_composite]


def cached(text: str, kind: str) -> bool:
    return e._key(text, kind) in e._cache


print(f"cache entries: {len(e._cache)}")
for label, items, kind, get in (
    ("skills (document)", skills, "document", lambda s: s.text()),
    ("all tasks (query)", tasks, "query", lambda t: t.instruction),
    ("single-skill tasks", single, "query", lambda t: t.instruction),
    ("composite tasks", comp, "query", lambda t: t.instruction),
):
    hit = sum(1 for i in items if cached(get(i), kind))
    print(f"  {label:22s} {hit:3d}/{len(items):3d} cached")
