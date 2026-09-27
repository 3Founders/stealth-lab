"""Arm KP ("K-prod"): Kel used the way the product is used, not as a note pasted into the prompt.

    python kprod.py survey      # once per scored repo: the survey_repo workflow writes .stealth/claims.md

What the KP agent gets, on top of the identical coding tools, budget and decoding of every other arm:
* the product's own words, verbatim and where production puts them: the v1 MCP server instructions in
  the system prompt, and the `plan_and_run` prompt AS the user's message with the issue as its task;
  plus the `survey_repo` output (.stealth/claims.md, surveyed once per repo like a real user);
* the real `find_ways` as a TOOL it may call whenever and as often as it likes (in-process, frozen
  `kel_swebench`), and the `stealth://procedures/<id>/claims` resource as `read_procedure_claims`;
* its own `.stealth/` directory: it writes `procedures.md` and `run.md` itself, as the workflow says.
  Nothing under `.stealth/` is ever part of the submitted patch.

Fixed adaptations, because this is an unattended benchmark (every one is stated to the agent in
KP_ADAPTATIONS and in the protocol): no user to ask, no subagents, no code execution (same as every
arm), knowledge frozen (report_discovery / submit_way / recommend_models / report_model_run are not
offered), and `repo_claims` may be passed as "@.stealth/claims.md" so the file need not be retyped
inside a 2,000-token completion (find_ways receives the same text either way).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict

import swe_env
from generate import checkout, client, design, instances, release

sys.path.insert(0, str(swe_env.HERE.parent))
import kel_product_arm as kpa  # noqa: E402  (shared with DS-1000 round 4)

KP = swe_env.CONFIG["kprod"]
STEALTH, CLAIMS_REF = kpa.STEALTH, kpa.CLAIMS_REF
SURVEY_DIR = swe_env.RUNS / "kp_claims"
SURVEY_LOG = swe_env.RUNS / "kp_survey.json"

KP_ADAPTATIONS = """How this session differs from an interactive one (fixed for every task):
- There is no user. Wherever the workflow says to ask, show or get an OK from the user, decide yourself and go on.
- There are no subagents: do every node yourself.
- You cannot run code or tests. A node's `check` is done by reading the changed code carefully.
- The knowledge base is read-only here: report_discovery, submit_way, recommend_models and report_model_run are
  not available. Still keep .stealth/run.md and .stealth/claims.md up to date as the workflow says.
- .stealth/claims.md was already written by the survey_repo workflow for this repository. For repo_claims you may
  pass the literal "@.stealth/claims.md" instead of retyping the file.
- Write .stealth/ files with create_file / edit_file. They are your working files and are never part of the fix.
- The fix itself is your edits outside .stealth/; call finish when it is done."""
SURVEY_ADAPTATIONS = ("Adaptations for this session: you cannot run git, so write `sha=-` in each source; write the "
                      "file with create_file at .stealth/claims.md (edit_file to extend it); call finish when it is "
                      "written.")


def task_prompt(problem_statement: str) -> str:
    """KP's user message: the plan_and_run prompt with the issue as its task, then the fixed adaptations."""
    return kpa.task_prompt(problem_statement, KP_ADAPTATIONS)


def instructions() -> str:
    """Everything KP adds to the prompts, for the record (hashed into each attempt)."""
    from app.execution.coding_agent import SYSTEM

    return kpa.mcp_system(SYSTEM) + "\n" + task_prompt("<issue>")


def survey_instructions() -> str:
    return kpa.survey_instructions(SURVEY_ADAPTATIONS)


class KelBridge(kpa.KelBridge):
    def __init__(self) -> None:
        super().__init__(swe_env.DSN)


make_sandbox = kpa.make_sandbox


def make_agent(model_client, model: str, max_steps: int, temperature: float, bridge):
    return kpa.make_agent(model_client, model, max_steps, temperature, bridge, KP["tool_max_chars"])


