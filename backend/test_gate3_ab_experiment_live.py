"""
Gate 3 A/B EXPERIMENT DRIVER -- hand-run live script, NOT part of pytest
(named test_*_live.py so backend/conftest.py's collect_ignore_glob keeps
it out of collection, same convention as test_gate2b_rehearsal_live.py).

    python test_gate3_ab_experiment_live.py smoke          # 1 A + 1 B
    python test_gate3_ab_experiment_live.py scored         # 12 scored trials
    python test_gate3_ab_experiment_live.py nonapplicable  # 1 B diagnostic

FROZEN BASELINE: Gate 2B at 47a944b. Verification thresholds, B_default,
Postgres evidence, and experiments/ are untouched by this driver.

ARMS:
  A = frontier agent + NO StealthLab procedural retrieval
      (LocalAgentRunner.run(..., experimental_no_retrieval=True))
  B = the SAME agent + StealthLab procedural retrieval
      (LocalAgentRunner.run(..., allow_unverified=True) -- the existing
      sanctioned experimental path; B_default / thresholds unchanged)

The ONLY intentional difference between A and B is retrieval. Identical:
model, budgets (max_steps=40, time_budget_s=600 -- production-matched to
Gate 2B), task text, fresh disposable starting repository, environment,
grader, success criteria.

SCORING: task success is decided ONLY by the deterministic grader
(gate3_graders.grade_repo) run in a fresh subprocess against the resulting
repository. A finished stop_reason, a non-empty patch, or the agent's own
claim is never evidence of success; a deterministic validation failure is
always a failure.

INVALID TRIALS: infrastructure failures (MCP server unreachable, provider
failure, corrupted disposable repo, driver failure, grader infrastructure
failure) are recorded invalid=true with reason + raw evidence. They are
NEVER silently converted into task failures and NEVER silently retried.
The scored campaign requires explicit interactive authorization (Gate 3
Part 12); only `smoke` has been run so far.

Output: one JSON line per trial in backend/gate3_results/
gate3_trials.jsonl (+ per-trial raw output under gate3_results/raw/).
"""
import asyncio
import datetime
import json
import os
import sys
import tempfile
import time
import traceback

from dotenv import load_dotenv

load_dotenv()

from gate3_graders import get_task, grade_repo, write_starter_repo
from app.local_agent.runner import LocalAgentRunner

FROZEN_COMMIT = "47a944b"
MAX_STEPS = int(os.environ.get("GATE3_MAX_STEPS", "40"))
TIME_BUDGET_S = int(os.environ.get("GATE3_TIME_BUDGET_S", "600"))
MODEL = os.environ.get("GATE3_MODEL", "gemma-4-31B-it")
SCORED_TASKS = ["task1_lazy_multi_tool", "task2_lazy_new_tool"]
TRIALS_PER_ARM = 3
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "gate3_results")
RAW_DIR = os.path.join(RESULTS_DIR, "raw")
JSONL_PATH = os.path.join(RESULTS_DIR, "gate3_trials.jsonl")

SERVER_URL = os.environ.get("STEALTHLAB_GLOBAL_SERVER_URL",
                            "http://127.0.0.1:8765/mcp")
TOKEN = os.environ.get("STEALTHLAB_MCP_TOKEN", "")


def _write_raw(record: dict, raw: dict) -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    raw_path = os.path.join(RAW_DIR, record["trial_id"] + ".json")
    with open(raw_path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2, default=str)
    record["raw_file"] = os.path.relpath(raw_path, RESULTS_DIR)


