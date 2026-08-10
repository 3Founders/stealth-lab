"""
A retrieval corpus that scales, with gold labels preserved (Experiment 5).

Hypothesis A ran against 22 leaves and found hierarchical search strictly
worse than a flat scan -- both less accurate and, because descent scores
internal nodes on the way down, more comparisons. That is the expected
result at that size: there is nothing to prune. The open question is
whether a crossover exists, and where.

Scaling honestly requires keeping the gold labels valid, so the corpus
grows three ways, none of which invent ground truth:

  1. CHUNKS of the skill documents. Every chunk inherits its parent
     skill's identity, so a retrieval is correct when the retrieved
     chunk's parent is the task's gold skill. This is the same labelling
     rule as Hypothesis A, applied at finer granularity -- and it is what
     the plan itself wanted from atomic leaves ("cite clause 4.2, not the
     whole policy").
  2. AUXILIARY skill files (references/, assets/, scripts/,
     SKILL_HANDCRAFT.md). Genuinely part of a skill, genuinely retrievable,
     genuinely labelled.
  3. DISTRACTORS from SWE-bench Pro (731 real issue reports). These carry
     no gold label and can never be a correct answer. They are the honest
     part of a scale test: real corpora grow mostly with content that is
     irrelevant to any given query, and a retriever that only stays sharp
     when every document is relevant has not been tested.

Task queries are NEVER added to the library -- that would let a query
retrieve itself.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from experiments.after.corpus import snapshot_dir

_HEADING = re.compile(r"^#{1,4}\s+.*$", re.MULTILINE)


@dataclass
class Node:
    key: str            # unique id within the corpus
    name: str           # short label
    text: str           # embedded/searchable content
    skill: Optional[str]  # parent skill, or None for a distractor
    kind: str           # skill | chunk | aux | distractor


def _chunk_markdown(body: str, min_chars: int = 250, max_chars: int = 2000) -> list[str]:
    """
    Split on markdown headings, then hard-wrap anything still oversized.

    Short trailing fragments are merged forward rather than emitted: a
    12-character chunk carries no retrievable signal but still occupies a
    leaf, which would inflate the corpus without making the task harder.
    """
    spans = [m.start() for m in _HEADING.finditer(body)]
    if not spans or spans[0] != 0:
        spans = [0] + spans
    raw = [body[a:b].strip() for a, b in zip(spans, spans[1:] + [len(body)])]

    out: list[str] = []
    buf = ""
    for piece in raw:
        if not piece:
            continue
        if len(buf) + len(piece) < min_chars:
            buf = f"{buf}\n{piece}".strip()
            continue
        piece = f"{buf}\n{piece}".strip() if buf else piece
        buf = ""
        while len(piece) > max_chars:
            cut = piece.rfind("\n", 0, max_chars)
            if cut < min_chars:
                cut = max_chars
            out.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            out.append(piece)
    if buf:
        if out:
            out[-1] = f"{out[-1]}\n{buf}"
        else:
            out.append(buf)
    return [c for c in out if len(c) >= 60]


def build_labelled_nodes(max_chars: int = 2000) -> list[Node]:
    """Skills, their chunks, and their auxiliary files -- all gold-labelled."""
    root = snapshot_dir() / "skills"
    nodes: list[Node] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        skill = d.name
        main = d / "SKILL.md"
        if main.exists():
            body = main.read_text(encoding="utf-8")
            nodes.append(Node(f"{skill}::SKILL", skill, f"{skill}\n{body[:8000]}", skill, "skill"))
            for i, c in enumerate(_chunk_markdown(body, max_chars=max_chars)):
                nodes.append(Node(f"{skill}::chunk{i}", f"{skill}#{i}", f"{skill}\n{c}", skill, "chunk"))

        for aux in sorted(d.rglob("*")):
            if not aux.is_file() or aux.name == "SKILL.md":
                continue
            if aux.suffix.lower() not in (".md", ".py", ".json", ".txt", ".yaml", ".yml"):
                continue
            try:
                body = aux.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            rel = aux.relative_to(d).as_posix()
            for i, c in enumerate(_chunk_markdown(body, max_chars=max_chars)):
                nodes.append(Node(
                    f"{skill}::{rel}::{i}", f"{skill}/{rel}#{i}",
                    f"{skill} {rel}\n{c}", skill, "aux",
                ))
    return nodes


def build_distractors(limit: Optional[int] = None, max_chars: int = 2000) -> list[Node]:
    """Real issue reports from SWE-bench Pro. Never a correct answer."""
    pattern = os.path.expanduser(
        "~/.cache/huggingface/hub/datasets--ScaleAI--SWE-bench_Pro/snapshots/*/data/*.parquet"
    )
    files = glob.glob(pattern)
    if not files:
        return []
    import pandas as pd

    df = pd.read_parquet(files[0], columns=["instance_id", "repo", "problem_statement"])
    if limit:
        df = df.head(limit)
    nodes = []
    for _, r in df.iterrows():
        text = str(r["problem_statement"])[:max_chars]
        nodes.append(Node(
            f"distractor::{r['instance_id']}", f"{r['repo']}:{str(r['instance_id'])[:40]}",
            text, None, "distractor",
        ))
    return nodes


def summary() -> dict:
    lab = build_labelled_nodes()
    dis = build_distractors()
    by_kind: dict[str, int] = {}
    for n in lab + dis:
        by_kind[n.kind] = by_kind.get(n.kind, 0) + 1
    per_skill: dict[str, int] = {}
    for n in lab:
        per_skill[n.skill] = per_skill.get(n.skill, 0) + 1
    return {
        "labelled_nodes": len(lab),
        "distractors": len(dis),
        "total": len(lab) + len(dis),
        "by_kind": by_kind,
        "skills_covered": len(per_skill),
        "nodes_per_skill_min": min(per_skill.values()) if per_skill else 0,
        "nodes_per_skill_max": max(per_skill.values()) if per_skill else 0,
        "mean_chars": round(sum(len(n.text) for n in lab + dis) / max(1, len(lab) + len(dis))),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2))
