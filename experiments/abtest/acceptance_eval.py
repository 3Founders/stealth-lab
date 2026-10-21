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
STRONG = "claude-sonnet-5-5"                # the strong reviewer (rules 10-11)
CACHE_STRONG = HERE / ".local" / "survey" / "reviews_strong.jsonl"
OPUS = "claude-opus-5-5"                     # the Opus reviewer (rules 12-13)
CACHE_OPUS = HERE / ".local" / "survey" / "reviews_opus.jsonl"
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
    "10 E S F X, no Rs-veto": lambda s: s["E"] and s["S"] and s["F"] and s["X"] and not s["Rs_no"],
    "11 E S and Rs": lambda s: s["E"] and s["S"] and s["Rs"],
    "12 E S and Ro": lambda s: s["E"] and s["S"] and s["Ro"],
    "13 E S A and Ro": lambda s: s["E"] and s["S"] and s["A"] and s["Ro"],
    "14 E S and C": lambda s: s["E"] and s["S"] and s["C"],
    "15 E S C and Ro": lambda s: s["E"] and s["S"] and s["C"] and s["Ro"],
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


def read_cache(path: Path) -> dict[str, dict]:
    return {json.loads(l)["key"]: json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()} if path.exists() else {}


def run_reviews(answers: list[dict], model: str | None = None, cache_path: Path = CACHE) -> dict[str, dict]:
    cache = {}
    if cache_path.exists():
        for l in cache_path.read_text(encoding="utf-8").splitlines():
            if l.strip():
                o = json.loads(l)
                cache[o["key"]] = o
    todo = [a for a in answers if a["key"] not in cache and (a["patch"] or "").strip()]
    if todo:
        prompts = problem_statements({a["iid"] for a in todo})
        lines = "".join(json.dumps({"key": a["key"], "prompt": prompts.get(a["iid"], ""), "patch": a["patch"]}) + "\n" for a in todo)
        env = review_env()
        if model:
            env["REVIEW_MODEL"] = model
        p = subprocess.run(["node", str(HERE / "acceptance_review.mjs")], input=lines, capture_output=True, text=True,
                           env=env, timeout=3600, encoding="utf-8")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("a", encoding="utf-8") as f:
            for l in p.stdout.splitlines():
                if l.strip():
                    o = json.loads(l)
                    if o.get("verdict"):            # a failed call is not cached: tried again next time
                        cache[o["key"]] = o
                        f.write(l + "\n")
        if p.returncode != 0:
            print("reviewer:", p.stderr[-300:], file=sys.stderr)
    return cache


def signals(rows3: list[dict], rows4: list[dict], reviews: dict[str, dict] | None, strong: dict[str, dict] | None = None,
            opus: dict[str, dict] | None = None) -> list[dict]:
    s4 = {r["instance_id"]: r for r in rows4 if r.get("agents")}
    out = []
    for r in rows3:
        ags = r.get("agents") or {}
        if not ags:
            continue
        # an issue whose real fix does not pass its hidden tests here grades nothing (stage 4 gold_solved)
        if s4.get(r["instance_id"], {}).get("gold_solved") is False:
            continue
        srcs = {a: set(f for f in files_of(x.get("patch") or "") if changes_source([f])) for a, x in ags.items()}
        # T*: our tests count only when at least one candidate answer passes them (else they likely guessed wrong)
        any_pass = any(bool(x.get("passes_our_tests")) for x in ags.values() if not unreached(x))
        for a, x in ags.items():
            if unreached(x):
                continue
            other = [b for b in ags if b != a]
            # a stage 4 re-judge (the existing tests run by file, fixed) is preferred to stage 3's own measurement
            j4 = s4.get(r["instance_id"], {}).get("agents", {}).get(a)
            e_new = j4.get("existing_new_failures") if j4 is not None else x.get("existing_new_failures")
            key = f"{r['instance_id']}|{a}"
            rv = (reviews or {}).get(key)
            rs = (strong or {}).get(key)
            ro = (opus or {}).get(key)
            # the label: the hidden tests; two runs on the same patch that disagree leave it unknown (left out)
            # with a stage 4 graded by the fixed harness (hidden tests' files reset to base first), its verdict is the label
            fixed = j4 is not None and "hidden_tail" in j4
            uncertain = (not fixed) and j4 is not None and bool(j4.get("truly_solved")) != bool(x.get("truly_solved"))
            label = bool(j4.get("truly_solved")) if fixed else bool(x.get("truly_solved"))
            out.append({"iid": r["instance_id"], "agent": a, "key": key, "patch": x.get("patch") or "", "uncertain": uncertain,
                        "solved": label, "cost": float(x.get("cost_usd") or 0),
                        "T": bool(x.get("passes_our_tests")), "Tstar": bool(x.get("passes_our_tests")) or not any_pass,
                        "F": focused(x.get("patch") or ""), "X": no_swallow(x.get("patch") or ""),
                        "Rno": bool(rv and rv.get("verdict") == "does_not_fix"),
                        # unknown (could not run or judge) counts as no new failure (pre-registered clarification)
                        "E": e_new in (0, None), "E_known": e_new is not None,
                        "S": changes_source(files_of(x.get("patch") or "")),
                        "A": bool(srcs[a]) and any(srcs[a] == srcs[b] for b in other),
                        "R": bool(rv and rv.get("verdict") == "fixes"), "R_known": rv is not None,
                        "Rs": bool(rs and rs.get("verdict") == "fixes"), "Rs_no": bool(rs and rs.get("verdict") == "does_not_fix"),
                        "Rs_known": rs is not None,
                        "Ro": bool(ro and ro.get("verdict") == "fixes"), "Ro_no": bool(ro and ro.get("verdict") == "does_not_fix"),
                        # C: passes the other answer's validated tests (stage 4 cross_tests); not judged = false
                        "C": bool(((s4.get(r["instance_id"], {}).get("cross") or {}).get(a) or {}).get("passes")),
                        # known: stage 4 cross-tested this issue (an answer it could not judge is left out of "cross")
                        "C_known": "cross" in s4.get(r["instance_id"], {}) and a in (s4[r["instance_id"]].get("agents") or {})})
    return out


