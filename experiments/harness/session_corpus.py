"""
Ingest this project's own Claude Code sessions as the harness's first real
corpus (board MEASURE item 3: "plus ingest this project's own Claude Code
sessions as first real corpus"; doubles as the P4 dogfooding seed).

PRIVACY DISCIPLINE (experiments/episode_assembly/segment.py rule): the
manifest is LOCATOR-ONLY. It stores where a prompt lives (session id + line
number + char length) and structural metadata — never message text, tool
inputs/outputs, or file contents. Prompt text is read back from the local
transcript only at run time, on the founder's machine.

Parsing mirrors segment.py's proven predicates over the 16 observed line
types: auto-continuation prefixes are not new prompts, meta/compact/sidechain
lines are not prompts, a `user` line whose content starts with a tool_result
is not a prompt. Tolerant of torn final lines and missing timestamps.

Usage:
    python session_corpus.py --out corpus/cc_manifest.jsonl
    python session_corpus.py --projects-dir <dir> --out <path>   # counts only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# Auto-continuations that arrive as type:"user" lines but are NOT new human
# prompts. Same list as segment.py (taken originally from the session-report
# plugin's analyze-sessions.mjs handleUser) — without these every background-
# agent notification would open a spurious task.
NON_PROMPT_PREFIXES = (
    "<task-notification",
    "<scheduled-wakeup",
    "<background-task",
    "[Request interrupted",
)

# Allowlist enforced on every emitted row: an unexpected key fails loudly
# instead of leaking transcript content into the corpus.
ALLOWED_ROW_KEYS = frozenset({
    "task_id", "kind", "session_id", "source_path", "line_no",
    "prompt_chars", "ts", "unseen", "dry_run", "archetype",
})

SUBAGENT_DIR = "subagents"


def discover_sessions(projects_dir: Path | str = DEFAULT_PROJECTS_ROOT
                      ) -> list[Path]:
    """All session transcripts under ~/.claude/projects/<slug>/*.jsonl,
    including subagent sibling files (<slug>/subagents/agent-*.jsonl per the
    episode_assembly FINDINGS). Sorted for deterministic manifests."""
    root = Path(projects_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.jsonl") if p.is_file())


def kind_of(path: Path) -> str:
    return "subagent" if path.parent.name == SUBAGENT_DIR else "main"


def is_human_prompt(rec: dict) -> bool:
    """Genuine human prompt, per segment.py's reference predicate."""
    if rec.get("type") != "user":
        return False
    if rec.get("isMeta") or rec.get("isCompactSummary") or rec.get("isSidechain"):
        return False
    content = (rec.get("message") or {}).get("content")
    text = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            if first.get("type") == "tool_result":
                return False
            if first.get("type") == "text":
                text = first.get("text") or ""
    if text is None:
        return False
    return not text.startswith(NON_PROMPT_PREFIXES)


def parse_ts(raw: Any) -> str | None:
    """Timestamp kept as the raw ISO string (or None); no parsing needed for
    a locator, and tolerance beats exceptions on drift."""
    return raw if isinstance(raw, str) and raw else None


def manifest_rows(path: Path) -> Iterator[dict]:
    """Yield one locator row per genuine human prompt in one transcript.
    Content is measured, never retained."""
    session_id = path.stem
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue  # torn line (crash mid-write); skip like scoring does
            if not isinstance(rec, dict) or not is_human_prompt(rec):
                continue
            yield {
                "task_id": f"cc-{session_id[:8]}-L{lineno}",
                "kind": kind_of(path),
                "session_id": session_id,
                "source_path": str(path),
                "line_no": lineno,
                "prompt_chars": _prompt_chars(rec),
                "ts": parse_ts(rec.get("timestamp")),
                "unseen": True,
                "dry_run": True,
                "archetype": "real_session_prompt",
            }


def _prompt_chars(rec: dict) -> int:
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            return len(first.get("text") or "")
    return 0


def assert_locator_only(row: dict) -> None:
    unexpected = set(row) - ALLOWED_ROW_KEYS
    if unexpected:
        raise AssertionError(
            f"locator-only violation, unexpected keys: {sorted(unexpected)}")


def write_manifest(paths: list[Path], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for p in paths:
            for row in manifest_rows(p):
                assert_locator_only(row)
                fh.write(json.dumps(row) + "\n")
                n += 1
    return n


def load_manifest(manifest_path: Path | str) -> list[dict]:
    rows = []
    for line in Path(manifest_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        assert_locator_only(rec)
        rows.append(rec)
    return rows


def corpus_tasks(manifest_path: Path | str) -> list[dict]:
    """Manifest -> runner-compatible dry-run tasks. No solo_outcome: the arms'
    scripted outcomes are meaningless on real prompts, so these run as
    unscored pipeline exercises (see scripted_arms.SoloFrontierAgent.run)."""
    return [
        {
            "task_id": r["task_id"],
            "domain": "real_session",
            "unseen": True,
            "rag": "none",
            "dry_run": True,
            "source_path": r["source_path"],
            "line_no": r["line_no"],
        }
        for r in load_manifest(manifest_path)
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--projects-dir", default=str(DEFAULT_PROJECTS_ROOT))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent /
                                         "corpus" / "cc_manifest.jsonl"))
    args = ap.parse_args(argv)

    root = Path(args.projects_dir)
    sessions = discover_sessions(root)
    out_path = Path(args.out)
    n_rows = write_manifest(sessions, out_path)

    # Counts only — never text (privacy discipline).
    by_kind: dict[str, int] = {}
    for row in load_manifest(out_path):
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    print(f"projects root : {root}")
    print(f"sessions found: {len(sessions)}")
    print(f"prompts       : {n_rows} "
          f"({', '.join(f'{k}={v}' for k, v in sorted(by_kind.items()))})")
    print(f"manifest      : {out_path} (locator-only; no message text)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