# ------------------------------------------------------------------ survey_repo, once per repo

def survey_commit(repo: str) -> str:
    """The base commit of the repo's earliest held-out instance: the repo as the user first meets it."""
    inst = instances()
    ids = sorted((i for i in design()["test"] if inst[i]["repo"] == repo), key=lambda i: (str(inst[i]["created_at"]), i))
    return inst[ids[0]]["base_commit"]


def survey() -> None:
    from check_env import require_pinned

    require_pinned(scored=True)
    swe_env.verify_after_import()
    if not (swe_env.RUNS / "kel_frozen.json").exists():
        raise SystemExit("freeze Kel first (notes.py, step 7): KP runs only against frozen knowledge")
    SURVEY_DIR.mkdir(parents=True, exist_ok=True)
    log = json.loads(SURVEY_LOG.read_text(encoding="utf-8")) if SURVEY_LOG.exists() else {}
    inst = instances()
    repos = sorted({inst[i]["repo"] for i in design()["test"]})
    max_steps = swe_env.CONFIG["agent"]["max_steps"]
    agent = make_agent(client(), swe_env.CONFIG["model"]["id"], max_steps, swe_env.CONFIG["agent"]["temperature"], None)
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()

    def survey_one(repo: str) -> None:  # repos are independent; within a repo, attempts stay in order
        out = SURVEY_DIR / (repo.replace("/", "__") + ".md")
        if out.exists():
            return
        commit = survey_commit(repo)
        for attempt in (1, 2):
            wt = checkout(repo, commit, f"kp_survey_{repo.replace('/', '__')}_{attempt}")
            try:
                sb = make_sandbox(wt, None)
                task = {"instance_id": f"survey__{repo.replace('/', '__')}", "repo": repo,
                        "problem_statement": survey_instructions()}
                run = agent.run(task, sb, "KP_survey")
                path = os.path.join(wt, STEALTH, "claims.md")
                text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
            finally:
                release(repo, wt)
            with lock:
                log.setdefault(repo, []).append({"attempt": attempt, "commit": commit, "usage": asdict(run.usage),
                                                 "stop_reason": run.stop_reason, "claims_chars": len(text),
                                                 "stray_edits": sb.edited_files()})
                SURVEY_LOG.write_text(json.dumps(log, indent=1, sort_keys=True), encoding="utf-8")
            if text.strip():
                out.write_text(text, encoding="utf-8")
                break
        print(f"{repo:<30} claims.md: {out.stat().st_size if out.exists() else 0} bytes", flush=True)

    with ThreadPoolExecutor(max_workers=len(repos) or 1) as ex:
        list(ex.map(survey_one, repos))
    missing = [r for r in repos if not (SURVEY_DIR / (r.replace('/', '__') + '.md')).exists()]
    print(f"surveyed {len(repos) - len(missing)} of {len(repos)} repos" + (f"; NO claims.md for {missing} "
          "(KP runs there without repo facts -- record it in DEVIATIONS.md)" if missing else ""))


def claims_for(repo: str) -> str | None:
    path = SURVEY_DIR / (repo.replace("/", "__") + ".md")
    return path.read_text(encoding="utf-8") if path.exists() else None


def survey_tokens() -> dict[str, int]:
    """Total survey tokens per repo (every attempt), charged to KP in the analysis."""
    log = json.loads(SURVEY_LOG.read_text(encoding="utf-8")) if SURVEY_LOG.exists() else {}
    return {repo: sum((a["usage"].get("prompt_tokens", 0) + a["usage"].get("completion_tokens", 0)) for a in atts)
            for repo, atts in log.items()}


def instructions_sha() -> str:
    return hashlib.sha256(instructions().encode()).hexdigest()


if __name__ == "__main__":
    if sys.argv[1:] != ["survey"]:
        raise SystemExit("usage: kprod.py survey")
    survey()
