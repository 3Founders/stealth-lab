"""Routing arms of the local-only test: for every task, plan -> run -> check -> report -> escalate.

    backend/.venv/Scripts/python experiments/local_eval/route_run.py --arm R80 [--part test] [--workers 4]
    backend/.venv/Scripts/python experiments/local_eval/route_run.py --summary

What an arm does for one task (routing.json `arms`):
  * `route`: ask the planner for a ladder (the product's own recommender, app/routing/service.py, run in-process
    on the bundled prior -- no server, no database), run its first model, check the result, and on a failed check
    tell the planner (previous_attempts) and run the model it names next, up to `max_rungs`. The repository's own
    outcomes so far (earlier tasks of the same repo, in created_at order) condition every plan, as routing.md's
    OBS counts do in the product.
  * `fixed`: run the ladder as written (STRONG, CHEAP, NAIVE baselines), with the same check and escalation.

Every attempt is ONE run of the agent (experiment.json's agent, budget and decoding; the memory block of
routing.json `memory_arm`, L1 by default) and ONE test run (grade_tests.grade_one). Both the check and the
final grade come from that same test run:
  * check (what the agent could know before delivering; routing.json check.kind):
      regression = the patch applies and every PASS_TO_PASS test passes;
      partial    = regression, and at least one FAIL_TO_PASS test passes;
      oracle     = resolved (upper bound only);
  * delivered = the first attempt whose check passed, else the last attempt (the ladder ran out);
  * resolved  = the delivered attempt resolved the task (every FAIL_TO_PASS and PASS_TO_PASS test passed).
A task's cost is the sum over ALL its attempts (failed rungs are paid for too).

Output: runs/routing_<arm>.jsonl, one line per task:
  {instance_id, repo, arm, attempts: [{rung, model, plan?, tokens_in, tokens_out, cost_usd, status, f2p, p2p,
   check, resolved, seconds}], delivered_rung, accepted, resolved, wrong_accepted, cost_usd}
Resumable: a task already in the file is skipped. An infrastructure error (agent API failure, test-run error)
stops that task without recording it, so it is retried on the next invocation.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ROUTING_PATH = HERE / "routing.json"
_lock = threading.Lock()


def load_routing(path: Path = ROUTING_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- check and cost

def check_passed(grade: dict, kind: str) -> bool:
    if grade.get("status") in ("empty_patch", "patch_failed", "error"):
        return False
    if kind == "oracle":
        return bool(grade.get("resolved"))
    p2p = grade.get("p2p") or [0, 0]
    regression = p2p[0] == p2p[1]
    if kind == "regression":
        return regression
    if kind == "partial":
        f2p = grade.get("f2p") or [0, 0]
        return regression and f2p[0] >= 1
    raise ValueError(f"unknown check kind {kind!r}")


def cost_of(model_cfg: dict, tokens_in: int, tokens_out: int) -> Optional[float]:
    if model_cfg.get("price_in") is None or model_cfg.get("price_out") is None:
        return None
    return (tokens_in * model_cfg["price_in"] + tokens_out * model_cfg["price_out"]) / 1e6


# ---------------------------------------------------------------- the repository's own outcomes (routing.md OBS)

class RepoMemory:
    """{repo: {unit: [attempts, accepted]}} -- what this repository's earlier tasks showed, per model."""

    def __init__(self) -> None:
        self._obs: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        self._lock = threading.Lock()

    def record(self, repo: str, unit: str, accepted: bool) -> None:
        with self._lock:
            c = self._obs[repo][unit]
            c[0] += 1
            c[1] += int(accepted)

    def local_obs(self, repo: str) -> list[dict]:
        with self._lock:
            return [{"unit": u, "n": n, "ok": ok} for u, (n, ok) in sorted(self._obs[repo].items()) if n]


# ---------------------------------------------------------------- planner: the product's recommender, in-process

