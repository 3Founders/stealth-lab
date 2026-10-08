"""The pre-registered analysis (PREREGISTRATION.md section 7). Run after grading:

    .venv/Scripts/python analyze.py [--part test]       # runs/analysis.json + runs/REPORT.md

Every comparison listed in the pre-registration is reported, whatever its direction; nothing is dropped for being
null or negative. Primary family (Holm): resolve and right-cause, L1 vs A0. Everything else is secondary
(Benjamini-Hochberg across the secondary family) or exploratory (no correction, labelled as such).
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict

import numpy as np

import stats
from common import ARMS, CONFIG, RUNS, load_jsonl, read_json, write_json

PRIMARY = [("resolved", "A0", "L1"), ("root_cause_right", "A0", "L1")]
PAIRS = [("A0", "L1"), ("L1", "L2"), ("L2", "L3"), ("A0", "L2"), ("A0", "L3")]
BINARY = ("resolved", "root_cause_right", "accept")
CONTINUOUS = ("score", "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd", "steps", "wall_seconds")


def price(rec: dict) -> float | None:
    p = CONFIG["model"]["price_per_mtok"]
    if p.get("input") is None or p.get("output") is None:
        return None
    u = rec.get("usage") or {}
    return (u.get("prompt_tokens", 0) * p["input"] + u.get("completion_tokens", 0) * p["output"]) / 1e6


def table(part: str, errors_as_failure: bool = False) -> dict[str, dict[str, dict]]:
    """task -> arm -> outcomes. A missing outcome stays missing (None) unless errors_as_failure."""
    rc = read_json(RUNS / "right_cause.json")["primary"] if (RUNS / "right_cause.json").exists() else {}
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for arm in ARMS:
        att = {r["instance_id"]: r for r in load_jsonl(RUNS / f"attempts_{part}_{arm}.jsonl")
               if not r.get("environmental_failure")}
        tests = read_json(RUNS / f"tests_{part}_{arm}.json") if (RUNS / f"tests_{part}_{arm}.json").exists() else {}
        for iid, rec in att.items():
            u = rec.get("usage") or {}
            t = tests.get(iid)
            resolved = None
            if t and t["status"] != "error":
                resolved = bool(t["resolved"])
            elif errors_as_failure:
                resolved = False
            g = rc.get(iid, {}).get(arm)
            out[iid][arm] = {
                "resolved": resolved,
                "root_cause_right": g["root_cause_right"] if g else None,
                "accept": g["accept"] if g else None,
                "score": g["score"] if g else None,
                "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
                "total_tokens": (u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0) if u else None,
                "cost_usd": price(rec), "steps": rec.get("steps"), "wall_seconds": rec.get("wall_seconds"),
                "reused_from": rec.get("reused_from"),
            }
    return out


def paired(tab, ids, repo, metric, a, b):
    keep = [i for i in ids if a in tab[i] and b in tab[i] and tab[i][a][metric] is not None
            and tab[i][b][metric] is not None]
    x = [tab[i][a][metric] for i in keep]
    y = [tab[i][b][metric] for i in keep]
    cl = [repo[i] for i in keep]
    return keep, x, y, cl


def run(part: str, task_set: str = "valid") -> dict:
    """task_set "valid": tasks that passed the gold/empty validity check (valid_tasks.json; the primary set when
    it exists); "design": every designed task (sensitivity for the right-cause outcome)."""
    inst = read_json(RUNS / "instances.json")
    design = read_json(RUNS / "design.json")[part]
    vpath = RUNS / "valid_tasks.json"
    ids = design
    if task_set == "valid" and vpath.exists():
        valid = set(read_json(vpath)["valid"])
        ids = [i for i in design if i in valid]
    repo = {i: inst[i]["repo"] for i in ids}
    res: dict = {"part": part, "task_set": task_set if vpath.exists() or task_set == "design" else "design",
                 "n_design": len(design), "n_scored": len(ids), "arms": {}, "primary": {}, "secondary": {},
                 "sensitivity": {}, "exploratory": {}}
    for errors_as_failure, key in ((False, None), (True, "errors_as_failure")):
        tab = table(part, errors_as_failure)
        if key:
            for metric, a, b in PRIMARY:
                keep, x, y, cl = paired(tab, ids, repo, metric, a, b)
                if keep:
                    res["sensitivity"][f"{metric}:{b}_vs_{a}"] = stats.compare_binary(x, y, cl)
            continue
        for arm in ARMS:
            row = {}
            for m in BINARY + CONTINUOUS:
                vals = [tab[i][arm][m] for i in ids if arm in tab[i] and tab[i][arm][m] is not None]
                if not vals:
                    continue
                if m in BINARY:
                    k = int(sum(vals))
                    row[m] = {"n": len(vals), "rate": k / len(vals), "ci": stats.wilson(k, len(vals))}
                else:
                    row[m] = {"n": len(vals), "mean": float(np.mean(vals)), "median": float(np.median(vals))}
            costs = [tab[i][arm]["cost_usd"] for i in ids if arm in tab[i] and tab[i][arm]["cost_usd"] is not None]
            wins = [tab[i][arm]["resolved"] for i in ids if arm in tab[i] and tab[i][arm]["resolved"] is not None]
            if costs and wins and sum(wins):
                row["cost_per_resolved_usd"] = float(sum(costs) / sum(wins))
            toks = [tab[i][arm]["total_tokens"] for i in ids if arm in tab[i] and tab[i][arm]["total_tokens"]]
            if toks and wins and sum(wins):
                row["tokens_per_resolved"] = float(sum(toks) / sum(wins))
            row["reused_A0"] = sum(1 for i in ids if arm in tab[i] and tab[i][arm]["reused_from"] == "A0")
            res["arms"][arm] = row
        pvals = {}
        for metric, a, b in PRIMARY:
            keep, x, y, cl = paired(tab, ids, repo, metric, a, b)
            if keep:
                r = stats.compare_binary(x, y, cl)
                res["primary"][f"{metric}:{b}_vs_{a}"] = r
                pvals[f"{metric}:{b}_vs_{a}"] = r["p_mcnemar"]
        for k, p in stats.holm(pvals).items():
            res["primary"][k]["p_holm"] = p
        sec_p = {}
        for a, b in PAIRS:
            for metric in BINARY + CONTINUOUS:
                name = f"{metric}:{b}_vs_{a}"
                if name in res["primary"]:
                    continue
                keep, x, y, cl = paired(tab, ids, repo, metric, a, b)
                if len(keep) < 5:
                    continue
                r = stats.compare_binary(x, y, cl) if metric in BINARY else stats.compare_continuous(x, y, cl)
                res["secondary"][name] = r
                sec_p[name] = r.get("p_mcnemar", r.get("p_wilcoxon"))
        for k, p in stats.benjamini_hochberg(sec_p).items():
            res["secondary"][k]["q_bh"] = p
        # exploratory: L1 vs A0 by language and by amount of earlier history
        for metric in ("resolved", "root_cause_right"):
            for label, groups in (("language", lambda i: inst[i]["language"]),
                                  ("history", lambda i: "history<30" if inst[i]["history_goals"] < 30 else "history>=30")):
                by = defaultdict(list)
                for i in ids:
                    by[groups(i)].append(i)
                for g, gi in by.items():
                    keep, x, y, cl = paired(tab, gi, repo, metric, "A0", "L1")
                    if len(keep) >= 10:
                        res["exploratory"][f"{metric}:L1_vs_A0:{label}={g}"] = stats.compare_binary(x, y, cl)
        res["coverage"] = {arm: sum(1 for i in ids if arm in tab[i]) for arm in ARMS}
    sec = RUNS / "right_cause.json"
    if sec.exists():
        rc = read_json(sec)
        a, b = [], []
        for iid, arms in rc.get("second", {}).items():
            for arm, v in arms.items():
                if arm in rc["primary"].get(iid, {}):
                    a.append(rc["primary"][iid][arm]["root_cause_right"])
                    b.append(v["root_cause_right"])
        res["inter_rater"] = {"n": len(a), "agreement": float(np.mean(np.array(a) == np.array(b))) if a else None,
                              "kappa": stats.cohen_kappa(a, b) if a else None}
    return res


def fmt(v, pct=False):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{100 * v:.1f}%" if pct else (f"{v:.3g}" if isinstance(v, float) else str(v))


def report(res: dict) -> str:
    L = [f"# Local-vs-global evaluation: results ({res['part']}, {res['n_scored']} of {res['n_design']} tasks, "
         f"set: {res['task_set']})", "",
         "Generated by analyze.py from the pre-registered plan. All comparisons are listed, including null and "
         "negative ones.", "", "## Per arm", "",
         "| arm | resolved | right cause | accept | mean score | mean tokens | mean cost | tokens / resolved |",
         "|---|---|---|---|---|---|---|---|"]
    for arm, r in res["arms"].items():
        g = lambda m: (f"{fmt(r[m]['rate'], True)} [{fmt(r[m]['ci'][0], True)}, {fmt(r[m]['ci'][1], True)}] (n={r[m]['n']})"  # noqa: E731
                       if m in r else "-")
        L.append(f"| {arm} | {g('resolved')} | {g('root_cause_right')} | {g('accept')} | "
                 f"{fmt(r.get('score', {}).get('mean'))} | {fmt(r.get('total_tokens', {}).get('mean'))} | "
                 f"{fmt(r.get('cost_usd', {}).get('mean'))} | {fmt(r.get('tokens_per_resolved'))} |")
    for fam in ("primary", "secondary", "sensitivity", "exploratory"):
        if not res[fam]:
            continue
        L += ["", f"## {fam.capitalize()}", "",
              "| comparison | n | baseline | treatment | diff [95% CI, repo bootstrap] | p | adj. p | effect |",
              "|---|---|---|---|---|---|---|---|"]
        for name, r in res[fam].items():
            binary = "p_mcnemar" in r
            base = fmt(r["rate_baseline"], True) if binary else fmt(r["mean_baseline"])
            treat = fmt(r["rate_treatment"], True) if binary else fmt(r["mean_treatment"])
            diff = (f"{fmt(r['diff'], binary)} [{fmt(r['diff_ci'][0], binary)}, {fmt(r['diff_ci'][1], binary)}]")
            p = r.get("p_mcnemar", r.get("p_wilcoxon"))
            adj = r.get("p_holm", r.get("q_bh"))
            eff = (f"OR {fmt(r['odds_ratio'])}, h {fmt(r['cohens_h'])}, {r['only_baseline']}/{r['only_treatment']} discordant"
                   if binary else f"dz {fmt(r['cohens_dz'])}, rel {fmt(r['relative'], True)}")
            L.append(f"| {name} | {r['n']} | {base} | {treat} | {diff} | {fmt(p)} | {fmt(adj)} | {eff} |")
    if res.get("inter_rater"):
        ir = res["inter_rater"]
        L += ["", f"Inter-rater (root_cause_right, double-graded batches): n={ir['n']}, agreement "
                  f"{fmt(ir['agreement'], True)}, kappa {fmt(ir['kappa'])}"]
    L += ["", f"Coverage (tasks with a scored attempt per arm): {res.get('coverage')}"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="test")
    a = ap.parse_args()
    res = run(a.part, "valid")
    write_json(RUNS / "analysis.json", res)
    text = report(res)
    if res["task_set"] == "valid":   # right cause does not need the tests: also report it on every designed task
        full = run(a.part, "design")
        write_json(RUNS / "analysis_all_tasks.json", full)
        text += "\n\n# Sensitivity: every designed task (including tasks whose tests failed the validity check)\n\n"
        text += report(full)
    (RUNS / "REPORT.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
