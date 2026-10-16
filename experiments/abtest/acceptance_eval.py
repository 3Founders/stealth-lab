"""acceptance_eval: the pre-registered acceptance rules (.scratch/acceptance_rules_preregistered_2026-10-16.md in the
stealth-lab checkout) judged on stage 3's saved cheap answers, with no Modal and no agent run.

Signals per answer (only what a real project has when the task is handed out; the hidden tests only GRADE):
  T our written tests pass            (stage 3: passes_our_tests)
  E the project's existing tests pass  (stage 3 existing_new_failures == 0, or stage 4's for runs made before it)
  S the answer changes source code     (lib/review.mjs changesSource on the patch's files)
  A the other agent changed the same source files (both answers non-empty)
  R a cheap reviewer says "fixes"      (lib/review.mjs, gpt-oss-120b; cached in .local/survey/reviews.jsonl)

    python experiments/abtest/acceptance_eval.py --stage3 a.jsonl [b.jsonl] [--stage4 c.jsonl] [--review] [--label design]

--review calls the reviewer for answers not yet reviewed (keys from the environment or backend/.env, by name). Runs that
never reached the model are left out, as in report3.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".local" / "survey" / "reviews.jsonl"
OPUS_USD = 1.13              # Opus per issue (the one series issue, arm A); an assumption, stated in the report
RULES = {
    "1 T (today)": lambda s: s["T"],
    "2 E and S": lambda s: s["E"] and s["S"],
    "3 E and S and (T or R)": lambda s: s["E"] and s["S"] and (s["T"] or s["R"]),
    "4 E and S and R": lambda s: s["E"] and s["S"] and s["R"],
    "5 E and S and A": lambda s: s["E"] and s["S"] and s["A"],
    "6 E and S and (A or R)": lambda s: s["E"] and s["S"] and (s["A"] or s["R"]),
    "7 E S F X, no R-veto": lambda s: s["E"] and s["S"] and s["F"] and s["X"] and not s["Rno"],
    "8 E S F X T*, no R-veto": lambda s: s["E"] and s["S"] and s["F"] and s["X"] and s["Tstar"] and not s["Rno"],
    "9 E S T* and (A or R)": lambda s: s["E"] and s["S"] and s["Tstar"] and (s["A"] or s["R"]),
}


def files_of(patch: str) -> list[str]:
    return re.findall(r"^diff --git a/\S+ b/(\S+)", patch or "", re.M)


def changes_source(files: list[str]) -> bool:
    test = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.(py|go|js|ts)$|\.(test|spec)\.[jt]sx?$", re.I)
    doc = re.compile(r"\.(md|rst|txt|cfg|ini|toml|ya?ml|json|lock)$|(^|/)docs?/", re.I)
    return any(not test.search(f) and not doc.search(f) for f in files)


def focused(patch: str) -> bool:
    """F: at most 3 source files and 60 changed lines (docs/acceptance_rules_preregistered_2026-10-16.md)."""
    files = [f for f in files_of(patch) if changes_source([f])]
    changed = sum(1 for l in (patch or "").splitlines() if (l.startswith("+") or l.startswith("-")) and not l.startswith(("+++", "---")))
    return len(files) <= 3 and changed <= 60


SWALLOW = re.compile(r"^\+\s*except(\s+(Exception|BaseException)(\s+as\s+\w+)?)?\s*:\s*(pass|continue|return\b.*)?\s*$")


def no_swallow(patch: str) -> bool:
    """X: no added catch-all except whose body (same or next added line) is pass / continue / return."""
    added = [l for l in (patch or "").splitlines() if l.startswith("+") and not l.startswith("+++")]
    for i, l in enumerate(added):
        m = SWALLOW.match(l)
        if m and (m.group(4) or (i + 1 < len(added) and re.match(r"^\+\s*(pass|continue|return\b.*)\s*$", added[i + 1]))):
            return False
    return True


def unreached(x: dict) -> bool:
    return x.get("state") == "error" or (not x.get("files") and (
        str(x.get("error") or "").startswith("the executor failed") or (x.get("tokens") or {}).get("in") in (0, None)))


def load(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths or []:
        rows += [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def problem_statements(iids: set[str]) -> dict[str, str]:
    sys.path.insert(0, str(HERE))
    import swe_series_task as S
    df = S.load_split()
    df = df[df.instance_id.isin(iids)]
    return dict(zip(df.instance_id, df.problem_statement))


def review_env() -> dict[str, str]:
    import modal_series as M
    env = {**os.environ, **M.read_names(M.KEY_NAMES)}
    env["STEALTHLAB_HOME"] = str(HERE / ".local" / "review_home")
    Path(env["STEALTHLAB_HOME"]).mkdir(parents=True, exist_ok=True)
    (Path(env["STEALTHLAB_HOME"]) / "exec.json").write_text((HERE / "exec.json").read_text(encoding="utf-8"), encoding="utf-8")
    return env


def run_reviews(answers: list[dict]) -> dict[str, dict]:
    cache = {}
    if CACHE.exists():
        for l in CACHE.read_text(encoding="utf-8").splitlines():
            if l.strip():
                o = json.loads(l)
                cache[o["key"]] = o
    todo = [a for a in answers if a["key"] not in cache and (a["patch"] or "").strip()]
    if todo:
        prompts = problem_statements({a["iid"] for a in todo})
        lines = "".join(json.dumps({"key": a["key"], "prompt": prompts.get(a["iid"], ""), "patch": a["patch"]}) + "\n" for a in todo)
        p = subprocess.run(["node", str(HERE / "acceptance_review.mjs")], input=lines, capture_output=True, text=True,
                           env=review_env(), timeout=3600, encoding="utf-8")
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with CACHE.open("a", encoding="utf-8") as f:
            for l in p.stdout.splitlines():
                if l.strip():
                    o = json.loads(l)
                    if o.get("verdict"):            # a failed call is not cached: tried again next time
                        cache[o["key"]] = o
                        f.write(l + "\n")
        if p.returncode != 0:
            print("reviewer:", p.stderr[-300:], file=sys.stderr)
    return cache


def signals(rows3: list[dict], rows4: list[dict], reviews: dict[str, dict] | None) -> list[dict]:
    s4 = {r["instance_id"]: r for r in rows4 if r.get("agents")}
    out = []
    for r in rows3:
        ags = r.get("agents") or {}
        if not ags:
            continue
        srcs = {a: set(f for f in files_of(x.get("patch") or "") if changes_source([f])) for a, x in ags.items()}
        # T*: our tests count only when at least one candidate answer passes them (else they likely guessed wrong)
        any_pass = any(bool(x.get("passes_our_tests")) for x in ags.values() if not unreached(x))
        for a, x in ags.items():
            if unreached(x):
                continue
            other = [b for b in ags if b != a]
            e_new = x.get("existing_new_failures")
            if e_new is None:
                e_new = (s4.get(r["instance_id"], {}).get("agents", {}).get(a) or {}).get("existing_new_failures")
            key = f"{r['instance_id']}|{a}"
            rv = (reviews or {}).get(key)
            out.append({"iid": r["instance_id"], "agent": a, "key": key, "patch": x.get("patch") or "",
                        "solved": bool(x.get("truly_solved")), "cost": float(x.get("cost_usd") or 0),
                        "T": bool(x.get("passes_our_tests")), "Tstar": bool(x.get("passes_our_tests")) or not any_pass,
                        "F": focused(x.get("patch") or ""), "X": no_swallow(x.get("patch") or ""),
                        "Rno": bool(rv and rv.get("verdict") == "does_not_fix"),
                        "E": e_new == 0, "E_known": e_new is not None,
                        "S": changes_source(files_of(x.get("patch") or "")),
                        "A": bool(srcs[a]) and any(srcs[a] == srcs[b] for b in other),
                        "R": bool(rv and rv.get("verdict") == "fixes"), "R_known": rv is not None})
    return out


def table(sig: list[dict], label: str) -> str:
    n, right = len(sig), sum(s["solved"] for s in sig)
    lines = [f"{label}: {n} answers that reached the model, {right} right (hidden tests); E known for "
             f"{sum(s['E_known'] for s in sig)}, R known for {sum(s['R_known'] for s in sig)}",
             f"{'rule':28} {'accepted':>8} {'wrong':>6} {'wrong%':>7} {'right kept':>10} {'right lost':>10}  est. cost vs Opus-only"]
    for name, rule in RULES.items():
        acc = [s for s in sig if rule(s)]
        wrong = sum(not s["solved"] for s in acc)
        kept = sum(s["solved"] for s in acc)
        # every answer: its cheap cost; a rejected one also pays Opus. Opus-only: Opus on every task.
        cost = sum(s["cost"] for s in sig) + OPUS_USD * (n - len(acc))
        lines.append(f"{name:28} {len(acc):>8} {wrong:>6} {(100 * wrong / len(acc) if acc else 0):>6.0f}% {kept:>10} {right - kept:>10}"
                     f"  {100 * (1 - cost / (OPUS_USD * n)):.0f}% saved, {wrong} wrong delivered")
    lines.append(f"(Opus assumed ${OPUS_USD}/task and always right: a wrong accept is a task Opus would have solved; "
                 "the test writer's and reviewer's own cost are not in the estimate)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage3", nargs="+", required=True)
    ap.add_argument("--stage4", nargs="*", default=[])
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--label", default="answers")
    a = ap.parse_args(argv)
    rows3, rows4 = load(a.stage3), load(a.stage4)
    sig = signals(rows3, rows4, None)
    reviews = run_reviews(sig) if a.review else ({json.loads(l)["key"]: json.loads(l) for l in CACHE.read_text(encoding="utf-8").splitlines() if l.strip()} if CACHE.exists() else {})
    print(table(signals(rows3, rows4, reviews), a.label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
