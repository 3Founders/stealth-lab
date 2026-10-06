"""Fix the task sample before any agent runs (PREREGISTRATION.md section 3).

    .venv/Scripts/python build_tasks.py            # writes runs/{instances,design,grading_source,corpus_dates}.json

* Tasks come from SWE-rebench-V2 (pinned revision): every V2 task ships a prebuilt image plus its own test
  command and log parser, so the real tests can grade it.
* Unseen: the instance is NOT in the Kel corpus and was not used in the earlier experiment (queries.json).
* Not memorised: created on/after the agent model's knowledge cutoff.
* Has history: its repo has >= min_history corpus Goals created strictly BEFORE the task.
* At most max_per_repo tasks per repo (the earliest ones), so a few big repos cannot dominate.
* Calibration tasks come from repos that are NOT scored.

instances.json never holds the gold patch or the test patch; grading_source.json holds what grading needs.
"""
from __future__ import annotations

import bisect
import json
from collections import Counter, defaultdict

import pyarrow.parquet as pq

from common import CONFIG, RUNS, corpus_dir, iso, parquet_path, repo_of, stable_hash, write_json

V2_COLS = ["instance_id", "repo", "base_commit", "created_at", "problem_statement", "language", "image_name",
           "install_config", "patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"]


def _listify(v):
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return [v]
    return list(v)


def load_corpus_refs() -> list[str]:
    refs = []
    with open(corpus_dir() / CONFIG["corpus"]["jsonl"], encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                refs.append(json.loads(line).get("row_ref"))
    return refs


def load_dates() -> dict[str, str]:
    """instance_id -> created_at for every row of the pinned history datasets."""
    dates: dict[str, str] = {}
    for ds in CONFIG["history_datasets"]:
        for f in ds["files"]:
            t = pq.read_table(parquet_path(ds["name"], f), columns=["instance_id", "created_at"]).to_pylist()
            for r in t:
                dates.setdefault(r["instance_id"], iso(r["created_at"]))
    return dates


def history_index(refs: list[str], dates: dict[str, str]) -> dict[str, list[str]]:
    """repo -> sorted creation dates of its corpus Goals (undated Goals are left out: they cannot be ordered)."""
    by_repo: dict[str, list[str]] = defaultdict(list)
    for ref in refs:
        repo = repo_of(ref)
        if repo and ref in dates:
            by_repo[repo].append(dates[ref])
    return {k: sorted(v) for k, v in by_repo.items()}


def earlier_count(hist: dict[str, list[str]], repo: str, created_at: str) -> int:
    return bisect.bisect_left(hist.get(repo.lower(), []), created_at)


def select(rows: list[dict], hist: dict[str, list[str]], excluded: set[str], sp: dict) -> tuple[list[str], list[str], dict]:
    """Pure selection: (test ids, calibration ids, report)."""
    eligible: dict[str, list[dict]] = defaultdict(list)
    reasons = Counter()
    for r in rows:
        if r["instance_id"] in excluded:
            reasons["seen (corpus or earlier experiment)"] += 1
        elif iso(r["created_at"]) < sp["model_cutoff"]:
            reasons["before model cutoff"] += 1
        elif not (r.get("problem_statement") or "").strip() or not _listify(r.get("FAIL_TO_PASS")) or not r.get("image_name"):
            reasons["no statement / FAIL_TO_PASS / image"] += 1
        elif earlier_count(hist, r["repo"], iso(r["created_at"])) < sp["min_history"]:
            reasons["too little earlier history"] += 1
        else:
            eligible[r["repo"]].append(r)
    capped = {repo: sorted(v, key=lambda r: (iso(r["created_at"]), r["instance_id"]))[: sp["max_per_repo"]]
              for repo, v in eligible.items()}
    repos = sorted(capped, key=lambda repo: stable_hash(sp["seed"], "repo", repo))
    calib, calib_repos = [], set()
    for repo in reversed(repos):          # calibration: one task each from the LAST repos in hash order
        if len(calib) >= sp["n_calibration"]:
            break
        calib.append(capped[repo][0]["instance_id"])
        calib_repos.add(repo)
    pool = [r["instance_id"] for repo in repos if repo not in calib_repos for r in capped[repo]]
    test = sorted(pool, key=lambda i: stable_hash(sp["seed"], "task", i))[: sp["n_test"]]
    report = {"excluded_reasons": dict(reasons), "eligible_tasks": sum(len(v) for v in eligible.values()),
              "eligible_repos": len(eligible), "after_cap": sum(len(v) for v in capped.values()),
              "pool_for_test": len(pool), "n_test": len(test), "n_calibration": len(calib),
              "shortfall": max(0, sp["n_test"] - len(test))}
    return test, calib, report


def main() -> None:
    sp = CONFIG["split"]
    ds = CONFIG["dataset"]
    rows = pq.read_table(parquet_path(ds["name"], ds["file"]), columns=V2_COLS).to_pylist()
    refs = load_corpus_refs()
    dates = load_dates()
    hist = history_index(refs, dates)
    excluded = {r for r in refs if r}
    for name in sp["exclude_ids_files"]:
        p = corpus_dir() / name
        if p.exists():
            excluded |= {q["id"] for q in json.loads(p.read_text(encoding="utf-8"))}
    test, calib, report = select(rows, hist, excluded, sp)
    by_id = {r["instance_id"]: r for r in rows}
    chosen = test + calib
    instances = {i: {"instance_id": i, "repo": by_id[i]["repo"], "base_commit": by_id[i]["base_commit"],
                     "problem_statement": by_id[i]["problem_statement"], "hints_text": "",
                     "created_at": iso(by_id[i]["created_at"]), "version": "", "language": by_id[i]["language"],
                     "history_goals": earlier_count(hist, by_id[i]["repo"], iso(by_id[i]["created_at"]))}
                 for i in chosen}
    grading = {i: {"instance_id": i, "repo": by_id[i]["repo"], "base_commit": by_id[i]["base_commit"],
                   "image_name": by_id[i]["image_name"], "install_config": by_id[i]["install_config"],
                   "patch": by_id[i]["patch"], "test_patch": by_id[i]["test_patch"],
                   "FAIL_TO_PASS": _listify(by_id[i]["FAIL_TO_PASS"]),
                   "PASS_TO_PASS": _listify(by_id[i]["PASS_TO_PASS"])}
               for i in chosen}
    write_json(RUNS / "instances.json", instances)
    write_json(RUNS / "design.json", {"calibration": calib, "train": [], "test": test})
    write_json(RUNS / "grading_source.json", grading)
    write_json(RUNS / "corpus_dates.json", {r: dates[r] for r in refs if r in dates})
    langs = Counter(instances[i]["language"] for i in test)
    report.update({"languages": dict(langs.most_common()), "repos_in_test": len({instances[i]["repo"] for i in test})})
    write_json(RUNS / "design_report.json", report)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
