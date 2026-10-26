"""Output check: is the message the user sees when a cheap model did their task correct, clear and enough?

When arm C (or C0) answers a prompt with a cheap model, the hook blocks the prompt and shows ONE message
(packaging/npm/lib/dispatch.mjs):

    Done by <model> in <N> s (kell dispatched it before this session's model ran, $<cost>). It passed <check> in an
    isolated worktree and the change is applied: <files>. Recorded in the library as <id>. To put it back as it was:
    kell undo.

    <a short explanation by a cheap model>

This scores each stored message out of 6, one point each (plan 3.4):

  1 names     names the model, the time AND the cost
  2 files     names the changed files (all of them, when the diff is known)
  3 correct   the explanation is correct for the diff            \\ judged by a model (Sonnet through Claude Code, a short
  4 checked   says how it was checked ("It passed <check>")        > system prompt, as lib/visual_check.mjs does) that sees
  5 undo      says how to undo ("kell undo")            /  the DIFF and the MESSAGE; 1, 2, 4 and 5 need no model
  6 honest    no claim the diff does not support               /

Without a diff (a single-prompt result keeps only the first 400 characters of the reply) criteria 3 and 6 are not scored
and the maximum is 4; every score says what it was out of. A reply cut at 400 characters is flagged `truncated`: its
explanation is partly missing. `--judge none` scores only criteria 1, 2, 4 and 5 (no model, no cost).

    python experiments/abtest/output_check.py <file.jsonl> [more ...] [--judge claude|none] [--out scores.jsonl]

Input lines (any mix): {"id", "message", "diff"?}  |  a runner result with `turn_log` (one message per dispatched turn,
with that turn's diff)  |  a runner result whose `final` starts with "Done by" (arm C, one prompt, no diff).
`message_from_log` reads the same message out of a stream-json session log. The report gives the mean and lists every
message that scored below 5/6 (below the maximum - 1 when it was out of 4 or less).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
JUDGE_MODEL = "claude-sonnet-5-5"
JUDGE_SYSTEM = ("You check a short message shown to a developer against the change it describes. "
                "Reply with one JSON object and nothing else.")
CRITERIA = ("names", "files", "correct", "checked", "undo", "honest")
JUDGED = ("correct", "honest")
DONE_BY = re.compile(r"Done by (\S+) in (\d+) s")
COST = re.compile(r"\$\d+(?:\.\d+)?")
CHECKED = re.compile(r"It passed (.+?) in an isolated worktree", re.S)
APPLIED = re.compile(r"change is applied:\s*([^\n]*?)\.(?:\s|$)")
TRUNCATED_AT = 400                      # runner.run_claude keeps `final`[:400]
IGNORED_PATHS = (".kell/", ".claude/")

Judge = Callable[[str, str], dict]      # (message, diff) -> {"correct": bool, "honest": bool, "reason": str, "cost_usd"?: float}


# ---------------------------------------------------------------- reading messages

def diff_files(diff: str) -> list[str]:
    """The files a unified diff touches, without kell's own directories."""
    files = re.findall(r"^diff --git a/(\S+) b/\S+$", diff or "", flags=re.M)
    return [f for f in dict.fromkeys(files) if not f.startswith(IGNORED_PATHS)]


def message_from_log(log: Path) -> str | None:
    """The "Done by ..." message in a stream-json session log: the hook's output reaches the log inside some message field,
    so every string of every line is searched. None when the turn was not dispatched."""
    if not Path(log).exists():
        return None
    found: list[str] = []

    def walk(x: object) -> None:
        if isinstance(x, str):
            if "Done by " in x and "kell dispatched it" in x:
                found.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for line in Path(log).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not found:
        return None
    text = found[0]
    try:                                # a hook's stdout is JSON ({"decision": "block", "reason": ...}): keep the reason
        reason = json.loads(text).get("reason")
        if isinstance(reason, str) and "Done by " in reason:
            text = reason
    except (json.JSONDecodeError, AttributeError):
        pass
    return text[text.find("Done by "):][:6000]


