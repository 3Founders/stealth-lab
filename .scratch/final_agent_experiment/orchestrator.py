"""
Trial orchestrator for the Final Baseline vs Stealth Agent Experiment.

Runs ONE trial of ONE task through ONE arm config, against a disposable,
isolated git worktree pinned to the frozen commit. Never reuses a worktree
across trials/arms. Built against the REAL current
backend/app/local_agent/runner.py::LocalAgentRunner.run() signature
(read in full before writing this file) -- not an assumed one.

Arm configs:
  A          -- direct Agent+RepoSandbox call via runner._run_local_node,
                bypassing LocalAgentRunner.run() entirely (no MCP, no
                search_procedures, no check_applicability).
  B_default  -- LocalAgentRunner.run(..., allow_unverified=False) -- the
                real production default (require_verified=True upstream).
  B_unverified -- LocalAgentRunner.run(..., allow_unverified=True).

Usage (from backend/, with .env sourced and the MCP server already running
for B_* arms):
    python ../.scratch/final_agent_experiment/orchestrator.py \
        --task-file ../.scratch/final_agent_experiment/tasks.jsonl \
        --task-id SMOKE --arm B_default --frozen-commit <sha> \
        --server-url http://127.0.0.1:8811/mcp --token $STEALTHLAB_MCP_TOKEN \
        --model gpt-oss-120b --max-steps 8 --time-budget-s 600 \
        --out-dir ../.scratch/final_agent_experiment/smoke_test

Writes one JSON result file (never appends to trials.jsonl for a smoke run --
caller controls out_dir/whether this is scored-matrix data or not) plus a
raw/ subdirectory with the tool-call journal.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))


def _make_disposable_worktree(repo_root: Path, commit: str, tmp_root: Path) -> Path:
    wt_path = tmp_root / f"trial-{uuid.uuid4().hex[:10]}"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt_path), commit],
        cwd=repo_root, check=True, capture_output=True, text=True,
    )
    return wt_path


def _remove_disposable_worktree(repo_root: Path, wt_path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(wt_path)],
        cwd=repo_root, check=False, capture_output=True, text=True,
    )
    shutil.rmtree(wt_path, ignore_errors=True)


def _redact(obj):
    """Strip anything that looks like a secret before writing raw logs."""
    SECRET_KEYS = {"api_key", "authorization", "token", "password", "secret"}
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if k.lower() in SECRET_KEYS else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class _UsageCapture:
    """Monkeypatches experiments/swebench_pro/agent.py's real Agent.run
    (NOT a file on disk -- a runtime patch applied from this orchestrator
    process only) to record the REAL AgentRun.usage (Usage.prompt_tokens/
    completion_tokens/calls, populated in agent.py's own run() from the
    real OpenAI-compatible response's resp.usage on every call -- see
    agent.py lines ~813-814) that backend/app/local_agent/runner.py's
    _run_local_node currently reads AgentRun.stop_reason/tool_calls/
    files_edited/patch from but never AgentRun.usage, discarding it.

    Zero bytes written to backend/app/** or experiments/swebench_pro/**;
    this class exists only in this scratch orchestrator and restores the
    original method on exit, so a caller reusing this process for
    something else is never left with a dangling patch.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self._agent_module = None
        self._original_run = None

    def __enter__(self) -> "_UsageCapture":
        from app.local_agent import runner as runner_mod

        runner_mod._ensure_swebench_pro_on_path()
        import agent as swebench_agent_module  # experiments/swebench_pro/agent.py

        self._agent_module = swebench_agent_module
        self._original_run = swebench_agent_module.Agent.run
        capture = self

        def _patched_run(self_agent, *args, **kwargs):
            result = capture._original_run(self_agent, *args, **kwargs)
            u = result.usage
            capture.calls.append({
                "instance_id": result.instance_id,
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.total,
                "api_calls": u.calls,
            })
            return result

        swebench_agent_module.Agent.run = _patched_run
        return self

    def __exit__(self, *exc_info) -> None:
        if self._agent_module is not None and self._original_run is not None:
            self._agent_module.Agent.run = self._original_run

    def summary(self) -> dict:
        """Real aggregated usage across every Agent.run() call captured
        this trial (a multi-node task graph makes more than one such
        call; a single-step task like the smoke test makes exactly one).
        cost_usd is deliberately always null -- no versioned
        GENERAL_COMPUTE/gpt-oss-120b pricing config exists anywhere in
        this repo (experiments/harness/openrouter_arms.py's
        PRICE_PER_MTOK has exactly one entry, "default": {input 2.50,
        output 10.00}/Mtok, which is an OpenRouter fallback price for a
        DIFFERENT provider -- borrowing it here would misattribute a
        real-looking but fabricated cost to GENERAL_COMPUTE usage,
        exactly what this instrumentation pass was told not to do)."""
        if not self.calls:
            return {
                "input_tokens": None, "output_tokens": None, "total_tokens": None,
                "model_calls": 0, "cost_usd": None,
                "cost_unavailable_reason": "no Agent.run() call captured this trial",
                "usage_raw": [],
            }
        input_tokens = sum(c["prompt_tokens"] for c in self.calls)
        output_tokens = sum(c["completion_tokens"] for c in self.calls)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "model_calls": sum(c["api_calls"] for c in self.calls),
            "cost_usd": None,
            "cost_unavailable_reason": (
                "no versioned GENERAL_COMPUTE/gpt-oss-120b pricing config exists in "
                "this repo (economics.py and openrouter_arms.py price OpenRouter only; "
                "PRICE_PER_MTOK's sole 'default' entry is an OpenRouter fallback for a "
                "different provider, not an authoritative GENERAL_COMPUTE price)"
            ),
            "usage_raw": self.calls,
        }


async def run_trial_arm_A(*, task_description: str, repo_path: str, model: str,
                           max_steps: int) -> dict:
    """Direct Agent+RepoSandbox call, NO MCP/Stealth layer -- mirrors
    runner.py::_run_local_node's own internals exactly, called directly."""
    from app.local_agent import runner as runner_mod

    node_notes: list[str] = []

    class _Node:
        order = 0
        goal = task_description

    t0 = time.monotonic()
    with _UsageCapture() as usage:
        result = await runner_mod._run_local_node(
            _Node(), task_description=task_description, repo_path=repo_path,
            model=model, max_steps=max_steps, node_notes=node_notes,
        )
    wall = time.monotonic() - t0
    return {
        "arm": "A",
        "task_success": None,  # filled by caller's verifier
        "graph_status": result.status,
        "files_touched": result.data.get("files_edited", []) if result.data else [],
        "tool_calls": result.data.get("tool_calls", 0) if result.data else 0,
        "wall_clock_seconds": wall,
        "notes": result.notes,
        "stealth_retrieval_decision": None,
        **usage.summary(),
    }


async def run_trial_arm_B(*, task_description: str, repo_path: str, model: str,
                           max_steps: int, server_url: str, token: str,
                           allow_unverified: bool) -> dict:
    from app.local_agent.runner import LocalAgentRunner

    runner = LocalAgentRunner(server_url, token, model=model, max_steps=max_steps)
    t0 = time.monotonic()
    with _UsageCapture() as usage:
        result = await runner.run(task_description, repo_path, allow_unverified=allow_unverified)
    wall = time.monotonic() - t0
    return {
        "arm": "B_unverified" if allow_unverified else "B_default",
        "task_success": None,
        "graph_outcome": result.graph_outcome,
        "files_touched": result.files_edited,
        "wall_clock_seconds": wall,
        "stealth_retrieval_decision": {
            "matched": result.matched_procedure is not None,
            "source": result.source,
            "procedure_id": (result.matched_procedure or {}).get("procedure_id")
                if result.matched_procedure else None,
        },
        "node_notes": result.node_notes,
        **usage.summary(),
    }


async def run_one_trial(*, task_id: str, task_description: str, arm: str,
                         frozen_commit: str, repo_root: Path, tmp_root: Path,
                         model: str, max_steps: int, time_budget_s: int,
                         server_url: str | None, token: str | None,
                         out_dir: Path, scored: bool) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    wt_path = _make_disposable_worktree(repo_root, frozen_commit, tmp_root)
    trial_id = f"{task_id}-{arm}-{uuid.uuid4().hex[:8]}"
    t0 = time.monotonic()
    try:
        coro = (
            run_trial_arm_A(task_description=task_description, repo_path=str(wt_path),
                             model=model, max_steps=max_steps)
            if arm == "A" else
            run_trial_arm_B(task_description=task_description, repo_path=str(wt_path),
                             model=model, max_steps=max_steps, server_url=server_url,
                             token=token, allow_unverified=(arm == "B_unverified"))
        )
        try:
            arm_result = await asyncio.wait_for(coro, timeout=time_budget_s)
            budget_exceeded = False
            error = None
        except asyncio.TimeoutError:
            arm_result = {"arm": arm}
            budget_exceeded = True
            error = None
    except Exception as exc:  # noqa: BLE001 -- record real failures, never swallow silently
        arm_result = {"arm": arm}
        budget_exceeded = False
        error = f"{type(exc).__name__}: {exc}"

    wall = time.monotonic() - t0
    record = {
        "trial_id": trial_id, "task_id": task_id, "arm": arm,
        "frozen_commit": frozen_commit, "scored": scored,
        "budget_exceeded": budget_exceeded, "error": error,
        "wall_clock_seconds_total": wall, **{k: v for k, v in arm_result.items() if k != "arm"},
    }
    (raw_dir / f"{trial_id}.json").write_text(
        json.dumps(_redact(record), indent=2, default=str), encoding="utf-8",
    )
    _remove_disposable_worktree(repo_root, wt_path)
    return record


def _start_mcp_server(repo_root: Path, port: int, env: dict) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.mcp_server.server:app",
         "--port", str(port), "--log-level", "warning"],
        cwd=repo_root / "backend", env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    return proc


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task-id", required=True)
    p.add_argument("--task-description", required=True)
    p.add_argument("--arm", required=True, choices=["A", "B_default", "B_unverified"])
    p.add_argument("--frozen-commit", required=True)
    p.add_argument("--repo-root", required=True)
    p.add_argument("--tmp-root", required=True)
    p.add_argument("--model", default="gpt-oss-120b")
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--time-budget-s", type=int, default=600)
    p.add_argument("--server-url", default=None)
    p.add_argument("--token", default=None)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--scored", action="store_true", help="omit for smoke-test runs")
    args = p.parse_args()

    record = asyncio.run(run_one_trial(
        task_id=args.task_id, task_description=args.task_description, arm=args.arm,
        frozen_commit=args.frozen_commit, repo_root=Path(args.repo_root),
        tmp_root=Path(args.tmp_root), model=args.model, max_steps=args.max_steps,
        time_budget_s=args.time_budget_s, server_url=args.server_url, token=args.token,
        out_dir=Path(args.out_dir), scored=args.scored,
    ))
    print(json.dumps(_redact(record), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