def _append_jsonl(record: dict) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(JSONL_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


async def run_trial(task_id: str, arm: str, trial_index: int,
                    scored: bool) -> dict:
    """One fresh-repository A or B trial, end to end. Never raises:
    infrastructure problems come back as invalid=True records with the
    raw evidence attached."""
    task = get_task(task_id)
    trial_id = (f"{task_id}_{arm}_{trial_index}_"
                f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")
    record = {
        "schema": "gate3_trial/v1",
        "frozen_commit": FROZEN_COMMIT,
        "task_id": task_id,
        "trial_id": trial_id,
        "arm": arm,
        "scored": scored,
        "model": MODEL,
        "max_steps": MAX_STEPS,
        "time_budget_s": TIME_BUDGET_S,
        "started_at_unix": round(time.time(), 3),
        "ended_at_unix": None,
        "retrieval": None,
        "metrics": None,
        "graph_outcome": None,
        "artifact_validation": None,
        "grader": None,
        "outcome": None,
        "invalid": False,
        "invalid_reason": None,
        "raw_file": None,
    }
    raw: dict = {"trial_id": trial_id, "arm": arm, "task_id": task_id,
                 "task_text": task["task_text"]}
    repo = None
    try:
        # Fresh disposable repository per trial -- zero cross-trial state.
        repo = tempfile.mkdtemp(prefix=f"gate3_{task_id}_{arm}_")
        write_starter_repo(task_id, repo)
        raw["repo_path"] = repo

        runner = LocalAgentRunner(server_url=SERVER_URL, token=TOKEN,
                                  model=MODEL, max_steps=MAX_STEPS)
        # A: explicit experimental_no_retrieval bypass.
        # B: the normal retrieval path via allow_unverified=True -- the
        # sanctioned experimental path; B_default / thresholds untouched.
        result = await runner.run(
            task_description=task["task_text"], repo_path=repo,
            allow_unverified=True,
            experimental_no_retrieval=(arm == "A"),
        )
        record["retrieval"] = result.retrieval_log
        record["metrics"] = result.metrics
        record["graph_outcome"] = result.graph_outcome
        record["artifact_validation"] = not any(
            "VALIDATION FAILED" in n for n in result.node_notes)
        raw["node_notes"] = list(result.node_notes)
        raw["files_edited"] = list(result.files_edited)
        raw["combined_patch_chars"] = len(result.combined_patch or "")
        if result.matched_procedure is not None:
            raw["matched_procedure"] = {
                k: result.matched_procedure.get(k)
                for k in ("name", "procedure_id", "version",
                          "verification_state")
            }
        if result.captured_candidate is not None:
            raw["captured_candidate_id"] = result.captured_candidate.get("id")
    except Exception as exc:  # noqa: BLE001 -- infrastructure failure
        record["invalid"] = True
        record["invalid_reason"] = f"trial_infra: {exc!r}"
        raw["traceback"] = traceback.format_exc()
        record["ended_at_unix"] = round(time.time(), 3)
        _write_raw(record, raw)
        _append_jsonl(record)
        return record

    # Deterministic task grader: fresh subprocess, repo state only.
    try:
        grader = grade_repo(task_id, repo)
    except Exception as exc:  # noqa: BLE001
        grader = {"grader_success": False, "checks": {},
                  "error": f"grader_infra: {exc!r}"}
    record["grader"] = grader
    raw["grader"] = grader
    record["ended_at_unix"] = round(time.time(), 3)

    if grader["error"] and str(grader["error"]).startswith("grader_infra:"):
        record["invalid"] = True
        record["invalid_reason"] = grader["error"]
        record["outcome"] = "invalid"
    else:
        # Success is the GRADER's verdict alone. An infrastructure failure
        # is never a task failure, and a grader failure is never rescued
        # by the agent's stop reason or self-report.
        record["outcome"] = ("success" if grader["grader_success"] is True
                             else "failure")
    _write_raw(record, raw)
    _append_jsonl(record)
    return record


def _print_record(record: dict) -> None:
    print(f"\n--- {record['trial_id']} (arm {record['arm']}, "
          f"{'SCORED' if record['scored'] else 'non-scored'}) ---")
    print("outcome:", record["outcome"],
          "| invalid:", record["invalid"], record["invalid_reason"] or "")
    if record["arm"] == "B" and record["retrieval"]:
        for entry in record["retrieval"]:
            print(f"  retrieved: rank={entry['rank']} name={entry['name']} "
                  f"state={entry['verification_state']}")
    if record["arm"] == "A":
        print("  retrieval: BYPASSED (no search performed, nothing fabricated)")
    if record["grader"]:
        print("  grader checks:", record["grader"]["checks"])
    if record["metrics"]:
        m = record["metrics"]
        print(f"  metrics: latency={m['latency_s']}s steps={m['steps']} "
              f"llm_calls={m['llm_calls']} tokens={m['total_tokens']} "
              f"stop={m['stop_reason']}")


async def _campaign(mode: str) -> None:
    if TOKEN == "":
        print("ERROR: STEALTHLAB_MCP_TOKEN not set; refusing to start.")
        sys.exit(2)
    if mode == "scored":
        confirm = input(
            "This runs the 12 SCORED trials (real billed LLM calls). "
            "Gate 3 Part 12 requires explicit authorization first. "
            "Type RUN-SCORED to proceed: ")
        if confirm.strip() != "RUN-SCORED":
            print("Aborted -- scored campaign not authorized.")
            return
        plan = [(t, arm, i, True)
                for t in SCORED_TASKS for arm in ("A", "B")
                for i in range(1, TRIALS_PER_ARM + 1)]
    elif mode == "smoke":
        # Exactly one A and one B smoke trial, non-scored.
        plan = [(SCORED_TASKS[0], "A", 0, False),
                (SCORED_TASKS[0], "B", 0, False)]
    elif mode == "nonapplicable":
        plan = [("na_broken_echo", "B", 0, False)]
    else:
        print(f"unknown mode: {mode}")
        return

    print(f"Gate 3 experiment driver -- mode={mode} trials={len(plan)}")
    print(f"frozen_commit={FROZEN_COMMIT} model={MODEL} "
          f"max_steps={MAX_STEPS} time_budget_s={TIME_BUDGET_S}")
    for task_id, arm, idx, scored in plan:
        record = await run_trial(task_id, arm, idx, scored)
        _print_record(record)
    print(f"\nJSONL written to {JSONL_PATH}")


if __name__ == "__main__":
    asyncio.run(_campaign(sys.argv[1] if len(sys.argv) > 1 else "smoke"))