def items_from_line(row: dict) -> list[dict]:
    """The messages one input line holds: [{id, message, diff|None, truncated}]."""
    if isinstance(row.get("message"), str):
        return [{"id": str(row.get("id") or "message"), "message": row["message"], "diff": row.get("diff"),
                 "truncated": bool(row.get("truncated"))}]
    base = f"{row.get('task_id', '?')}.{row.get('arm', '?')}"
    out = []
    for t in row.get("turn_log") or []:
        if isinstance(t.get("shown"), str) and t["shown"].startswith("Done by"):
            out.append({"id": f"{base}.t{t.get('turn', '?')}", "message": t["shown"], "diff": t.get("diff"), "truncated": False})
    final = row.get("final")
    if not out and isinstance(final, str) and final.startswith("Done by"):
        out.append({"id": base, "message": final, "diff": None, "truncated": len(final) >= TRUNCATED_AT})
    return out


def load_items(paths: list[Path]) -> list[dict]:
    items = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                items += items_from_line(json.loads(line))
    return items


# ---------------------------------------------------------------- the criteria that need no model

def rule_points(message: str, diff: str | None) -> dict[str, tuple[int, str]]:
    """criterion -> (0 or 1, why) for names, files, checked, undo."""
    out: dict[str, tuple[int, str]] = {}
    m = DONE_BY.search(message)
    has_cost = bool(COST.search(message[: message.find("\n\n")] if "\n\n" in message else message))
    miss = [w for w, ok in (("the model", bool(m)), ("the time", bool(m)), ("the cost", has_cost)) if not ok]
    out["names"] = (0, "does not name " + ", ".join(miss)) if miss else (1, f"{m.group(1)}, {m.group(2)} s, cost shown")
    named = APPLIED.search(message)
    listed = [f.strip() for f in (named.group(1) if named else "").split(",") if f.strip() and f.strip() != "no files"]
    if diff is not None:
        want = diff_files(diff)
        lacking = [f for f in want if f not in listed]
        if not want:
            out["files"] = (1, "the diff changes no tracked file") if not listed else (0, f"names {listed} but the diff changes nothing")
        else:
            out["files"] = (0, "missing from the message: " + ", ".join(lacking[:5])) if lacking else (1, "all changed files named")
    else:
        out["files"] = (1, f"names {len(listed)} file(s) (no diff to compare)") if listed else (0, "names no file")
    c = CHECKED.search(message)
    out["checked"] = (1, "check: " + " ".join(c.group(1).split())[:60]) if c else (0, "does not say how it was checked")
    out["undo"] = (1, "undo command given") if "kell undo" in message else (0, "does not say how to undo")
    return out


# ---------------------------------------------------------------- the judge (a model)

def judge_prompt(message: str, diff: str) -> str:
    return (
        "A coding assistant changed a project, and the developer was shown the MESSAGE below. The exact change is the DIFF.\n"
        "Decide two things.\n"
        "  correct: the message's EXPLANATION of what changed (the text after the first blank line, or the whole message "
        "if there is none) matches what the diff actually does.\n"
        "  honest: the message makes NO claim the diff does not support (a feature, file, test or behaviour the diff does not "
        "contain, or 'all cases' when it handles some).\n"
        'Reply with JSON only: {"correct": true|false, "honest": true|false, "reason": "at most 25 words"}.\n\n'
        f"MESSAGE:\n{message[:3000]}\n\nDIFF:\n{diff[:12000]}\n")


