"""Run the agent for one arm over one part of the design; write attempts + SWE-bench predictions.

    python generate.py --part calibration --arm A0 --max-steps 40      # calibration only (unscored)
    python generate.py --part train --arm A0                           # the train pool Kel learns from
    python generate.py --part test  --arm A0|K|E|C1|C2|A0r|KP          # held-out arms (A0r: fresh repeat of A0)

* The agent is app.execution.coding_agent.Agent -- identical tools, budget and decoding for every arm;
  the ONLY difference between arms is the memory block appended to the first user message
  (runs/notes_<arm>.json), under one fixed "may not apply" header.
* Each attempt runs in a fresh git worktree of the repo at the instance's base_commit, deleted after.
* A held-out arm whose memory block is empty for an instance REUSES that instance's A0 attempt
  (byte-identical prompt), never re-samples it.
* KP ("K-prod", kprod.py) is the exception to "only the memory block differs": it is Kel used the
  way the product is used -- find_ways as a tool, the product's own instructions, .stealth/ files
  the agent writes itself (never part of the patch). Same model, budget, decoding and coding tools.
  It always runs fresh (never reuses A0), because the agent decides whether to use Kel.
* Resumable: an (instance, arm, part) already in attempts is skipped. A provider/infrastructure
  failure is recorded with `environmental_failure: true` and retried on the next invocation.

Outputs: runs/attempts_<part>_<arm>.jsonl and runs/predictions_<part>_<arm>.jsonl
({"instance_id", "model_name_or_path", "model_patch"} -- the official SWE-bench format).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import swe_env

HEADER = ("Notes from previous work on similar tasks in this repository (may or may not apply; verify "
          "against the current code before relying on them):\n")
_lock = threading.Lock()


def instances() -> dict:
    return json.loads((swe_env.RUNS / "instances.json").read_text(encoding="utf-8"))


def design() -> dict:
    return json.loads((swe_env.RUNS / "design.json").read_text(encoding="utf-8"))


def load_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def append(path, rec: dict) -> None:
    with _lock, open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def checkout(repo: str, commit: str, key: str) -> str:
    """A fresh worktree of `repo` at `commit` (partial clone cached under cache/)."""
    mirror = swe_env.CACHE / repo.replace("/", "__")
    with _lock:
        if not mirror.exists():
            _git("clone", "--filter=blob:none", "--no-checkout", f"https://github.com/{repo}.git", str(mirror))
    work = swe_env.CACHE / "work" / key
    if work.exists():
        _git("worktree", "remove", "--force", str(work), cwd=mirror, check=False)
        shutil.rmtree(work, ignore_errors=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        _git("worktree", "add", "--detach", str(work), commit, cwd=mirror)
    return str(work)


def release(repo: str, work: str) -> None:
    mirror = swe_env.CACHE / repo.replace("/", "__")
    with _lock:
        _git("worktree", "remove", "--force", work, cwd=mirror, check=False)
    shutil.rmtree(work, ignore_errors=True)


def client():
    import os

    from openai import OpenAI

    cfg = swe_env.CONFIG["model"]
    base = OpenAI(api_key=os.environ[cfg["api_key_env"]], base_url=os.environ[cfg["base_url_env"]], max_retries=0)
    order = cfg.get("openrouter_provider_order")
    if not order:
        return base

    class _Pinned:   # OpenRouter: pin the provider route; never fall back to another backend
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    kw.setdefault("extra_body", {})["provider"] = {"order": order, "allow_fallbacks": False}
                    return base.chat.completions.create(**kw)
    return _Pinned()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["calibration", "train", "test"], required=True)
    ap.add_argument("--arm", default="A0")
    ap.add_argument("--max-steps", type=int, help="calibration only")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    from check_env import require_pinned

    scored = a.part != "calibration"
    require_pinned(scored=scored)
    if scored and a.max_steps is not None:
        raise SystemExit("--max-steps is for calibration only; scored runs use experiment.json agent.max_steps")
    if a.part == "train" and a.arm != "A0":
        raise SystemExit("the train pool is run with A0 only")
    max_steps = a.max_steps if not scored else swe_env.CONFIG["agent"]["max_steps"]
    if max_steps is None:
        raise SystemExit("--max-steps is required for calibration")
    swe_env.verify_after_import()
    from app.execution.coding_agent import Agent, RepoSandbox

    tag = f"{a.part}_{a.arm}" + (f"_s{max_steps}" if not scored else "")
    attempts_path = swe_env.RUNS / f"attempts_{tag}.jsonl"
    preds_path = swe_env.RUNS / f"predictions_{tag}.jsonl"
    notes = {}
    if a.arm == "KP" and a.part != "test":
        raise SystemExit("KP is a held-out arm only")
    if a.arm not in ("A0", "A0r", "KP"):
        notes = json.loads((swe_env.RUNS / f"notes_{a.arm}.json").read_text(encoding="utf-8"))
    done = {r["instance_id"] for r in load_jsonl(attempts_path) if not r.get("environmental_failure")}
    a0 = {r["instance_id"]: r for r in load_jsonl(swe_env.RUNS / f"attempts_{a.part}_A0.jsonl")
          if not r.get("environmental_failure")}
    inst, ids = instances(), design()[a.part]
    if a.part == "test" and a.arm != "A0":
        missing_a0 = [i for i in ids if i not in a0]
        if missing_a0:
            raise SystemExit(f"run and finish `--part test --arm A0` first ({len(missing_a0)} instances missing): "
                             "the other arms reuse A0 wherever they have no notes")
        if not (swe_env.RUNS / "kel_frozen.json").exists():
            raise SystemExit("Kel is not frozen yet -- build the notes (notes.py) before any held-out arm")
    model = swe_env.CONFIG["model"]["id"]
    agent = Agent(client(), model, max_steps=max_steps, temperature=swe_env.CONFIG["agent"]["temperature"])
    bridge = kp_block = None
    if a.arm == "KP":
        import kprod

        missing_survey = sorted({inst[i]["repo"] for i in ids if kprod.claims_for(inst[i]["repo"]) is None})
        if missing_survey and not kprod.SURVEY_LOG.exists():
            raise SystemExit("run `python kprod.py survey` first (the survey_repo step writes .stealth/claims.md)")
        bridge = kprod.KelBridge()
        agent = kprod.make_agent(client(), model, max_steps, swe_env.CONFIG["agent"]["temperature"], bridge)
        kp_block = kprod.instructions()

    def record(iid: str, rec: dict) -> None:
        append(attempts_path, rec)
        if not rec.get("environmental_failure"):
            append(preds_path, {"instance_id": iid, "model_name_or_path": f"{tag}__{model}",
                                "model_patch": rec.get("patch") or ""})

    def work(iid: str) -> None:
        text = (notes.get(iid) or {}).get("text")
        if a.arm not in ("A0", "A0r", "KP") and not text and iid in a0:
            record(iid, {**a0[iid], "arm": a.arm, "reused_from": "A0"})
            print(f"{iid:<45} reused A0 (no notes)", flush=True)
            return
        row = inst[iid]
        memory = "" if a.arm == "KP" else ((HEADER + text) if text else "")
        started = time.time()
        wt = None
        try:
            wt = checkout(row["repo"], row["base_commit"], f"{tag}_{iid}".replace("/", "_"))
            if a.arm == "KP":
                import kprod

                sandbox = kprod.make_sandbox(wt, kprod.claims_for(row["repo"]))
                row = {**row, "problem_statement": kprod.task_prompt(row["problem_statement"])}
            else:
                sandbox = RepoSandbox(wt)
            run = agent.run(row, sandbox, a.arm, memory_block=memory)
            added = kp_block if a.arm == "KP" else memory
            rec = {**asdict(run), "usage": asdict(run.usage), "part": a.part, "max_steps": max_steps,
                   "memory_sha256": hashlib.sha256(added.encode()).hexdigest(), "memory_chars": len(added),
                   "environmental_failure": bool(run.error and not run.patch)}
            if a.arm == "KP":
                rec["kel_calls"] = sandbox.kel_log
        except Exception as exc:  # noqa: BLE001 -- infrastructure, not the arm: retried next invocation
            rec = {"instance_id": iid, "arm": a.arm, "part": a.part, "environmental_failure": True,
                   "error": f"{type(exc).__name__}: {str(exc)[:300]}", "wall_seconds": time.time() - started}
        finally:
            if wt:
                release(row["repo"], wt)
        record(iid, rec)
        print(f"{iid:<45} steps={rec.get('steps')} stop={rec.get('stop_reason')} "
              f"patch={'Y' if rec.get('patch') else '-'} env_fail={rec.get('environmental_failure')}", flush=True)

    todo = [i for i in ids if i not in done]
    print(f"{tag}: {len(todo)} to run ({len(done)} done), model={model}, max_steps={max_steps}", flush=True)
    try:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(ex.map(work, todo))
    finally:
        if bridge is not None:
            bridge.close()


if __name__ == "__main__":
    main()