@contextlib.contextmanager
def _no_database():
    """The recommender's store reads answer from the bundled prior alone (what a fresh install has); its writes
    are dropped. The planner state that matters -- earlier attempts on this task, this repo's counts -- is passed
    in explicitly, as report_result and route_obs pass it in the product."""
    from app.routing import store

    async def none(*a, **k):
        return None

    async def empty_dict(*a, **k):
        return {}

    async def empty_list(*a, **k):
        return []

    async def drop(*a, **k):
        return None

    saved = {}
    fakes = {"active_params": none, "load_posteriors": empty_dict, "model_registry": empty_dict,
             "model_cards": empty_list, "current_prices": empty_dict, "model_updates": empty_dict,
             "goal_token_stats": empty_dict, "goal_cost_stats": empty_dict, "goal_rows": empty_dict,
             "record_decision": drop}
    for name, fake in fakes.items():
        saved[name] = getattr(store, name)
        setattr(store, name, fake)
    try:
        yield
    finally:
        for name, fn in saved.items():
            setattr(store, name, fn)


class Planner:
    def __init__(self, units: list[str], *, reliability_target: float, check_kind: str = "tests",
                 max_rungs: int = 3) -> None:
        self.units, self.target, self.check_kind, self.max_rungs = units, reliability_target, check_kind, max_rungs
        self._lock = threading.Lock()

    def plan(self, repo: str, attempts: list[dict], local_obs: list[dict],
             features: Optional[dict] = None) -> dict:
        """The recommender's answer for this task now: {ladder, p_ok, meets_target, basis}."""
        import asyncio

        from app.routing import service
        from app.services.access import AccessScope

        virtual = {"kind": "repo", "ref": repo, **({"features": features} if features else {})}
        with self._lock, _no_database():
            rec = asyncio.run(service._recommend(
                None, goal_id=service.virtual_goal_id("repo", repo), candidates=self.units,
                access_scope=AccessScope.unrestricted(), virtual=virtual, instance_key=f"exp:{repo}",
                check_kind=self.check_kind, previous_attempts=attempts, local_obs=local_obs,
                constraints={"reliability_target": self.target, "max_rungs": self.max_rungs}, record=False))
        if rec.get("status") != "ok":
            raise RuntimeError(f"planner: {rec.get('status')}: {rec.get('reason') or rec.get('excluded')}")
        return {"ladder": rec["recommended"]["ladder"], "meets_target": rec["meets_reliability_target"],
                "p_ok": {u: round(v, 4) for u, v in rec["p_correct_single_attempt"].items()},
                "excluded": rec.get("excluded") or []}


# ---------------------------------------------------------------- one task

@dataclass
class Infra(Exception):
    reason: str


