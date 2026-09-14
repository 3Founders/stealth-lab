"""
Local, zero-LLM-cost "is there a cheaper way to do this node" check --
mirrors resolve_implementation's real job (app/execution/implementation_
registry.py: a pure Postgres lookup, no model call, honest null when
nothing resolves) but scoped locally to this workspace's `.stealth/`
instead of requiring a real `task_nodes` row in the global graph.

Conservative by design: keyed on EXACT normalized goal text, never a fuzzy
match -- applying a cached tool-call sequence to a goal it wasn't actually
proven against would be exactly the "fabricated capability" this whole
project's evidence-based-capability rule forbids. A near-miss just means
another real LLM call (the honest fallback), not a wrong shortcut.

`.stealth/implementations.md` format:
    ---
    goal: <exact, normalized goal text>
    uses: 3
    successes: 3
    recorded_at: <iso ts>
    ---
    tool_calls:
      - {"name": "api_search", "arguments": {...}}
      - {"name": "api_fetch", "arguments": {...}}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _normalize(goal: str) -> str:
    return re.sub(r"\s+", " ", goal.strip().lower())


def _slug(goal: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _normalize(goal)).strip("-")
    return s[:60] or "goal"


@dataclass
class LocalImplementation:
    goal: str
    uses: int
    successes: int
    tool_calls: list[dict]


def _entry_path(root: Path, goal: str) -> Path:
    return root / f"{_slug(goal)}.md"


def resolve_local_implementation(root: Path, goal: str) -> Optional[LocalImplementation]:
    """Zero-LLM-cost lookup. Returns None (never a guess) if this exact
    goal text has no recorded implementation yet, or if its own recorded
    success rate isn't good enough to trust blindly (< 100% -- a step
    that has ever failed under this exact goal is NOT replayed silently;
    it still needs a real, LLM-driven attempt so a genuine failure gets
    seen, not papered over by re-running the same broken sequence)."""
    path = root / f"{_slug(goal)}.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    _, frontmatter_text, body = text.split("---", 2)
    frontmatter = {}
    for line in frontmatter_text.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            frontmatter[k.strip()] = v.strip()
    if frontmatter.get("goal") != _normalize(goal):
        return None
    uses = int(frontmatter.get("uses", 0))
    successes = int(frontmatter.get("successes", 0))
    if uses == 0 or successes != uses:
        return None  # anything less than a clean record gets a real attempt, not a replay
    tool_calls_text = body.split("tool_calls:", 1)[-1].strip()
    try:
        tool_calls = [json.loads(line.strip().lstrip("- ")) for line in tool_calls_text.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return None
    return LocalImplementation(goal=goal, uses=uses, successes=successes, tool_calls=tool_calls)


def record_local_implementation(
    root: Path, goal: str, *, tool_calls: list[dict], success: bool,
) -> None:
    """Records/updates this exact goal's local track record. A failure
    still gets recorded (uses += 1, successes unchanged) so a previously
    clean implementation that later fails on this same goal stops being
    trusted (successes != uses) rather than silently staying "resolved"."""
    root.mkdir(parents=True, exist_ok=True)
    path = _entry_path(root, goal)
    uses, successes = 0, 0
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("uses:"):
                uses = int(line.split(":", 1)[1].strip())
            elif line.startswith("successes:"):
                successes = int(line.split(":", 1)[1].strip())
    uses += 1
    if success:
        successes += 1
    tool_calls_yaml = "\n".join(f"  - {json.dumps(tc)}" for tc in tool_calls)
    path.write_text(
        f"---\ngoal: {_normalize(goal)}\nuses: {uses}\nsuccesses: {successes}\n"
        f"recorded_at: {datetime.now(timezone.utc).isoformat()}\n---\n\ntool_calls:\n{tool_calls_yaml}\n",
        encoding="utf-8",
    )
