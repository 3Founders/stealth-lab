"""
MCP Prompts for StealthLab -- reusable host workflows / orchestration policy.

A Prompt here is NOT business logic and NOT autonomous execution. It is a
short policy the MCP host (Claude Desktop / Cursor / Codex / ...) can load
to know HOW to use StealthLab's tools and resources for a class of task:
which discovery tool to start from, when to prefer verified evidence, when
to read a Resource rather than call a tool, when to stop and decompose.

Each prompt returns plain text. Every tool / resource name it mentions is a
real current one on this server (server.py tools + resources.py URIs).
Prompts never tell the host to act unattended or to commit without review.

The prompt functions are module-level so they can be unit-tested by direct
call; register_prompts() binds them to the server.
"""
from __future__ import annotations

_TOOLS_NOTE = (
    "Tools available: search_procedures, get_procedure, check_applicability, "
    "check_procedure, decide_procedure, find_best_way, reproduce_procedure, "
    "report_execution, submit_procedure, retrieve_precedent, propose_synthesis, "
    "decompose_task, decide_decomposition, submit_approval, resolve_implementation, "
    "inspect_implementation, find_problem, inspect_problem, compare_solutions, "
    "inspect_evaluation, find_best_solution, inspect_run, resume_execution_run, "
    "retry_run_node. Resources (read-only): stealth://procedures/{id}, "
    "stealth://problems/{id}, stealth://problems/{id}/solutions, "
    "stealth://claims/{id}, stealth://evaluations/{id}, "
    "stealth://implementations/{id}, stealth://tasks/{id}/implementations, "
    "stealth://runs/{id}."
)


def solve_with_stealth(task: str, repo_path: str = "") -> str:
    """Policy for solving a coding/engineering task: reuse a verified procedure
    when one applies, otherwise decompose and solve normally."""
    return f"""You are solving a task with StealthLab's procedural memory.

Task: {task}
{f"Repository: {repo_path}" if repo_path else ""}

Policy:
1. Restate the objective and the hard constraints of the current environment
   (language, runtime, tools, versions, what must NOT change).
2. Search for relevant procedures with `search_procedures` (and, for a
   "have we solved this shape before" check, `retrieve_precedent`). For a
   larger method, `find_best_way` can retrieve + plan + execute in one call.
3. Read the strongest candidates via `stealth://procedures/<id>`. Compare
   their "Use when" and "Failure modes" sections to your actual situation.
4. Prefer a procedure whose verification state is `verified` and that has
   real recorded evidence. Treat `candidate` / unverified procedures as
   suggestions, not authority.
5. Run `check_applicability` on a chosen procedure against the current
   environment/state. A violated hard constraint is a disqualification,
   not a low score -- do not "adapt around" it.
6. If a usable procedure exists, ADAPT it to this task rather than
   rediscovering the method from scratch.
7. If no procedure is sufficient, `decompose_task` and solve normally;
   call `resolve_implementation` only for nodes that actually need a
   concrete executable mechanism.
8. Verify the stated postconditions after doing the work.
9. When the outcome is known, `report_execution` (success or failure, with
   the context key and real success criteria) so the evidence improves.

{_TOOLS_NOTE}"""


def debug_with_stealth(symptom: str, repo_path: str = "") -> str:
    """Policy for diagnosing a failure: pull relevant debugging precedents and
    evidence, form competing hypotheses, verify the fix."""
    return f"""You are debugging with StealthLab.

Symptom: {symptom}
{f"Repository: {repo_path}" if repo_path else ""}

Policy:
1. `search_procedures` for debugging procedures relevant to this symptom;
   `retrieve_precedent` for prior occurrences of the same failure shape.
2. Inspect observed failures and evidence: `stealth://runs/<id>` for a
   failing run, and the "Failure modes" / "Evidence" sections of
   `stealth://procedures/<id>` for known ways a candidate procedure breaks.
3. Form at least two competing hypotheses before committing to one. Do not
   latch onto the first plausible cause.
4. Use the MINIMUM relevant procedure / claim context -- read one or two
   `stealth://claims/<id>` that bear on the hypothesis, not the whole graph.
5. Apply the smallest fix that a hypothesis predicts will work.
6. Verify: re-run the failing check; if a procedure covers this,
   `reproduce_procedure` and compare expected vs observed.
7. `report_execution` with the outcome and context key.

{_TOOLS_NOTE}"""


