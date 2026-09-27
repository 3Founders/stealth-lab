"""The sample, fixed before any agent runs: which instances are train pool / held-out test /
calibration. Deterministic from experiment.json and the pinned dataset revision.

    python design.py      # writes runs/design.json and runs/design.sha256

* Scored repos: those with >= repos_scored_min_instances instances.
* Per scored repo, instances sorted by created_at (ties: instance_id); the earliest
  round(train_fraction * n) are the TRAIN POOL (Kel learns from them), the rest are HELD-OUT TEST.
  Chronological, so no held-out issue is older than anything Kel learned from -- as in real use.
* Calibration (step-budget tuning only, never scored): instances of the remaining (small) repos.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

import swe_env
from check_env import dataset_revision

KEEP = ("instance_id", "repo", "base_commit", "problem_statement", "hints_text", "created_at", "version",
        "FAIL_TO_PASS", "PASS_TO_PASS", "environment_setup_commit")


def load_rows() -> list[dict]:
    from datasets import load_dataset

    cfg = swe_env.CONFIG["dataset"]
    rev = dataset_revision()
    ds = load_dataset(cfg["name"], split=cfg["split"], revision=rev)
    return [{k: r.get(k) for k in KEEP} for r in ds]


def build(rows: list[dict]) -> dict:
    cfg = swe_env.CONFIG
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
    scored = sorted(repo for repo, rs in by_repo.items() if len(rs) >= cfg["repos_scored_min_instances"])
    train, test = [], []
    for repo in scored:
        rs = sorted(by_repo[repo], key=lambda r: (str(r["created_at"]), r["instance_id"]))
        cut = round(cfg["split"]["train_fraction"] * len(rs))
        train += [r["instance_id"] for r in rs[:cut]]
        test += [r["instance_id"] for r in rs[cut:]]
    small = sorted(r["instance_id"] for repo, rs in by_repo.items() if repo not in scored for r in rs)
    calib = sorted(small, key=lambda i: hashlib.sha256(f"{cfg['split']['seed']}|{i}".encode()).hexdigest())
    calib = calib[: cfg["calibration"]["max_instances"]]
    return {"dataset": cfg["dataset"]["name"], "dataset_revision": dataset_revision(), "scored_repos": scored,
            "train": train, "test": test, "calibration": calib}


def main() -> None:
    rows = load_rows()
    design = build(rows)
    (swe_env.RUNS / "instances.json").write_text(json.dumps({r["instance_id"]: r for r in rows}, default=str),
                                                 encoding="utf-8")
    text = json.dumps(design, indent=1)
    (swe_env.RUNS / "design.json").write_text(text, encoding="utf-8")
    digest = hashlib.sha256((text + json.dumps(swe_env.CONFIG, sort_keys=True)).encode()).hexdigest()
    (swe_env.RUNS / "design.sha256").write_text(digest + "\n", encoding="utf-8")
    repo_of = {r["instance_id"]: r["repo"] for r in rows}
    print("scored repos:", design["scored_repos"])
    print("train:", len(design["train"]), dict(Counter(repo_of[i] for i in design["train"])))
    print("test:", len(design["test"]), dict(Counter(repo_of[i] for i in design["test"])))
    print("calibration:", len(design["calibration"]))
    print("design sha256:", digest)


if __name__ == "__main__":
    main()
