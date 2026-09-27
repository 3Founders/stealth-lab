"""The SWE-rebench sample, fixed before any agent runs. Run with the rebench config selected:

    set KEL_SWEBENCH_CONFIG=experiments/swebench_rebench/experiment.json
    python experiments/swebench_rebench/design.py     # writes runs/{instances,design,grading_source}.json

* Only tasks with a prebuilt SWE-rebench image (`docker_image`) -- building environments from install_config is
  slow and unvalidated.
* Per repo, chronological around the agent model's knowledge cutoff: tasks created BEFORE it are the train pool
  (Kel learns from them; the model may have seen them, which does not matter), tasks created ON/AFTER it are
  held out (the model cannot have seen them). So every held-out task is newer than everything Kel learned from
  AND than the model's training data. Caps keep one repo (sqlglot) from dominating.
* instances.json holds exactly the fields experiments/swebench uses for prompts -- never the gold patch, and
  never SWE-rebench's `requirements` column (pip requirements, which the agent prompt would present as task
  requirements). grading_source.json holds what render_grading_dataset.py needs (patches, install_config, image).
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "swebench"))
import swe_env  # noqa: E402

KEEP = ("instance_id", "repo", "base_commit", "problem_statement", "hints_text", "created_at", "version",
        "FAIL_TO_PASS", "PASS_TO_PASS", "environment_setup_commit")
GRADING = ("instance_id", "repo", "version", "base_commit", "patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS",
           "environment_setup_commit", "install_config", "docker_image", "image_name")


def load_rows() -> list[dict]:
    from datasets import load_dataset

    cfg = swe_env.CONFIG["dataset"]
    ds = load_dataset(cfg["name"], split=cfg["split"], revision=cfg["revision"])
    return [dict(r) for r in ds if r.get("docker_image")]


def _h(i: str) -> str:
    return hashlib.sha256(f"{swe_env.CONFIG['split']['seed']}|{i}".encode()).hexdigest()


def build(rows: list[dict]) -> dict:
    sp = swe_env.CONFIG["split"]
    cutoff = sp["model_cutoff"]
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
    train, test, scored = [], [], []
    for repo in sorted(by_repo):
        rs = sorted(by_repo[repo], key=lambda r: (str(r["created_at"]), r["instance_id"]))
        before = [r for r in rs if str(r["created_at"]) < cutoff]
        after = [r for r in rs if str(r["created_at"]) >= cutoff]
        if len(after) < sp["min_test"] or len(before) < sp["min_train"]:
            continue
        scored.append(repo)
        train += [r["instance_id"] for r in before[-sp["max_train_per_repo"]:]]   # the most recent before cutoff
        test += [r["instance_id"] for r in after[:sp["max_test_per_repo"]]]       # the earliest after cutoff
    pool = [r["instance_id"] for r in rows if r["repo"] not in scored and str(r["created_at"]) >= cutoff]
    calib = sorted(pool, key=_h)[: swe_env.CONFIG["calibration"]["max_instances"]]
    return {"dataset": swe_env.CONFIG["dataset"]["name"], "dataset_revision": swe_env.CONFIG["dataset"]["revision"],
            "model_cutoff": cutoff, "scored_repos": scored, "train": train, "test": test, "calibration": calib}


def main() -> None:
    rows = load_rows()
    design = build(rows)
    used = set(design["train"]) | set(design["test"]) | set(design["calibration"])
    rows = [r for r in rows if r["instance_id"] in used]
    swe_env.RUNS.mkdir(parents=True, exist_ok=True)
    (swe_env.RUNS / "instances.json").write_text(
        json.dumps({r["instance_id"]: {k: r.get(k) for k in KEEP} for r in rows}, default=str), encoding="utf-8")
    (swe_env.RUNS / "grading_source.json").write_text(
        json.dumps([{k: r.get(k) for k in GRADING} for r in rows], default=str), encoding="utf-8")
    text = json.dumps(design, indent=1)
    (swe_env.RUNS / "design.json").write_text(text, encoding="utf-8")
    digest = hashlib.sha256((text + json.dumps(swe_env.CONFIG, sort_keys=True)).encode()).hexdigest()
    (swe_env.RUNS / "design.sha256").write_text(digest + "\n", encoding="utf-8")
    repo_of = {r["instance_id"]: r["repo"] for r in rows}
    print("scored repos:", len(design["scored_repos"]), design["scored_repos"])
    print("train:", len(design["train"]), dict(Counter(repo_of[i] for i in design["train"])))
    print("test:", len(design["test"]), dict(Counter(repo_of[i] for i in design["test"])))
    print("calibration:", len(design["calibration"]), dict(Counter(repo_of[i] for i in design["calibration"])))
    print("design sha256:", digest)


if __name__ == "__main__":
    main()