def table(sig: list[dict], label: str) -> str:
    unsure = [s for s in sig if s.get("uncertain")]
    sig = [s for s in sig if not s.get("uncertain")]
    n, right = len(sig), sum(s["solved"] for s in sig)
    lines = [f"{label}: {n} answers that reached the model ({len(unsure)} more left out: hidden-test verdict differs between "
             f"two runs), {right} right (hidden tests); E known for "
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


# each rule in the product's form (packaging/npm lib/accept.mjs) and the strong reviewer it needs (--review-model):
# Rs and Ro are both the product's strong reviewer, on Sonnet or on Opus (docs/acceptance_rules_preregistered_2026-10-16.md)
PRODUCT = {"1": ("T", ""), "2": ("E S", ""), "3": ("E S T|R", ""), "4": ("E S R", ""), "5": ("E S A", ""), "6": ("E S A|R", ""),
           "7": ("E S F X !Rno", ""), "8": ("E S F X T* !Rno", ""), "9": ("E S T* A|R", ""),
           "10": ("E S F X !Rs_no", STRONG), "11": ("E S Rs", STRONG), "12": ("E S Rs", OPUS), "13": ("E S A Rs", OPUS),
           "14": ("E S C", ""), "15": ("E S C Rs", OPUS)}
USES_T = {"1", "3", "8", "9"}
MAX_WRONG_SHARE = 0.10        # wrong deliveries at most 10% of ALL tasks (the user's goal: within 1-2 solved tasks of Opus)
FALLBACK = "15"


def pair_rows(paths: list[str]) -> list[dict]:
    """Several runs' stage 3 files (<run>-stage3.jsonl) as one row per issue, agent "a" of run "r" named "a@r" -- the same
    names stage 4 gives a paired --src-run r1+r2 (pipeline_survey.merge_answers)."""
    by: dict[str, dict] = {}
    for p in paths:
        run = Path(p).name.removesuffix(".jsonl").removesuffix("-stage3")
        for r in load([p]):
            row = by.setdefault(r["instance_id"], {**r, "agents": {}})
            row["agents"].update({f"{a}@{run}": x for a, x in (r.get("agents") or {}).items()})
    return list(by.values())


def needs(num: str) -> set[str]:
    """The signals a rule reads that come from a reviewer or the cross-tests (each must be known before it is scored)."""
    rule, model = PRODUCT[num]
    used = {x.lstrip("!") for g in rule.split() for x in g.split("|")}
    out = {"R"} if used & {"R", "Rno"} else set()
    if used & {"Rs", "Rs_no"}:
        out.add("Ro" if model == OPUS else "Rs")
    return out | ({"C"} if "C" in used else set())


def choose(sig: list[dict], n_tasks: int, have_t: bool, missing: dict[str, int] | None = None) -> tuple[str | None, list[str]]:
    """The pre-registered choice: the largest estimated saving among the rules whose wrong deliveries are at most
    MAX_WRONG_SHARE of all tasks; ties -> the simpler rule (fewer signals); none qualifies -> rule 15. No choice while a
    signal some rule needs is missing (a rule never qualifies on a signal that was not measured)."""
    sig = [s for s in sig if not s.get("uncertain")]
    gaps = {k: v for k, v in (missing or {}).items() if v}
    lines, ok = [], []
    for name, rule in RULES.items():
        num = name.split()[0]
        if num in USES_T and not have_t:
            lines.append(f"  rule {num}: not scorable (no written tests in these runs)")
            continue
        if needs(num) & set(gaps):
            lines.append(f"  rule {num}: not scored ({', '.join(f'{k} missing for {gaps[k]}' for k in sorted(needs(num) & set(gaps)))})")
            continue
        acc = [s for s in sig if rule(s)]
        wrong = sum(not s["solved"] for s in acc)
        cost = sum(s["cost"] for s in sig) + OPUS_USD * (n_tasks - len(acc))
        saving = 1 - cost / (OPUS_USD * n_tasks)
        fits = wrong <= MAX_WRONG_SHARE * n_tasks
        lines.append(f"  rule {num}: {wrong} wrong of {n_tasks} tasks ({100 * wrong / n_tasks:.0f}%), "
                     f"{100 * saving:.0f}% saved -> {'qualifies' if fits else 'does not qualify'}")
        if fits:
            ok.append((-saving, len(PRODUCT[num][0].split()), int(num)))
    if gaps:
        lines.append("NOT DECIDED: a signal is missing (" + ", ".join(f"{k} {v}" for k, v in gaps.items())
                     + "); run with --review, and with the paired stage 4 for C")
        return None, lines
    pick = str(min(ok)[2]) if ok else FALLBACK
    lines.append(f"chosen: rule {pick}" + ("" if ok else f" (none qualifies: the pre-registered fallback)"))
    rule, model = PRODUCT[pick]
    lines.append(f'final run: --accept-rule "{rule}"' + (f" --review-model {model}" if model else ""))
    return pick, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage3", nargs="+", required=True)
    ap.add_argument("--stage4", nargs="*", default=[])
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--label", default="answers")
    ap.add_argument("--pair", action="store_true", help="the --stage3 files are runs to pair per issue (agents named agent@run)")
    ap.add_argument("--score", default="", help="score only this agent's answers (e.g. claude@survey-ab-r1); the others are "
                                                "candidates for A and C only, and are not reviewed")
    ap.add_argument("--choose", action="store_true", help="apply the pre-registered choice and print the final run's rule")
    a = ap.parse_args(argv)
    rows3, rows4 = (pair_rows(a.stage3) if a.pair else load(a.stage3)), load(a.stage4)
    pick = (lambda sig: [s for s in sig if s["agent"] == a.score]) if a.score else (lambda sig: sig)
    sig = pick(signals(rows3, rows4, None))
    reviews = run_reviews(sig) if a.review else read_cache(CACHE)
    strong = run_reviews(sig, STRONG, CACHE_STRONG) if a.review else read_cache(CACHE_STRONG)
    opus = run_reviews(sig, OPUS, CACHE_OPUS) if a.review else read_cache(CACHE_OPUS)
    sig = pick(signals(rows3, rows4, reviews, strong, opus))
    print(table(sig, a.label))
    missing = {k: sum(not s[f"{k}_known"] for s in sig if s["E"] and s["S"]) for k in ("R", "Rs", "C")}
    missing["Ro"] = sum(1 for s in sig if s["E"] and s["S"] and s["key"] not in opus)
    if any(missing.values()):
        print("signal not known for answers with E and S: " + ", ".join(f"{k} {v}" for k, v in missing.items() if v))
    if a.choose:
        # ALL tasks: every issue the hidden tests can grade, also those where the scored answer never reached its model
        gold_out = {r["instance_id"] for r in rows4 if r.get("gold_solved") is False}
        n_tasks = len({r["instance_id"] for r in rows3 if r.get("agents") and r["instance_id"] not in gold_out})
        have_t = any(r.get("written") for r in rows3)
        print(f"tasks: {n_tasks}; at most {int(MAX_WRONG_SHARE * n_tasks)} wrong deliveries qualify")
        print("\n".join(choose(sig, n_tasks, have_t, missing)[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
