"""Kel used the way the product is used -- shared by the SWE-bench arm KP and DS-1000 round 4.

No environment setup here: the caller has already isolated the process to its own local database
(swebench/swe_env.py or ds1000/demo_env.py) before importing this module.

* `KelBridge(dsn)`: the real `find_ways` tool and the `stealth://procedures/<id>/claims` resource,
  in-process, on one background event loop shared by the agent's worker threads.
* `make_sandbox(root, claims_text)`: a RepoSandbox whose `.stealth/` working files never reach the
  patch or the edited-files list; seeds `.stealth/claims.md` (the survey_repo output) when given.
* `make_agent(...)`: app.execution.coding_agent.Agent with two extra tools (find_ways,
  read_procedure_claims); every Kel call is logged on the sandbox (`kel_log`).
* Delivery as in production: the v1 MCP server instructions go into the SYSTEM prompt (`mcp_system`, as a
  host injects them) and the user's message IS the `plan_and_run` prompt with the task inside it
  (`task_prompt`), followed by the experiment's fixed adaptations.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time

STEALTH = ".stealth"
CLAIMS_REF = "@.stealth/claims.md"

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


def mcp_system(base: str) -> str:
    """The agent's system prompt plus the MCP server's own instructions, as an MCP host injects them.
    Braces are escaped because Agent formats the system prompt with {repo}/{max_steps}."""
    from app.mcp_server.server import _V1_INSTRUCTIONS

    extra = "\n\nConnected MCP server: StealthLab (Kel). Its instructions:\n" + _V1_INSTRUCTIONS
    return base + extra.replace("{", "{{").replace("}", "}}")


def task_prompt(task: str, adaptations: str) -> str:
    """The user's message when they invoke the product's plan_and_run prompt for `task`, then the fixed
    adaptations of an unattended run."""
    from app.mcp_server import prompts

    return f"{prompts.plan_and_run(task)}\n\n{adaptations}"


def survey_instructions(adaptations: str) -> str:
    from app.mcp_server import prompts

    return f"This episode is NOT the task itself: make no change outside .stealth/.\n\n{prompts.survey_repo('.')}\n\n{adaptations}"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class KelBridge:
    """One asyncpg pool on a background event loop, shared by the agent's worker threads."""

    def __init__(self, dsn: str) -> None:
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        from app.db.session import create_pool

        self.pool = self._call(create_pool(dsn, min_size=1, max_size=6))

        class RC:
            def __init__(self, pool): self.lifespan_context = {"pool": pool}

        class Ctx:
            def __init__(self, pool): self.request_context = RC(pool)

        self._ctx = Ctx(self.pool)

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
            self._call(self.pool.close(), timeout=60)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)


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


def make_sandbox(root: str, claims_text: str | None = None):
    sb = _sandbox_cls()(root)
    if claims_text:
        os.makedirs(os.path.join(root, STEALTH), exist_ok=True)
        with open(os.path.join(root, STEALTH, "claims.md"), "w", encoding="utf-8", newline="") as fh:
            fh.write(claims_text)
    return sb


def make_agent(model_client, model: str, max_steps: int, temperature: float, bridge: KelBridge | None,
               tool_max_chars: dict, system: str | None = None, kel_tools: bool = True):
    """kel_tools=False gives the SAME agent (system prompt, sandbox) without Kel: a baseline arm."""
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
                        procs = d.get("procedures") or []
                        cand_ways = [w for c in (d.get("candidates") or []) for w in (c.get("ways") or [])]
                        summary = {"outcome": d.get("outcome"),
                                   "procedures": [str(p.get("procedure_id")) for p in procs if p.get("procedure_id")],
                                   "candidates": len(d.get("candidates") or []),
                                   "candidate_ways": [str(w.get("procedure_id")) for w in cand_ways],
                                   "verified_solution": any(p.get("verified_solution") for p in procs + cand_ways)}
                    except (json.JSONDecodeError, AttributeError):
                        summary = {"outcome": "unparsed"}
                else:
                    out = bridge.procedure_claims(str(args.get("procedure_id") or ""))
                    summary = {"procedure_id": args.get("procedure_id")}
            except Exception as exc:  # noqa: BLE001 -- a Kel failure is the agent's to handle, not a crash
                out, summary = f"error: {type(exc).__name__}: {str(exc)[:300]}", {"error": type(exc).__name__}
            sandbox.kel_log.append({"tool": name, "ms": round((time.time() - t0) * 1000), "chars": len(out),
                                    "query": str(args.get("query") or "")[:300] if name == "find_ways" else None,
                                    **summary})
            return out, False

    from app.execution.coding_agent import SYSTEM as AGENT_SYSTEM

    tools = [*TOOLS, *KP_TOOLS_EXTRA] if kel_tools else list(TOOLS)
    if kel_tools:
        system = mcp_system(system or AGENT_SYSTEM)
    return KPAgent(model_client, model, max_steps=max_steps, temperature=temperature, tools=tools,
                   tool_max_chars=dict(tool_max_chars), system=system)