def run_task(task: dict, arm: str, arm_cfg: dict, *, execute: Callable[[str, dict], dict],
             grade: Callable[[dict, str], dict], check_kind: str, scaffold: str, max_rungs: int,
             memory: RepoMemory, planner: Optional[Planner] = None, features: Optional[dict] = None) -> dict:
    """execute(model_key, task) -> {patch, tokens_in, tokens_out, cost_usd, seconds} or raises Infra;
    grade(task, patch) -> grade_tests.grade_one's dict."""
    repo = task["repo"]
    attempts: list[dict] = []
    tried: list[dict] = []                                # previous_attempts for the planner
    fixed = list(arm_cfg.get("ladder") or [])
    for rung in range(max_rungs):
        plan = None
        if arm_cfg["policy"] == "route":
            plan = planner.plan(repo, tried, memory.local_obs(repo), features)
            model = plan["ladder"][0].split("|", 1)[0]
        elif rung < len(fixed):
            model = fixed[rung]
        else:
            break
        run = execute(model, task)
        g = grade(task, run["patch"])
        if g.get("status") == "error":
            raise Infra(f"test run error: {g.get('detail', '')[:200]}")
        ok = check_passed(g, check_kind)
        attempts.append({"rung": rung, "model": model, **({"plan": plan} if plan else {}),
                         "tokens_in": run.get("tokens_in", 0), "tokens_out": run.get("tokens_out", 0),
                         "cost_usd": run.get("cost_usd"), "status": g.get("status"), "f2p": g.get("f2p"),
                         "p2p": g.get("p2p"), "check": ok, "resolved": bool(g.get("resolved")),
                         "seconds": run.get("seconds")})
        unit = f"{model}|{scaffold}"
        memory.record(repo, unit, ok)
        tried.append({"unit": unit, "accepted": ok, "check_kind": "tests"})
        if ok:
            break
    if not attempts:
        raise Infra("no attempt was made")
    delivered = next((a for a in attempts if a["check"]), attempts[-1])
    costs = [a["cost_usd"] for a in attempts]
    return {"instance_id": task["instance_id"], "repo": repo, "arm": arm, "attempts": attempts,
            "delivered_rung": delivered["rung"], "accepted": delivered["check"], "resolved": delivered["resolved"],
            "wrong_accepted": delivered["check"] and not delivered["resolved"],
            "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 6)}


def run_repo(tasks: list[dict], **kw) -> list[dict]:
    """One repository's tasks in created_at order: each task's plan sees the earlier tasks' outcomes."""
    out = []
    for t in sorted(tasks, key=lambda t: (str(t.get("created_at") or ""), t["instance_id"])):
        out.append(run_task(t, **kw))
    return out


# ---------------------------------------------------------------- summary

def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"tasks": 0}
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    return {"tasks": n, "resolved": round(sum(r["resolved"] for r in rows) / n, 4),
            "accepted": round(sum(r["accepted"] for r in rows) / n, 4),
            "wrong_accepted": round(sum(r["wrong_accepted"] for r in rows) / n, 4),
            "mean_attempts": round(sum(len(r["attempts"]) for r in rows) / n, 3),
            "mean_cost_usd": round(sum(costs) / len(costs), 6) if costs else None,
            "first_rung_models": dict(sorted(_count(r["attempts"][0]["model"] for r in rows).items()))}


def _count(xs) -> dict:
    out: dict = defaultdict(int)
    for x in xs:
        out[x] += 1
    return out


# ---------------------------------------------------------------- the real agent and test runner

def _real_execute(routing: dict, memory_text: Callable[[str], str]) -> Callable[[str, dict], dict]:
    os.environ.setdefault("KEL_SWEBENCH_CONFIG", str(HERE / "experiment.json"))
    sys.path.insert(0, str(ROOT / "experiments" / "swebench"))
    sys.path.insert(0, str(ROOT / "backend"))
    import generate                                    # experiments/swebench/generate.py: worktrees, header
    import swe_env
    from openai import OpenAI

    from app.execution.coding_agent import Agent, RepoSandbox

    agent_cfg = swe_env.CONFIG["agent"]
    if agent_cfg.get("max_steps") is None:
        raise SystemExit("experiment.json agent.max_steps is not frozen yet")
    agents: dict[str, Any] = {}

    def agent_for(model: str):
        with _lock:
            if model not in agents:
                m = routing["models"][model]
                client = OpenAI(api_key=os.environ[m["api_key_env"]], base_url=os.environ[m["base_url_env"]],
                                max_retries=0)
                agents[model] = Agent(client, m["id"], max_steps=agent_cfg["max_steps"],
                                      temperature=agent_cfg["temperature"])
            return agents[model]

    def execute(model: str, task: dict) -> dict:
        text = memory_text(task["instance_id"])
        memory = (generate.HEADER + text) if text else ""
        started = time.time()
        wt = generate.checkout(task["repo"], task["base_commit"], f"route_{model}_{task['instance_id']}".replace("/", "_"))
        try:
            run = agent_for(model).run(task, RepoSandbox(wt), f"route:{model}", memory_block=memory)
        finally:
            generate.release(task["repo"], wt)
        if run.stop_reason == "api_error":
            raise Infra(f"{model}: provider failure")
        tin, tout = run.usage.prompt_tokens, run.usage.completion_tokens
        return {"patch": run.patch or "", "tokens_in": tin, "tokens_out": tout,
                "cost_usd": cost_of(routing["models"][model], tin, tout), "seconds": round(time.time() - started, 1)}

    return execute


