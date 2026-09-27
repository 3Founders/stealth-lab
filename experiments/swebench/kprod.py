"""Arm KP ("K-prod"): Kel used the way the product is used, not as a note pasted into the prompt.

    python kprod.py survey      # once per scored repo: the survey_repo workflow writes .stealth/claims.md

What the KP agent gets, on top of the identical coding tools, budget and decoding of every other arm:
* the product's own words, verbatim: the v1 MCP server instructions, the `survey_repo` output
  (.stealth/claims.md, surveyed once per repo like a real user), and the `plan_and_run` prompt;
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

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import asdict

import swe_env
from generate import checkout, client, design, instances, release

KP = swe_env.CONFIG["kprod"]
STEALTH = ".stealth"
CLAIMS_REF = "@.stealth/claims.md"
SURVEY_DIR = swe_env.RUNS / "kp_claims"
SURVEY_LOG = swe_env.RUNS / "kp_survey.json"

KP_TOOLS_EXTRA = [
    {"type": "function", "function": {
        "name": "find_ways",
        "description": ("StealthLab: find the known ways to do something. Returns knowledge (the Goal, the chosen "
                        "Procedure(s) with every step, alternatives, repo_fit, and verified_solution when one was "
                        "recorded), not a plan."),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What you want done, in plain words."},
            "repo_claims": {"type": "string", "description": (
                "The text of this repo's .stealth/claims.md, or the literal '" + CLAIMS_REF + "' to send that "
                "file as it is on disk.")}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_procedure_claims",
        "description": "Read the StealthLab resource stealth://procedures/<procedure_id>/claims.",
        "parameters": {"type": "object", "properties": {
            "procedure_id": {"type": "string"}}, "required": ["procedure_id"]}}},
]

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


def instructions() -> str:
    """The KP memory block: the product's instructions verbatim, then the fixed adaptations."""
    from app.mcp_server import prompts
    from app.mcp_server.server import _V1_INSTRUCTIONS

    return ("StealthLab (Kel) is connected to this session. Its instructions, as the product gives them:\n"
            f"{_V1_INSTRUCTIONS}\n\nThe product's plan_and_run workflow for this task:\n"
            f"{prompts.plan_and_run('the issue above')}\n\n{KP_ADAPTATIONS}\n")


def survey_instructions() -> str:
    from app.mcp_server import prompts

    return ("This episode is NOT a bug fix: make no change outside .stealth/.\n\n"
            f"{prompts.survey_repo('.')}\n\n"
            "Adaptations for this session: you cannot run git, so write `sha=-` in each source; write the file "
            "with create_file at .stealth/claims.md (edit_file to extend it); call finish when it is written.")


# ------------------------------------------------------------------ Kel, in-process on its own event loop

class KelBridge:
    """One asyncpg pool on a background event loop, shared by the agent's worker threads."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        from app.db.session import create_pool

        self._pool = self._call(create_pool(swe_env.DSN, min_size=1, max_size=6))

        class RC:
            def __init__(self, pool): self.lifespan_context = {"pool": pool}

        class Ctx:
            def __init__(self, pool): self.request_context = RC(pool)

        self._ctx = Ctx(self._pool)

    def _call(self, coro, timeout: float = 900):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def find_ways(self, query: str, repo_claims: str) -> str:
        import app.mcp_server.server as srv

        return self._call(srv.find_ways(query, self._ctx, repo_claims=repo_claims))

    def procedure_claims(self, procedure_id: str) -> str:
        from app.mcp_server.resources import procedure_claims_resource

        return self._call(procedure_claims_resource(procedure_id, self._ctx))

    def close(self) -> None:
        try:
            self._call(self._pool.close(), timeout=60)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)


# ------------------------------------------------------------------ sandbox + agent

def _sandbox_cls():
    from app.execution.coding_agent import RepoSandbox

    class KPSandbox(RepoSandbox):
        """A RepoSandbox whose .stealth/ working files never reach the patch or the 'edited files' list."""

        def __init__(self, root: str):
            super().__init__(root)
            self.kel_log: list[dict] = []

        def _without_stealth(self) -> dict:
            return {k: v for k, v in self._original.items() if not (k == STEALTH or k.startswith(STEALTH + "/"))}

        def edited_files(self) -> list[str]:
            return sorted(self._without_stealth())

        def diff(self) -> str:
            full, self._original = self._original, self._without_stealth()
            try:
                return super().diff()
            finally:
                self._original = full

    return KPSandbox


def make_sandbox(worktree: str, claims_text: str | None):
    sb = _sandbox_cls()(worktree)
    if claims_text:
        os.makedirs(os.path.join(worktree, STEALTH), exist_ok=True)
        with open(os.path.join(worktree, STEALTH, "claims.md"), "w", encoding="utf-8", newline="") as fh:
            fh.write(claims_text)
    return sb


def make_agent(model_client, model: str, max_steps: int, temperature: float, bridge: KelBridge | None):
    from app.execution.coding_agent import TOOLS, Agent

    class KPAgent(Agent):
        def _dispatch(self, name, args, sandbox):   # instance method: Agent.run calls self._dispatch
            if name not in ("find_ways", "read_procedure_claims"):
                return Agent._dispatch(name, args, sandbox)
            if bridge is None:
                return "StealthLab is not available in this episode.", False
            t0 = time.time()
            try:
                if name == "find_ways":
                    claims = str(args.get("repo_claims") or "")
                    if claims.strip() == CLAIMS_REF:
                        path = os.path.join(sandbox.root, STEALTH, "claims.md")
                        claims = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
                    out = bridge.find_ways(str(args.get("query") or ""), claims[:65536])
                    try:
                        d = json.loads(out)
                        summary = {"outcome": d.get("outcome"), "procedures": [
                            str(p.get("procedure_id")) for p in (d.get("procedures") or []) if p.get("procedure_id")],
                            "candidates": len(d.get("candidates") or []),
                            "verified_solution": any(p.get("verified_solution") for p in (d.get("procedures") or []))}
                    except (json.JSONDecodeError, AttributeError):
                        summary = {"outcome": "unparsed"}
                else:
                    out = bridge.procedure_claims(str(args.get("procedure_id") or ""))
                    summary = {"procedure_id": args.get("procedure_id"), "chars": len(out)}
            except Exception as exc:  # noqa: BLE001 -- a Kel failure is the agent's to handle, not a crash
                out, summary = f"error: {type(exc).__name__}: {str(exc)[:300]}", {"error": type(exc).__name__}
            sandbox.kel_log.append({"tool": name, "ms": round((time.time() - t0) * 1000), "chars": len(out),
                                    "query": str(args.get("query") or "")[:300] if name == "find_ways" else None,
                                    **summary})
            return out, False

    return KPAgent(model_client, model, max_steps=max_steps, temperature=temperature,
                   tools=[*TOOLS, *KP_TOOLS_EXTRA], tool_max_chars=dict(KP["tool_max_chars"]))


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
    for repo in repos:
        out = SURVEY_DIR / (repo.replace("/", "__") + ".md")
        if out.exists():
            continue
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
            log.setdefault(repo, []).append({"attempt": attempt, "commit": commit, "usage": asdict(run.usage),
                                             "stop_reason": run.stop_reason, "claims_chars": len(text),
                                             "stray_edits": sb.edited_files()})
            SURVEY_LOG.write_text(json.dumps(log, indent=1), encoding="utf-8")
            if text.strip():
                out.write_text(text, encoding="utf-8")
                break
        print(f"{repo:<30} claims.md: {out.stat().st_size if out.exists() else 0} bytes", flush=True)
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
