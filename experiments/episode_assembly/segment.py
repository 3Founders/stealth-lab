#!/usr/bin/env python3
"""
Throwaway episode segmenter over real Claude Code transcripts (ticket 11).

Ticket 11 is a PROTOTYPE ticket. Its deliverable is "a judgement to react to,
not a shipped segmenter." This runs 3 candidate deterministic rule sets over
this project's real session history and reports where they disagree. It is not
production code and nothing imports it.

PRIVACY: reports counts, boundaries, and timing only. Never emits message text,
tool inputs/outputs, or file contents. The only transcript strings printed are
structural (type names, tool names).

Rule sets (ticket 11's own list):
  A  prompt          -- genuine human prompts only
  B  prompt+subagent -- A, plus subagent spans as nested sub-episodes
  C  commit/test     -- git-commit and test-run completions

Precedence when combined (ticket 11): prompt > subagent > commit/test > idle.
Deterministic boundaries are HARD edges; nothing may split across them.

Usage:
    python3 segment.py                      # all sessions, summary
    python3 segment.py --limit 5            # N largest sessions
    python3 segment.py --json out.json      # machine-readable report
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PROJECT_SLUG = "c--Users-chait-Prog-3Found-Stealth-StealthLab"
PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# Auto-continuations that arrive as `type:"user"` lines but are NOT new human
# prompts. Taken from the reference implementation in the session-report plugin
# (analyze-sessions.mjs handleUser). Without these, every background-agent
# notification would open a spurious episode -- this project's own sessions are
# full of them.
NON_PROMPT_PREFIXES = (
    "<task-notification",
    "<scheduled-wakeup",
    "<background-task",
    "[Request interrupted",
)

COMMIT_RE = re.compile(r"\bgit\s+commit\b")
TEST_RE = re.compile(
    r"\b(pytest|npm\s+(run\s+)?test|yarn\s+test|go\s+test|cargo\s+test|"
    r"ansible-test|jest|vitest|tox|unittest)\b"
)


# ---------------------------------------------------------------- loading


def parse_ts(raw: Any) -> datetime | None:
    """Tolerant ISO-8601. 9 of 16 observed line types carry no timestamp, and
    the trailing-Z form breaks fromisoformat before Python 3.11."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def is_human_prompt(rec: dict) -> bool:
    """Genuine human prompt, per the reference predicate."""
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


def bash_commands(rec: dict) -> Iterable[str]:
    """Command strings from Bash/PowerShell tool_use blocks. Used only for
    regex classification; never stored or printed."""
    if rec.get("type") != "assistant":
        return
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") not in ("Bash", "PowerShell"):
            continue
        cmd = (block.get("input") or {}).get("command")
        if isinstance(cmd, str):
            yield cmd


def load_session(path: Path) -> list[dict]:
    """Structural view of one session. Content is inspected but not retained."""
    out: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            cmds = list(bash_commands(rec))
            out.append({
                "lineno": lineno,
                "type": rec.get("type"),
                "uuid": rec.get("uuid"),
                "parentUuid": rec.get("parentUuid"),
                "ts": parse_ts(rec.get("timestamp")),
                "is_prompt": is_human_prompt(rec),
                "is_root": "parentUuid" in rec and rec.get("parentUuid") is None,
                "is_commit": any(COMMIT_RE.search(c) for c in cmds),
                "is_test": any(TEST_RE.search(c) for c in cmds),
                "is_compaction": (
                    isinstance(rec.get("attachment"), dict)
                    and rec["attachment"].get("type") == "compact_file_reference"
                ),
                "spawns_agent": any(
                    isinstance(b, dict) and b.get("type") == "tool_use"
                    and b.get("name") == "Agent"
                    for b in ((rec.get("message") or {}).get("content") or [])
                    if isinstance((rec.get("message") or {}).get("content"), list)
                ),
            })
    return out


# ---------------------------------------------------------------- rules


def boundaries_prompt(events: list[dict]) -> list[int]:
    return [i for i, e in enumerate(events) if e["is_prompt"]]


def boundaries_prompt_subagent(events: list[dict]) -> list[int]:
    """Rule A plus Agent-spawn points. Ticket 11 wants subagent work NESTED,
    not co-equal -- so spawns are recorded as sub-boundaries and reported
    separately rather than merged into the top-level cut list."""
    return sorted({i for i, e in enumerate(events) if e["is_prompt"] or e["spawns_agent"]})


def boundaries_commit_test(events: list[dict]) -> list[int]:
    return [i for i, e in enumerate(events) if e["is_commit"] or e["is_test"]]


