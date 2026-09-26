"""Run open models on sample tasks, grade each attempt locally, append to runs/attempts.jsonl.

    python run_models.py --split fit --condition raw
    python run_models.py --split heldout --condition kel      # needs runs/knowledge_heldout.json

Resumable: an attempt already in attempts.jsonl for (task, model, condition) is skipped.
Temperature 0, one sample per (task, model, condition).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import demo_env

from app.benchmarks import bigcodebench as bcb
from evaluate import extract_code, run_tests
from models import SCAFFOLD_API, GeneralCompute, build_prompt, cost_usd

OPEN_MODELS = ("gemma-4-31B-it", "gpt-oss-120b", "deepseek-v3.2")
ATTEMPTS = demo_env.RUNS / "attempts.jsonl"
_lock = threading.Lock()


def load_attempts() -> list[dict]:
    if not ATTEMPTS.exists():
        return []
    return [json.loads(line) for line in ATTEMPTS.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(record: dict) -> None:
    with _lock, open(ATTEMPTS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def sample_tasks(split: str) -> dict[str, bcb.BenchmarkTask]:
    sample = json.loads((demo_env.RUNS / "sample.json").read_text(encoding="utf-8"))
    wanted = set(sample[split])
    rows = [r for r in bcb.load_rows(demo_env.DATA / f"bigcodebench-{bcb.LATEST_VERSION}.parquet")
            if r["task_id"] in wanted]
    return {t.external_id: t for t in bcb.tasks_from_rows(rows)}


def grade_and_record(task: bcb.BenchmarkTask, split: str, model: str, scaffold: str, condition: str, text: str,
                     tokens_in: int, tokens_out: int, estimated: bool, latency_ms: int,
                     error: str | None = None, knowledge_ref: str | None = None) -> dict:
    code = extract_code(text) if text else ""
    result = run_tests(code, task.test_code, task.visible_tests) if code else {
        "visible_pass": False, "gold_pass": False, "tests": {}, "error": error or "empty reply", "screened": False}
    record = {
        "task_id": task.external_id, "split": split, "model": model, "scaffold": scaffold, "condition": condition,
        "code_sha256": hashlib.sha256(code.encode()).hexdigest(), "code": code,
        "visible_pass": result["visible_pass"], "gold_pass": result["gold_pass"], "tests": result.get("tests", {}),
        "failures": result.get("failures", {}), "eval_error": result.get("error"), "screened": result.get("screened"),
        "call_error": error, "tokens_in": tokens_in, "tokens_out": tokens_out, "tokens_estimated": estimated,
        "cost_usd": round(cost_usd(model, tokens_in, tokens_out), 6), "latency_ms": latency_ms,
        "knowledge_ref": knowledge_ref, "at": datetime.now(timezone.utc).isoformat(),
    }
    append(record)
    return record


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["fit", "heldout"], required=True)
    ap.add_argument("--condition", choices=["raw", "kel", "kel_down"], default="raw")
    ap.add_argument("--reuse-raw", action="store_true",
                    help="where Kel retrieved nothing, the prompt equals the raw one: reuse that raw attempt")
    ap.add_argument("--models", default=",".join(OPEN_MODELS))
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    demo_env.verify_after_import()
    tasks = sample_tasks(a.split)
    knowledge = {}
    if a.condition != "raw":
        name = f"knowledge_{a.split}.json" if a.condition == "kel" else f"knowledge_{a.split}_down.json"
        knowledge = json.loads((demo_env.RUNS / name).read_text(encoding="utf-8"))
    done = {(r["task_id"], r["model"], r["condition"]) for r in load_attempts()}
    jobs = [(t, m) for t in tasks.values() for m in a.models.split(",") if (t.external_id, m, a.condition) not in done
            and (a.condition == "raw" or t.external_id in knowledge)]      # kel: only tasks Kel has knowledge for
    if a.reuse_raw:
        raw = {(r["task_id"], r["model"]): r for r in load_attempts() if r["condition"] == "raw" and r["split"] == a.split}
        kept = []
        for task, model in jobs:
            if knowledge.get(task.external_id, {}).get("text") is None and (task.external_id, model) in raw:
                append({**raw[(task.external_id, model)], "condition": a.condition, "reused_from_raw": True,
                        "knowledge_ref": None})
            else:
                kept.append((task, model))
        print(f"reused {len(jobs) - len(kept)} raw attempts (no knowledge retrieved); running {len(kept)}")
        jobs = kept
    gc = GeneralCompute()

    def work(job):
        task, model = job
        k = knowledge.get(task.external_id, {})
        reply = gc.complete(model, build_prompt(task.goal_description, k.get("text")))
        rec = grade_and_record(task, a.split, model, SCAFFOLD_API, a.condition, reply.text, reply.tokens_in,
                               reply.tokens_out, reply.estimated, reply.latency_ms, reply.error, k.get("ref"))
        print(f"{task.external_id:<18} {model:<16} visible={rec['visible_pass']!s:<5} gold={rec['gold_pass']!s:<5} "
              f"{rec['eval_error'] or ''}"[:160], flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(work, jobs))
    rows = [r for r in load_attempts() if r["split"] == a.split and r["condition"] == a.condition]
    for m in a.models.split(","):
        mine = [r for r in rows if r["model"] == m]
        if mine:
            print(f"{m:<16} gold {sum(r['gold_pass'] for r in mine)}/{len(mine)}  "
                  f"visible {sum(r['visible_pass'] for r in mine)}/{len(mine)}")


if __name__ == "__main__":
    main()
