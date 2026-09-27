"""The preregistered analysis (docs/knowledge_side_improvements.md, 'Next experiment: SWE-bench').

    python analyze.py      # writes runs/report.json and prints the verdict

Primary: held-out resolved rate, K vs A0, paired per instance: exact McNemar p and a 95% bootstrap CI
stratified by repo (10,000 resamples, seed 0).
Decision rule (fixed): "Kel knowledge helps on repo-level work" only if
  (1) K - A0 > 0 with CI excluding 0 and McNemar p < 0.05,
  (2) K - C2 (placebo) lower CI bound > -0.03,
  (3) no scored repo with a significant regression (McNemar p < 0.05, K < A0),
  (4) mean cost (tokens) per RESOLVED instance under K is at most 1.2x A0's,
  (5) K - A0r > 0, where A0r is a fresh repeat of A0 run at the same time as K (the time-matched baseline).
Run-to-run noise is reported as A0r - A0 (discordant pairs = instances whose outcome flipped with no change).
Arm KP (Kel used the way the product is used, kprod.py) gets its own verdict, "PRODUCT HELPS", under
rules (1), (3), (4) and (5) with KP in place of K (no placebo exists for a workflow). KP's tokens include
the survey_repo episodes, charged to each repo's held-out instances in equal shares.
Arm KH (the Claude Code knowledge hook, kprod.py; DS-1000 round 5's confirmed arm) gets the same product verdict
("HOOK HELPS") under the same rules with KH in place of K, and the same survey charge. Reported only when KH is
among the configured arms.
Harness errors (`status: error`) are excluded from EVERY arm for that instance, never counted as failures.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict

import swe_env
from generate import design, instances, load_jsonl

ARMS = swe_env.CONFIG["arms"]


def mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def paired(ids, x: dict, y: dict, repo_of: dict, seed: int = 0) -> dict:
    ids = [i for i in ids if i in x and i in y]
    if not ids:
        return {"n": 0}
    d = {i: int(y[i]) - int(x[i]) for i in ids}
    by_repo = defaultdict(list)
    for i in ids:
        by_repo[repo_of[i]].append(d[i])
    rng = random.Random(seed)
    boots = []
    for _ in range(10_000):
        s = n = 0
        for vals in by_repo.values():
            sample = [vals[rng.randrange(len(vals))] for _ in vals]
            s += sum(sample)
            n += len(sample)
        boots.append(s / n)
    boots.sort()
    b, c = sum(v == 1 for v in d.values()), sum(v == -1 for v in d.values())
    return {"n": len(ids), "x_rate": round(sum(x[i] for i in ids) / len(ids), 3),
            "y_rate": round(sum(y[i] for i in ids) / len(ids), 3), "delta": round(sum(d.values()) / len(ids), 3),
            "ci95": [round(boots[249], 3), round(boots[9750], 3)], "gained": b, "lost": c,
            "mcnemar_p": round(mcnemar(b, c), 4)}


def main() -> None:
    inst, test = instances(), design()["test"]
    repo_of = {i: inst[i]["repo"] for i in test}
    grades, tokens = {}, {}
    for arm in ARMS:
        g = json.loads((swe_env.RUNS / f"grades_test_{arm}.json").read_text(encoding="utf-8"))
        grades[arm] = g
        tokens[arm] = {r["instance_id"]: (r.get("usage") or {}).get("prompt_tokens", 0) +
                       (r.get("usage") or {}).get("completion_tokens", 0)
                       for r in load_jsonl(swe_env.RUNS / f"attempts_test_{arm}.jsonl") if not r.get("environmental_failure")}
    errored = {i for arm in ARMS for i, g in grades[arm].items() if g["status"] == "error"}
    ids = [i for i in test if i not in errored and all(i in grades[a] for a in ARMS)]
    res = {arm: {i: grades[arm][i]["resolved"] for i in ids} for arm in ARMS}

    rep = {"design_sha256": (swe_env.RUNS / "design.sha256").read_text().strip(),
           "pinned": json.loads((swe_env.RUNS / "pinned.json").read_text(encoding="utf-8")),
           "n_scored": len(ids), "excluded_harness_errors": sorted(errored)}
    rep["primary_K_minus_A0"] = paired(ids, res["A0"], res["K"], repo_of)
    rep["secondary"] = {f"{y}-{x}": paired(ids, res[x], res[y], repo_of)
                        for x, y in (("A0", "E"), ("E", "K"), ("A0", "C1"), ("A0", "C2"), ("C2", "K"), ("C1", "K"),
                                     ("A0r", "K"), ("A0", "KP"), ("A0r", "KP"), ("K", "KP"), ("E", "KP"),
                                     *((("A0", "KH"), ("A0r", "KH"), ("K", "KH"), ("KP", "KH")) if "KH" in ARMS else ()))}
    noise = paired(ids, res["A0"], res["A0r"], repo_of)
    rep["noise_A0r_minus_A0"] = {**noise, "flip_rate": round((noise["gained"] + noise["lost"]) / max(noise["n"], 1), 3)}
    rep["per_repo_K_minus_A0"] = {repo: paired([i for i in ids if repo_of[i] == repo], res["A0"], res["K"], repo_of)
                                  for repo in sorted(set(repo_of[i] for i in ids))}
    notes_k = json.loads((swe_env.RUNS / "notes_K.json").read_text(encoding="utf-8"))
    with_notes = [i for i in ids if (notes_k.get(i) or {}).get("text")]
    rep["K_minus_A0_where_K_had_notes"] = paired(with_notes, res["A0"], res["K"], repo_of)
    rep["coverage_K"] = {"with_notes": len(with_notes), "of": len(ids)}
    import kprod

    survey = kprod.survey_tokens()
    per_repo_n = defaultdict(int)
    for i in ids:
        per_repo_n[repo_of[i]] += 1
    product_arms = [arm for arm in ("KP", "KH") if arm in ARMS]
    for arm in product_arms:   # both use the survey's claims.md
        tokens[arm] = {i: t + survey.get(repo_of[i], 0) / per_repo_n[repo_of[i]] for i, t in tokens[arm].items()}
    cost = {}
    for arm in ARMS:
        solved = [i for i in ids if res[arm][i]]
        total = round(sum(tokens[arm].get(i, 0) for i in ids))
        cost[arm] = {"resolved": len(solved), "rate": round(len(solved) / max(len(ids), 1), 3),
                     "tokens_total": total, "tokens_per_resolved": round(total / len(solved)) if solved else None}
    rep["cost"] = cost
    p = rep["primary_K_minus_A0"]
    placebo = rep["secondary"]["K-C2"]
    regress = [r for r, v in rep["per_repo_K_minus_A0"].items() if v.get("n") and v["delta"] < 0 and v["mcnemar_p"] < 0.05]
    tok_ratio = (cost["K"]["tokens_per_resolved"] or float("inf")) / max(cost["A0"]["tokens_per_resolved"] or 1, 1)
    rep["decision"] = {
        "1_K_beats_A0": bool(p.get("n") and p["ci95"][0] > 0 and p["mcnemar_p"] < 0.05),
        "2_not_explained_by_placebo": bool(placebo.get("n") and placebo["ci95"][0] > -0.03),
        "3_no_repo_regression": not regress, "regressing_repos": regress,
        "4_cost_per_resolved_ratio": round(tok_ratio, 3), "4_cost_ok": tok_ratio <= 1.2,
        "5_K_beats_time_matched_A0r": bool(rep["secondary"]["K-A0r"].get("n") and rep["secondary"]["K-A0r"]["delta"] > 0),
    }
    rep["decision"]["VERDICT"] = ("KNOWLEDGE HELPS" if all(rep["decision"][k] for k in
                                  ("1_K_beats_A0", "2_not_explained_by_placebo", "3_no_repo_regression", "4_cost_ok",
                                   "5_K_beats_time_matched_A0r"))
                                  else "NOT SHOWN")
    for arm in product_arms:
        rep[f"{arm}_usage"], rep[f"decision_{arm}"] = product_decision(arm, ids, res, cost, rep, repo_of, survey)
    (swe_env.RUNS / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("n_scored", "primary_K_minus_A0", "noise_A0r_minus_A0", "secondary",
                                          "coverage_K", "cost", "decision",
                                          *(f"{a}_{s}" if s == "usage" else f"decision_{a}"
                                            for a in product_arms for s in ("usage", "decision")))}, indent=1))


def product_decision(arm: str, ids, res, cost, rep, repo_of, survey) -> tuple[dict, dict]:
    """KP's and KH's product verdict: rules 1, 3, 4 and 5 with the arm in place of K (a workflow has no placebo)."""
    d = rep["secondary"][f"{arm}-A0"]
    regress = [r for r in sorted(set(repo_of[i] for i in ids))
               if (v := paired([i for i in ids if repo_of[i] == r], res["A0"], res[arm], repo_of)).get("n")
               and v["delta"] < 0 and v["mcnemar_p"] < 0.05]
    ratio = (cost[arm]["tokens_per_resolved"] or float("inf")) / max(cost["A0"]["tokens_per_resolved"] or 1, 1)
    attempts = {r["instance_id"]: r for r in load_jsonl(swe_env.RUNS / f"attempts_test_{arm}.jsonl")
                if not r.get("environmental_failure")}
    calls = [attempts[i].get("kel_calls") or [] for i in ids if i in attempts]
    usage = {
        "episodes": len(calls),
        "called_find_ways": sum(any(c["tool"] == "find_ways" for c in cs) for cs in calls),
        "got_resolved": sum(any(c.get("outcome") == "resolved" for c in cs) for cs in calls),
        "got_verified_solution": sum(any(c.get("verified_solution") for c in cs) for cs in calls),
        "mean_kel_calls": round(sum(len(cs) for cs in calls) / max(len(calls), 1), 2),
        "survey_tokens": survey}
    hooks = [attempts[i]["hook"] for i in ids if i in attempts and attempts[i].get("hook")]
    if hooks:
        outcomes: dict = {}
        for h in hooks:
            outcomes[str(h.get("outcome"))] = outcomes.get(str(h.get("outcome")), 0) + 1
        usage["hook"] = {"episodes": len(hooks), "outcomes": outcomes,
                         "with_related_examples": sum(1 for h in hooks if h.get("related_examples")),
                         "with_suggested": sum(1 for h in hooks if h.get("suggested")),
                         "with_procedures": sum(1 for h in hooks if h.get("procedures")),
                         "empty_context": sum(1 for h in hooks if not h.get("context_chars")),
                         "context_chars_mean": round(sum(h.get("context_chars") or 0 for h in hooks) / len(hooks))}
    decision = {
        f"1_{arm}_beats_A0": bool(d.get("n") and d["ci95"][0] > 0 and d["mcnemar_p"] < 0.05),
        "3_no_repo_regression": not regress, "regressing_repos": regress,
        "4_cost_per_resolved_ratio": round(ratio, 3), "4_cost_ok": ratio <= 1.2,
        f"5_{arm}_beats_time_matched_A0r": bool(rep["secondary"][f"{arm}-A0r"].get("n")
                                                and rep["secondary"][f"{arm}-A0r"]["delta"] > 0),
    }
    decision["VERDICT"] = (("PRODUCT HELPS" if arm == "KP" else "HOOK HELPS") if all(decision[k] for k in (
        f"1_{arm}_beats_A0", "3_no_repo_regression", "4_cost_ok", f"5_{arm}_beats_time_matched_A0r")) else "NOT SHOWN")
    return usage, decision


if __name__ == "__main__":
    main()