def parse_verdict(text: str) -> dict | None:
    s = str(text or "")
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        obj = json.loads(s[a:b + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("correct"), bool) or not isinstance(obj.get("honest"), bool):
        return None
    return {"correct": obj["correct"], "honest": obj["honest"], "reason": str(obj.get("reason") or "")[:200]}


def claude_judge(model: str = JUDGE_MODEL, timeout: int = 240) -> Judge:
    """The judge: headless Claude Code on Sonnet with a one-line system prompt and nothing to read. It runs in an empty
    temp directory with project settings only, so none of the person's hooks (kell's own included) can touch it.
    Needs the Claude Code login (or CLAUDE_CODE_OAUTH_TOKEN) of whoever runs it; about $0.01-0.02 a message."""
    exe = shutil.which("claude") or "claude"

    def judge(message: str, diff: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([exe, "-p", "--model", model, "--system-prompt", JUDGE_SYSTEM, "--tools", "Read", "--allowedTools", "Read",
                                "--setting-sources", "project", "--strict-mcp-config", "--output-format", "json"],
                               input=judge_prompt(message, diff), cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"the judge returned no JSON (exit {r.returncode})") from None
        verdict = parse_verdict(str(payload.get("result") or ""))
        if verdict is None:
            raise RuntimeError("the judge's reply was not the verdict JSON")
        verdict["cost_usd"] = payload.get("total_cost_usd")
        return verdict

    return judge


# ---------------------------------------------------------------- scoring and the report

def score_item(item: dict, judge: Judge | None) -> dict:
    message, diff = item["message"], item.get("diff")
    pts = rule_points(message, diff)
    cost = 0.0
    if judge is not None and diff is not None:
        try:
            v = judge(message, diff)
            pts["correct"] = (int(v["correct"]), v.get("reason") or "")
            pts["honest"] = (int(v["honest"]), v.get("reason") or "")
            cost = float(v.get("cost_usd") or 0)
        except Exception as exc:  # noqa: BLE001 -- one bad judge call must not end the run: those two are simply not scored
            pts["judge_error"] = (0, str(exc)[:200])
    scored = [c for c in CRITERIA if c in pts]
    total = sum(pts[c][0] for c in scored)
    return {"id": item["id"], "points": {c: pts[c][0] for c in scored}, "why": {c: pts[c][1] for c in pts},
            "total": total, "of": len(scored), "truncated": bool(item.get("truncated")), "judge_cost_usd": round(cost, 6),
            "not_scored": [c for c in CRITERIA if c not in pts]}


def threshold(of: int) -> int:
    """5 when scored out of 6; with fewer criteria scored (no diff, or no judge), one below the maximum."""
    return 5 if of >= 6 else max(of - 1, 1)


def low(scores: list[dict]) -> list[dict]:
    """Every message below 5/6 (below `threshold` for one scored out of fewer criteria)."""
    return [s for s in scores if s["total"] < threshold(s["of"])]


def report(scores: list[dict]) -> str:
    if not scores:
        return "no dispatched message found in the input"
    lines = [f"{len(scores)} message(s); judge cost ${sum(s['judge_cost_usd'] for s in scores):.3f}"]
    by_of: dict[int, list[dict]] = {}
    for s in scores:
        by_of.setdefault(s["of"], []).append(s)
    for of in sorted(by_of, reverse=True):
        g = by_of[of]
        lines.append(f"  scored out of {of}: {len(g)} message(s), mean {sum(s['total'] for s in g) / len(g):.2f}/{of}")
    crit = {c: [s["points"][c] for s in scores if c in s["points"]] for c in CRITERIA}
    lines.append("  per criterion: " + ", ".join(f"{c} {sum(v)}/{len(v)}" for c, v in crit.items() if v))
    if any(s["truncated"] for s in scores):
        lines.append(f"  {sum(s['truncated'] for s in scores)} message(s) were cut at {TRUNCATED_AT} characters (the explanation is partly missing)")
    bad = low(scores)
    lines.append(f"below 5/6 (below max-1 where scored out of fewer): {len(bad)}")
    for s in bad:
        miss = [f"{c}: {s['why'][c]}" for c in CRITERIA if c in s["points"] and not s["points"][c]]
        lines.append(f"  {s['id']}  {s['total']}/{s['of']}  " + "; ".join(miss))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    paths, judge_name, out = [], "claude", None
    it = iter(argv)
    for a in it:
        if a == "--judge":
            judge_name = next(it, "")
        elif a == "--out":
            out = Path(next(it, ""))
        else:
            paths.append(Path(a))
    if not paths or judge_name not in ("claude", "none"):
        print(__doc__.split("Input lines")[0].strip().splitlines()[-1])
        return 2
    judge = claude_judge() if judge_name == "claude" else None
    scores = [score_item(i, judge) for i in load_items(paths)]
    if out:
        out.write_text("".join(json.dumps(s) + "\n" for s in scores), encoding="utf-8")
    print(report(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