def to_episodes(events: list[dict], cuts: list[int]) -> list[tuple[int, int]]:
    """Half-open [start, end) spans. A cut opens a new episode."""
    if not events:
        return []
    starts = sorted(set(cuts) | {0})
    return [
        (s, starts[i + 1] if i + 1 < len(starts) else len(events))
        for i, s in enumerate(starts)
    ]


# ------------------------------------------------- idle threshold (B2)


def fit_two_component_gmm(xs: list[float], iters: int = 200) -> dict[str, float] | None:
    """1-D two-component Gaussian mixture by EM, pure Python (no numpy -- this
    is a throwaway prototype and a dependency would outlive it).

    Ticket 11: fit the threshold on LOG-scaled inter-event times and place it
    at the valley, rather than assuming the 30-minute convention.
    """
    xs = [x for x in xs if math.isfinite(x)]
    if len(xs) < 50:
        return None
    lo, hi = min(xs), max(xs)
    if hi - lo < 1e-9:
        return None

    mu = [lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.75]
    var = [statistics.pvariance(xs) or 1.0] * 2
    w = [0.5, 0.5]

    def pdf(x: float, m: float, v: float) -> float:
        v = max(v, 1e-9)
        return math.exp(-((x - m) ** 2) / (2 * v)) / math.sqrt(2 * math.pi * v)

    for _ in range(iters):
        resp = []
        for x in xs:
            a, b = w[0] * pdf(x, mu[0], var[0]), w[1] * pdf(x, mu[1], var[1])
            tot = a + b
            resp.append((0.5, 0.5) if tot <= 0 else (a / tot, b / tot))
        for k in (0, 1):
            nk = sum(r[k] for r in resp)
            if nk <= 1e-9:
                return None
            mu[k] = sum(r[k] * x for r, x in zip(resp, xs)) / nk
            var[k] = max(sum(r[k] * (x - mu[k]) ** 2 for r, x in zip(resp, xs)) / nk, 1e-9)
            w[k] = nk / len(xs)

    order = sorted(range(2), key=lambda k: mu[k])
    lo_k, hi_k = order
    # Valley = densest-crossing point between the two means.
    best, best_gap = None, None
    steps = 2000
    a, b = mu[lo_k], mu[hi_k]
    for i in range(steps + 1):
        x = a + (b - a) * i / steps
        d = abs(w[lo_k] * pdf(x, mu[lo_k], var[lo_k]) - w[hi_k] * pdf(x, mu[hi_k], var[hi_k]))
        if best_gap is None or d < best_gap:
            best_gap, best = d, x
    return {
        "mu_low": mu[lo_k], "mu_high": mu[hi_k],
        "sd_low": math.sqrt(var[lo_k]), "sd_high": math.sqrt(var[hi_k]),
        "weight_low": w[lo_k], "weight_high": w[hi_k],
        "valley_log": best,
        "valley_seconds": math.exp(best) if best is not None else float("nan"),
    }


# ---------------------------------------------------------------- report


def jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 1.0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--project", default=DEFAULT_PROJECT_SLUG)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    project_dir = PROJECTS_ROOT / args.project
    if not project_dir.is_dir():
        print(f"ERROR: no such project directory: {project_dir}")
        return 1

    sessions = sorted(project_dir.glob("*.jsonl"))
    if args.limit:
        sessions = sorted(sessions, key=lambda p: p.stat().st_size, reverse=True)[: args.limit]
    if not sessions:
        print("ERROR: no sessions found")
        return 1

    per_session: list[dict[str, Any]] = []
    all_gaps_log: list[float] = []
    all_prompt_gaps_log: list[float] = []
    agreement: list[tuple[float, float, float]] = []
    patho = Counter()

    for path in sessions:
        events = load_session(path)
        if not events:
            continue

        cuts = {
            "A_prompt": boundaries_prompt(events),
            "B_prompt_subagent": boundaries_prompt_subagent(events),
            "C_commit_test": boundaries_commit_test(events),
        }
        eps = {k: to_episodes(events, v) for k, v in cuts.items()}

        ts = [e["ts"] for e in events if e["ts"] is not None]
        ts.sort()
        gaps = [(ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1)]
        all_gaps_log += [math.log(g) for g in gaps if g > 0]

        # Gaps between consecutive HUMAN PROMPTS, not raw events. Raw
        # inter-event gaps are machine-paced (sub-second tool calls) and show
        # no human idle structure at all -- see the report. Prompt-to-prompt
        # gaps are the human-paced signal ticket 11's web-analytics source was
        # actually describing.
        pts = [e["ts"] for e in events if e["is_prompt"] and e["ts"] is not None]
        pts.sort()
        pgaps = [(pts[i + 1] - pts[i]).total_seconds() for i in range(len(pts) - 1)]
        all_prompt_gaps_log += [math.log(g) for g in pgaps if g > 0]

        sa, sb, sc = (set(cuts["A_prompt"]), set(cuts["B_prompt_subagent"]),
                      set(cuts["C_commit_test"]))
        agreement.append((jaccard(sa, sb), jaccard(sa, sc), jaccard(sb, sc)))

        # Pathological cases ticket 11 asks to be surfaced.
        n_prompts = len(cuts["A_prompt"])
        if n_prompts == 0:
            patho["session with zero human prompts"] += 1
        long_eps = [e for e in eps["A_prompt"] if e[1] - e[0] > 200]
        if long_eps:
            patho["one prompt spawning >200 events"] += len(long_eps)
        tiny = [e for e in eps["A_prompt"] if e[1] - e[0] <= 2]
        if tiny:
            patho["trivial prompt (<=2 events)"] += len(tiny)
        n_roots = sum(1 for e in events if e["is_root"])
        if n_roots > 1:
            patho["session is a forest (>1 root)"] += 1
        n_compact = sum(1 for e in events if e["is_compaction"])
        if n_compact:
            patho["compaction event mid-session"] += n_compact
        if any(e["spawns_agent"] for e in events) and not cuts["C_commit_test"]:
            patho["subagent work with no commit/test signal"] += 1

        per_session.append({
            "file": path.name,
            "events": len(events),
            "timestamped": len(ts),
            "roots": n_roots,
            "episodes": {k: len(v) for k, v in eps.items()},
            "boundaries": {k: len(v) for k, v in cuts.items()},
            "compaction_events": n_compact,
        })

    # ---- output
    print(f"project   {args.project}")
    print(f"sessions  {len(per_session)}")
    print()

    print("=== EPISODES PER SESSION, BY RULE ===")
    for rule in ("A_prompt", "B_prompt_subagent", "C_commit_test"):
        counts = [s["episodes"][rule] for s in per_session]
        tot = sum(counts)
        ev = sum(s["events"] for s in per_session)
        print(f"  {rule:<20} total={tot:>5}  "
              f"median/session={statistics.median(counts):>5.1f}  "
              f"mean events/episode={ev / tot if tot else 0:>7.1f}")
    print()

    print("=== INTER-RULE DISAGREEMENT (Jaccard over boundary positions) ===")
    if agreement:
        ab = statistics.mean(a for a, _, _ in agreement)
        ac = statistics.mean(b for _, b, _ in agreement)
        bc = statistics.mean(c for _, _, c in agreement)
        print(f"  A vs B  {ab:.3f}   (prompt vs prompt+subagent)")
        print(f"  A vs C  {ac:.3f}   (prompt vs commit/test)")
        print(f"  B vs C  {bc:.3f}   (prompt+subagent vs commit/test)")
        print(f"  -> rules disagree on {(1 - ac) * 100:.0f}% of boundary positions (A vs C)")
    print()

    print("=== IDLE THRESHOLD (fitted, not assumed) ===")
    fits = {}
    for label, data in (("raw inter-event", all_gaps_log),
                        ("human prompt-to-prompt", all_prompt_gaps_log)):
        f = fit_two_component_gmm(data)
        fits[label] = f
        print(f"  --- {label} gaps (n={len(data):,}) ---")
        if not f:
            print("      insufficient data to fit")
            continue
        print(f"      low  mean={math.exp(f['mu_low']):>9.1f}s  weight={f['weight_low']:.2f}")
        print(f"      high mean={math.exp(f['mu_high']):>9.1f}s  weight={f['weight_high']:.2f}")
        v = f["valley_seconds"]
        print(f"      VALLEY = {v:>9.1f}s = {v/60:>7.1f} min")
        sep = math.exp(f["mu_high"]) / max(math.exp(f["mu_low"]), 1e-9)
        print(f"      component separation = {sep:.1f}x "
              f"({'bimodal' if sep >= 10 else 'NOT bimodal -- single mode'})")
    if data := all_gaps_log:
        gs = sorted(math.exp(x) for x in data)
        q = lambda p: gs[min(int(len(gs)*p), len(gs)-1)]
        print(f"  raw gap percentiles: p50={q(.5):.1f}s p90={q(.9):.1f}s "
              f"p99={q(.99):.1f}s max={gs[-1]/3600:.1f}h")
    print()

    print("=== PATHOLOGICAL CASES ===")
    for k, n in patho.most_common():
        print(f"  {n:>5}  {k}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "project": args.project,
            "per_session": per_session,
            "idle_fits": fits,
            "pathological": dict(patho),
        }, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
