"""Grade predictions with the OFFICIAL SWE-bench harness, unmodified -- in local Docker, or on Modal when
experiment.json grading.backend is "modal" (the harness's own `--modal true`; images build and run remotely,
reports land in the same runs/logs/run_evaluation/<run_id>/ tree, so collection is identical).

    python grade.py --gold                     # sanity check FIRST: gold patches on the calibration set must all resolve
    python grade.py --tag train_A0             # grades runs/predictions_train_A0.jsonl
    python grade.py --tag test_K

Runs `python -m swebench.harness.run_evaluation` inside runs/ (so its logs land in runs/logs/), then
reads every per-instance report.json and writes runs/grades_<tag>.json:
    {instance_id: {"resolved": bool, "status": "resolved|unresolved|empty_patch|error"}}
An instance whose container errored is `error` -- it is re-graded, never counted as a failure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import swe_env
from check_env import require_pinned


def _dataset_name(g: dict) -> str:
    """HF dataset id, or a local .json/.jsonl grading dataset resolved next to the experiment config."""
    name = g.get("dataset", swe_env.CONFIG["dataset"]["name"])
    return str(swe_env.CONFIG_PATH.parent / name) if name.endswith((".json", ".jsonl")) else name


def run_harness(predictions: str, run_id: str, instance_ids: list[str]) -> None:
    g = swe_env.CONFIG["grading"]
    cmd = [sys.executable, "-m", "swebench.harness.run_evaluation",
           "--dataset_name", _dataset_name(g), "--split", swe_env.CONFIG["dataset"]["split"],
           "--predictions_path", predictions, "--run_id", run_id, "--max_workers", str(g["max_workers"]),
           "--timeout", str(g["timeout_s"])]         # swebench 5.x removed --cache_level
    if g.get("backend", "docker") == "gce":      # the official harness on Compute Engine, via the GCS queue
        import gce_queue
        if predictions == "gold":
            gce_queue.gold_ids(run_id, instance_ids)
        else:
            gce_queue.submit_rows(run_id, [r for r in gce_queue.rows_from(Path(predictions))
                                           if r["instance_id"] in set(instance_ids)])
        gce_queue.wait(run_id, instance_ids)
        return
    if g.get("backend", "docker") == "modal":
        cmd += ["--modal", "true"]
    if instance_ids:
        cmd += ["--instance_ids", *instance_ids]
    print(" ".join(cmd[:12]), "...", flush=True)
    env = None
    if sys.platform == "win32":     # the harness subprocess needs the `resource` stand-in too
        import os
        env = {**os.environ, "PYTHONUTF8": "1",   # the harness writes logs with the locale encoding otherwise
               "PYTHONPATH": os.pathsep.join(filter(None, [str(swe_env.HERE / "winshim"), os.environ.get("PYTHONPATH")]))}
    subprocess.run(cmd, cwd=swe_env.RUNS, check=True, env=env)


def collect(run_id: str, instance_ids: list[str], predictions: dict) -> dict:
    out: dict = {}
    reports = {}
    for path in (swe_env.RUNS / "logs" / "run_evaluation" / run_id).rglob("report.json"):
        reports.update(json.loads(path.read_text(encoding="utf-8")))
    for iid in instance_ids:
        if predictions and not (predictions.get(iid) or "").strip():
            out[iid] = {"resolved": False, "status": "empty_patch"}
        elif iid in reports:
            resolved = bool(reports[iid].get("resolved"))
            out[iid] = {"resolved": resolved, "status": "resolved" if resolved else "unresolved"}
        else:
            out[iid] = {"resolved": False, "status": "error"}
    return out


def _read_predictions(path: Path) -> dict[str, str]:
    preds: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                preds[r["instance_id"]] = r.get("model_patch") or ""
    return preds


def reused_a0_grades(tag: str, preds: dict[str, str]) -> dict[str, dict]:
    """Grades copied from A0 for attempts generate.py REUSED from A0 (no notes for that instance, so the arm's
    prompt -- and its attempt and patch -- is A0's, byte for byte). Grading them again would re-measure the same
    observation; copying keeps the arms' results identical where their inputs are identical, as designed.
    Copied only when the patch is verifiably identical to A0's and A0's grade is a real verdict (never `error`)."""
    part, _, arm = tag.partition("_")
    if arm in ("A0", "A0r", "KP", "KH") or not arm:
        return {}
    a0_grades_path = swe_env.RUNS / f"grades_{part}_A0.json"
    if not a0_grades_path.exists():
        return {}
    a0_grades = json.loads(a0_grades_path.read_text(encoding="utf-8"))
    a0_preds = _read_predictions(swe_env.RUNS / f"predictions_{part}_A0.jsonl")
    reused: set[str] = set()
    attempts = swe_env.RUNS / f"attempts_{tag}.jsonl"
    if attempts.exists():
        for line in attempts.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("reused_from") == "A0" and not r.get("environmental_failure"):
                    reused.add(r["instance_id"])
    out: dict[str, dict] = {}
    for iid in reused:
        g = a0_grades.get(iid)
        if g and g.get("status") != "error" and iid in preds and preds[iid] == a0_preds.get(iid):
            out[iid] = {**g, "copied_from": f"{part}_A0"}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    ap.add_argument("--gold", action="store_true")
    ap.add_argument("--gold-set", default="calibration", help="design parts whose gold patches are checked, e.g. "
                    "calibration or train+test (a benchmark without human validation needs every task checked)")
    ap.add_argument("--run-id", help="harness run id (default: the tag, or gold_check). Reports are cached per "
                    "run id: re-running the same id re-grades only missing/errored instances; a new id grades afresh")
    a = ap.parse_args()
    require_pinned(scored=not (a.gold or (a.tag or "").startswith("calibration")))
    design = json.loads((swe_env.RUNS / "design.json").read_text(encoding="utf-8"))
    if a.gold:
        ids = [i for part in a.gold_set.split("+") for i in design[part]]
        run_id = a.run_id or "gold_check"
        run_harness("gold", run_id, ids)
        grades = collect(run_id, ids, {})
        bad = [i for i, g in grades.items() if not g["resolved"]]
        (swe_env.RUNS / "grades_gold_check.json").write_text(json.dumps(grades, indent=1), encoding="utf-8")
        print(f"gold patches resolved: {len(ids) - len(bad)}/{len(ids)}")
        if bad:
            raise SystemExit(f"STOP: the harness does not resolve gold patches for {bad}. Fix Docker/harness before "
                             "running any arm (or record these instances as harness-broken and exclude them "
                             "from every arm, as a logged deviation).")
        return
    preds_path = swe_env.RUNS / f"predictions_{a.tag}.jsonl"
    preds = _read_predictions(preds_path)
    ids = sorted(preds)
    run_id = a.run_id or a.tag
    copied = reused_a0_grades(a.tag, preds)
    to_grade = [i for i in ids if preds[i].strip() and i not in copied]
    if copied:
        print(f"{len(copied)} attempt(s) reused from A0: grades copied, not re-graded")
    if to_grade:
        run_harness(str(preds_path), run_id, to_grade)
    grades = {**collect(run_id, [i for i in ids if i not in copied], preds), **copied}
    (swe_env.RUNS / f"grades_{a.tag}.json").write_text(json.dumps(grades, indent=1), encoding="utf-8")
    from collections import Counter
    print(a.tag, dict(Counter(g["status"] for g in grades.values())))
    errors = [i for i, g in grades.items() if g["status"] == "error"]
    if errors:
        print(f"{len(errors)} instance(s) errored in the harness -- re-run this command; errors are never scored.")


if __name__ == "__main__":
    main()
