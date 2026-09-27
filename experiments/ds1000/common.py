"""Shared plumbing: problems, the design, the attempt log, grading + recording.

Attempt log: runs/attempts.jsonl, one line per graded attempt, keyed by
(problem_id, model, arm). Arms: fit_raw, fit_val, A, A2 (noise re-run), B, C, D, E.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import demo_env
from evaluate import extract_code, grade
from models import cost_usd

ATTEMPTS = demo_env.RUNS / "attempts.jsonl"
_lock = threading.Lock()
DOMAINS = {
    "Pandas": "Manipulate tabular data with pandas",
    "Numpy": "Compute with NumPy arrays",
    "Scipy": "Solve scientific computing problems with SciPy",
    "Sklearn": "Build machine learning workflows with scikit-learn",
}


@lru_cache(maxsize=1)
def problems() -> dict[str, dict]:
    return {str(json.loads(l)["metadata"]["problem_id"]): json.loads(l)
            for l in (demo_env.DATA / "test.jsonl").read_text(encoding="utf-8").splitlines()}


@lru_cache(maxsize=1)
def design() -> dict:
    return json.loads((demo_env.RUNS / "design.json").read_text(encoding="utf-8"))


def fit_items() -> list[dict]:
    return design()["fit"]


def test_items() -> list[dict]:
    return design()["test"]


def load_attempts() -> list[dict]:
    if not ATTEMPTS.exists():
        return []
    return [json.loads(line) for line in ATTEMPTS.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(record: dict) -> None:
    """Thread- AND process-safe append: a lock file (O_CREAT|O_EXCL) serialises writers."""
    import os
    import time

    lock = str(ATTEMPTS) + ".lock"
    with _lock:
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                time.sleep(0.02)
        try:
            with open(ATTEMPTS, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        finally:
            os.close(fd)
            os.remove(lock)


def grade_and_record(pid: str, model: str, scaffold: str, arm: str, text: str, tokens_in: int, tokens_out: int,
                     estimated: bool, latency_ms: int, *, error: Optional[str] = None,
                     notes_ref: Optional[str] = None, prompt_sha: Optional[str] = None) -> dict:
    p = problems()[pid]
    code = extract_code(text) if text else ""
    g = grade(code, p["code_context"], p["metadata"]["test_case_cnt"]) if code else {
        "check_pass": False, "gold_pass": False, "check_kind": None, "error": error or "empty reply",
        "screened": False, "detail": {}}
    rec = {
        "problem_id": pid, "model": model, "scaffold": scaffold, "arm": arm,
        "code": code, "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
        "check_pass": g["check_pass"], "gold_pass": g["gold_pass"], "check_kind": g["check_kind"],
        "eval_error": g["error"], "screened": g["screened"], "detail": g.get("detail", {}),
        "call_error": error, "tokens_in": tokens_in, "tokens_out": tokens_out, "tokens_estimated": estimated,
        "cost_usd": round(cost_usd(model, tokens_in, tokens_out), 7), "latency_ms": latency_ms,
        "notes_ref": notes_ref, "prompt_sha256": prompt_sha, "at": datetime.now(timezone.utc).isoformat(),
    }
    append(rec)
    return rec


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