def _real_grade() -> Callable[[dict, str], dict]:
    import grade_tests
    from common import CONFIG, RUNS, read_json

    grade_tests.fetch_parsers()
    src = read_json(RUNS / "grading_source.json")
    runner, timeout = CONFIG["grading"]["runner"], CONFIG["grading"]["timeout_s"]
    return lambda task, patch: grade_tests.grade_one(src[task["instance_id"]], patch, runner, timeout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", help="a routing.json arm")
    ap.add_argument("--part", default="test")
    ap.add_argument("--workers", type=int, default=4, help="repositories in parallel (tasks of one repo run in order)")
    ap.add_argument("--summary", action="store_true", help="print every arm's summary and exit")
    ap.add_argument("--allow-draft", action="store_true", help="run with routing.json not frozen (smoke runs only)")
    a = ap.parse_args()
    sys.path.insert(0, str(HERE))
    from common import RUNS, load_jsonl, read_json

    routing = load_routing()
    if a.summary:
        for arm in routing["arms"]:
            path = RUNS / f"routing_{arm}.jsonl"
            if path.exists():
                print(arm, json.dumps(summarize(load_jsonl(path))))
        return
    if a.arm not in routing["arms"]:
        raise SystemExit(f"--arm must be one of {list(routing['arms'])}")
    if not routing.get("frozen") and not a.allow_draft:
        raise SystemExit("routing.json is a draft: freeze it with the pre-registration first (or --allow-draft for a smoke run)")
    arm_cfg = routing["arms"][a.arm]
    units = [f"{m}|{routing['scaffold']}" for m in routing["models"] if not m.startswith("_")]
    planner = (Planner(units, reliability_target=arm_cfg["reliability_target"],
                       check_kind=routing["check"]["routing_check_kind"], max_rungs=routing["max_rungs"])
               if arm_cfg["policy"] == "route" else None)
    notes = {}
    if routing.get("memory_arm") and routing["memory_arm"] != "A0":
        notes = read_json(RUNS / f"notes_{routing['memory_arm']}.json")
    execute = _real_execute(routing, lambda iid: (notes.get(iid) or {}).get("text") or "")
    grade = _real_grade()
    out_path = RUNS / f"routing_{a.arm}.jsonl"
    done = {r["instance_id"] for r in load_jsonl(out_path)} if out_path.exists() else set()
    inst = read_json(RUNS / "instances.json")
    ids = [i for i in read_json(RUNS / "design.json")[a.part] if i not in done]
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for i in ids:
        by_repo[inst[i]["repo"]].append(inst[i])
    memory = RepoMemory()
    for r in load_jsonl(out_path) if out_path.exists() else []:    # a resumed run keeps what earlier tasks showed
        for att in r["attempts"]:
            memory.record(r["repo"], f"{att['model']}|{routing['scaffold']}", att["check"])
    print(f"routing {a.arm}: {len(ids)} tasks in {len(by_repo)} repos to run ({len(done)} done)", flush=True)

    def one_repo(tasks: list[dict]) -> None:
        for t in sorted(tasks, key=lambda t: (str(t.get("created_at") or ""), t["instance_id"])):
            try:
                rec = run_task(t, a.arm, arm_cfg, execute=execute, grade=grade, check_kind=routing["check"]["kind"],
                               scaffold=routing["scaffold"], max_rungs=routing["max_rungs"], memory=memory,
                               planner=planner)
            except Infra as exc:
                print(f"{t['instance_id']:<50} infrastructure: {exc.reason} (retried next run)", flush=True)
                continue
            with _lock, open(out_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(f"{t['instance_id']:<50} rungs={len(rec['attempts'])} accepted={rec['accepted']} "
                  f"resolved={rec['resolved']} cost={rec['cost_usd']}", flush=True)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(one_repo, by_repo.values()))


if __name__ == "__main__":
    main()