def research_with_stealth(question: str) -> str:
    """Policy for answering a question from the substrate: separate verified
    evidence from hypotheses, surface contradictions and gaps."""
    return f"""You are researching a question against StealthLab's knowledge.

Question: {question}

Policy:
1. Gather the relevant objects: `search_procedures` / `find_problem` to
   locate them, then read `stealth://procedures/<id>`, `stealth://claims/<id>`,
   `stealth://problems/<id>`, `stealth://evaluations/<id>`.
2. Separate what is EVIDENCE-BACKED (verified procedures, completed
   evaluations, supported claims) from what is HYPOTHESIS (candidate
   procedures, disputed or unsupported claims, incomplete evaluations).
3. Never present an unverified procedure candidate or an unsupported claim
   as established fact. State its status explicitly.
4. Surface contradictions (claims that conflict) and evidence gaps
   (assertions with no supporting evaluation/execution) rather than
   smoothing them over.
5. Answer with the evidence-backed position first, then the open questions.

{_TOOLS_NOTE}"""


def improve_with_stealth(problem_id: str = "", goal: str = "") -> str:
    """Policy for challenging an incumbent solution: find a measurable
    weakness, try a challenger, compare under the same benchmark."""
    return f"""You are trying to improve on a known solution with StealthLab.

{f"Problem id: {problem_id}" if problem_id else ""}
{f"Goal: {goal}" if goal else ""}

Policy:
1. Identify the incumbent: `find_problem` (or the given problem id) then
   read `stealth://problems/<id>` and `stealth://problems/<id>/solutions`
   for the current best VERIFIED solution and its leaderboard.
2. Inspect the benchmark and its evaluations via `stealth://evaluations/<id>`
   -- understand exactly what is being measured and under what environment.
3. Identify ONE measurable weakness of the incumbent (a metric, a failure
   class, an environment it does not cover). Be specific and quantified.
4. Build or select a challenger method and `submit_procedure` it (private
   candidate by default). Do not submit a generic or noisy procedure.
5. Compare challenger vs incumbent with `compare_solutions` under the SAME
   benchmark and environment. Only completed, mutually-comparable
   evaluations count.
6. Report the comparison honestly, including where the challenger is worse.

{_TOOLS_NOTE}"""


def verify_with_stealth(procedure_id: str) -> str:
    """Policy for verifying a procedure: reproduce it, collect evidence,
    compare expected vs observed, report the result."""
    return f"""You are verifying a procedure with StealthLab.

Procedure id: {procedure_id}

Policy:
1. Read `stealth://procedures/{procedure_id}` -- note its stated
   preconditions, steps, constraints and postconditions.
2. `check_applicability` against the environment you will verify in. If a
   hard constraint fails, stop: this environment cannot verify it.
3. `reproduce_procedure` -- deliberately re-run the procedure's own steps
   against a real target.
4. Collect the evidence the run produces. Compare EXPECTED (the stated
   postconditions / success criteria) against OBSERVED.
5. `report_execution` with success or failure, the context key, and the
   explicit success criteria checked. A success outcome requires real
   criteria, never a bare "it worked".

{_TOOLS_NOTE}"""


def contribute_learning(summary: str = "") -> str:
    """Policy for capturing a genuinely reusable method after novel successful
    work, with provenance, without adding noise."""
    return f"""You are contributing a learning back to StealthLab.

{f"What was done: {summary}" if summary else ""}

Policy:
1. Only contribute after NOVEL successful work whose method is genuinely
   reusable. Routine or one-off work does not become a procedure.
2. Distinguish the three kinds of knowledge:
   - an OBSERVATION (what happened this once),
   - a CLAIM (a structured, checkable statement),
   - a PROCEDURE (a reusable abstract method).
   Only the reusable method is a `submit_procedure` candidate.
3. Preserve provenance: record where the method came from (the task, the
   repo, the source), not just the steps.
4. Do not submit a generic, vague, or noisy procedure -- it degrades
   retrieval for everyone. If it is not distinct and useful, do not submit.
5. `submit_procedure` creates a private candidate. It earns verification
   only through recorded evidence (`report_execution`), and human sign-off
   through `decide_procedure` -- do not claim it is verified.

{_TOOLS_NOTE}"""


_PROMPTS = [
    ("solve_with_stealth", "Solve a task using StealthLab", solve_with_stealth),
    ("debug_with_stealth", "Debug a failure using StealthLab", debug_with_stealth),
    ("research_with_stealth", "Research a question using StealthLab knowledge",
     research_with_stealth),
    ("improve_with_stealth", "Improve on a known solution using StealthLab",
     improve_with_stealth),
    ("verify_with_stealth", "Verify a procedure using StealthLab", verify_with_stealth),
    ("contribute_learning", "Contribute a reusable method back to StealthLab",
     contribute_learning),
]


def register_prompts(server) -> None:
    """Bind the recommended-workflow prompts to `server`. Called once from
    server.py."""
    for name, title, fn in _PROMPTS:
        server.prompt(name=name, title=title, description=(fn.__doc__ or "").strip())(fn)
