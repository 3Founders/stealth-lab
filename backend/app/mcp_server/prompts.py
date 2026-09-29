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
    "Tools available: init_workspace, search_procedures, get_procedure, check_applicability, "
    "check_procedure, decide_procedure, find_best_way, reproduce_procedure, "
    "report_execution, submit_procedure, retrieve_precedent, "
    "search_goals, inspect_goal, "
    "inspect_run, resume_execution_run, "
    "retry_run_node, record_run_update, preview_local_sync, commit_local_sync, "
    "record_stealth_edit, list_stealth_edits. "
    # find_problem/inspect_problem/compare_solutions/inspect_evaluation/
    # find_best_solution were already removed from the MCP tool surface
    # (product-model tools pruned, then Problem itself folded into Goal
    # by migration 110) -- this note was still listing them as if live.
    "Resources (read-only): "
    "stealth://procedures/{id}, "
    "stealth://goals/{id}, stealth://goals/{id}/solutions, "
    "stealth://claims/{id}, stealth://evaluations/{id}, "
    ""
    "stealth://runs/{id}."
)

_INIT_WORKSPACE_NOTE = (
    "Session start / first tool use for a given repo_path? Call "
    "`init_workspace(repo_path)` first, before anything else in this "
    "list -- it bootstraps a new workspace (probes the environment, "
    "checks for AGENTS.md/CLAUDE.md/README/CI config, never fabricates a "
    "Claim) on a first connection, or surfaces an in-progress run's open "
    "blockers/handoffs/questions to pick up on a later one. Idempotent --"
    " safe to call again if unsure whether it already ran this session."
)


def solve_with_stealth(task: str, repo_path: str = "") -> str:
    """Policy for solving a coding/engineering task: reuse a verified procedure
    when one applies, otherwise decompose and solve normally."""
    return f"""You are solving a task with StealthLab's procedural memory.

Task: {task}
{f"Repository: {repo_path}" if repo_path else ""}

Policy:
0. {f"{_INIT_WORKSPACE_NOTE}" if repo_path else "(no repo_path given -- init_workspace needs one; skip this step)"}
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
7. If no procedure is sufficient, solve it yourself using your own
   reasoning and tools against the real task/repository; use a step's
   `binding` (see the procedure's steps) only for nodes that actually need a
   concrete executable mechanism.
8. Resuming or continuing an existing run (`resume_execution_run`,
   `retry_run_node`)? Check `.stealth/run.md`'s COLLAB_SUMMARY and
   COLLAB lines first -- an open BLOCKER, a pending HANDOFF, or an
   unanswered QUESTION left by another agent working the same run.
   Record your own notes/blockers/handoffs with `record_run_update` so
   the next agent sees them too.
9. Verify the stated postconditions after doing the work.
10. When the outcome is known, `report_execution` (success or failure, with
    the context key and real success criteria) so the evidence improves.

{_TOOLS_NOTE}"""


def debug_with_stealth(symptom: str, repo_path: str = "") -> str:
    """Policy for diagnosing a failure: pull relevant debugging precedents and
    evidence, form competing hypotheses, verify the fix."""
    return f"""You are debugging with StealthLab.

Symptom: {symptom}
{f"Repository: {repo_path}" if repo_path else ""}

Policy:
0. {f"{_INIT_WORKSPACE_NOTE}" if repo_path else "(no repo_path given -- init_workspace needs one; skip this step)"}
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
1. Gather the relevant objects: `search_procedures` / `search_goals` to
   locate them, then read `stealth://procedures/<id>`, `stealth://claims/<id>`,
   `stealth://goals/<id>`, `stealth://evaluations/<id>`.
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


def improve_with_stealth(goal_id: str = "", goal: str = "") -> str:
    """Policy for challenging an incumbent solution: find a measurable
    weakness, try a challenger, compare under the same benchmark."""
    return f"""You are trying to improve on a known solution with StealthLab.

{f"Goal id: {goal_id}" if goal_id else ""}
{f"Goal: {goal}" if goal else ""}

Policy:
1. Identify the incumbent: `search_goals` (or the given goal id) then
   read `stealth://goals/<id>` and `stealth://goals/<id>/solutions`
   for the current best VERIFIED solution and its leaderboard.
2. Inspect the benchmark and its evaluations via `stealth://evaluations/<id>`
   -- understand exactly what is being measured and under what environment.
3. Identify ONE measurable weakness of the incumbent (a metric, a failure
   class, an environment it does not cover). Be specific and quantified.
4. Build or select a challenger method and `submit_procedure` it (private
   candidate by default). Do not submit a generic or noisy procedure.
5. Compare challenger vs incumbent by reading `stealth://goals/<id>`'s
   leaderboard for both under the SAME benchmark and environment. Only
   completed, mutually-comparable evaluations count.
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


# ===========================================================================
# v1 prompts (final_architecture.md). The only two on the v1 surface. They
# name only v1 tools/resources, and they run on the CLIENT: only the agent
# can see the user's repo, so the server never reads or writes it.
# ===========================================================================

CLAIMS_MD_FORMAT = (
    "CLAIM|<id>|<status>|<topic>|repository|<statement>|source=<path>:<line>#sha=<sha7>|version=<n>"
)
RUN_MD_FORMAT = (
    "NODE|<node_id>|<status>|<what to do>|step=<P-n>:<order>|claims=<R-a..R-b,...>|deps=<node_ids or ->|check=<how to tell it worked>"
)
_TOPICS = ("purpose, stack, runtime, deps, build, test, lint, ci, layout, conventions, features, "
           "decisions, env, issues, absent")

# What makes a repository fact useful to find_ways, in plain language. Drawn from the leaderboard-
# extraction work (Singh et al., "Automated Early Leaderboard Generation From Comparative Tables",
# arXiv:1802.04538; see docs/claim_formation.md): which approach fits depends on the whole context
# (task, data, conditions, what is measured, trade-offs), a fact read on its own loses that context,
# comparisons need to say what was compared and who reported it, and a missing comparison is not a win.
# Kept as guidance for the writer, not as fields: facts stay natural-language sentences end to end.
_WHAT_TO_CAPTURE = """   The choice of a way to do a task depends on this repository's context: what it is for,
   what it runs on, what it already has, what it has decided, and what it prefers when
   goals conflict. Capture that context. Useful kinds of facts:
   - purpose: what the project is and who uses it (a CLI, a web app, a library, a service),
     and its main surfaces. It decides which goal a vague request means.
   - stack, runtime, deps: languages, runtimes and frameworks with their versions, and the
     libraries that matter for everyday tasks, by the names people search for (give the
     package's registry name and the name people use for it, e.g. "the docx npm package
     (docx-js)").
   - build, test, lint, ci: how work is checked here -- the exact commands, what CI must
     pass, and anything slow or special (needs a database, needs network, runs only on Linux).
   - layout, conventions: where things live and the patterns new code must follow.
   - features: what already exists that a task might build on or duplicate ("PDF export
     exists in src/export/pdf.ts"). Knowing the strongest thing already here is what keeps a
     plan from rebuilding it.
   - decisions: choices the repo made between alternatives, with what was compared and why
     ("uses pnpm, not npm, because of workspaces (README)"; "moved from Jest to Vitest").
     A decision says which way fits; a bare list of tools doesn't.
   - env: the platform, operating system, deployment target, and which secrets or services
     are needed (names only).
   - issues: known problems, flaky tests, workarounds, deprecations, and migrations in
     progress -- the current state, not the history.
   - absent: what was looked for and is not there ("No Python toolchain", "No end-to-end
     tests"). Silence is not support: a missing fact means unknown, not "fine"."""

_HOW_TO_WRITE = """   - Each sentence stands on its own. Carry its context inside it: the version, the place,
     and the condition under which it holds ("On CI only, tests run against Postgres 15").
     Never "it", "this" or "the library" -- name the thing.
   - When a fact involves a trade-off or a direction, say which side the repo prefers
     ("prefers a smaller bundle over build speed", "lower is better").
   - When a fact is a comparison or a choice, say what was compared, what won, and where
     that is stated.
   - Say a fact once. If two sources disagree, write both and say in the statement that they
     disagree; don't pick one silently.
   - Prefer what is true now over what used to be; mark a migration as in progress.
   - Write only what a file shows. If something looks surprising, say where it came from
     rather than smoothing it over."""


def survey_repo(repo_path: str = "") -> str:
    """Look around this repo and write .stealth/claims.md: short, cited facts
    about its purpose, stack, how work is checked, what exists, what it has
    decided and what is missing, that find_ways uses to pick Procedures that fit."""
    where = f" at `{repo_path}`" if repo_path else ""
    return f"""Survey the repository{where} and write `.stealth/claims.md`.

1. Read only what states facts, not the whole codebase: manifests and
   lockfiles (package.json, pyproject.toml, requirements*.txt, go.mod,
   Cargo.toml, ...), version pins (.nvmrc, .python-version, .tool-versions),
   build/test config (Makefile, tsconfig, jest/vitest/pytest config),
   CI workflows, Dockerfile, README / AGENTS.md / CLAUDE.md / CONTRIBUTING,
   CHANGELOG, .env.example, and the top level of the source tree.
2. What to capture.
{_WHAT_TO_CAPTURE}
3. Write one fact per line, in this exact format:
   `{CLAIMS_MD_FORMAT}`
   e.g. `CLAIM|R-001|current|runtime|repository|Node 20.11|source=.nvmrc:1#sha=9f2c1ab|version=1`
   - statement: one plain sentence a stranger could check ("Tests run with `pnpm test`").
{_HOW_TO_WRITE}
   - source: the file and line that shows it; sha = first 7 chars of
     `git hash-object <path>`, so the fact goes stale when the file changes.
   - Facts about absence use topic `absent` and source=`search:<what you looked for>`.
4. Group by topic, in this order: {_TOPICS}. Number ids R-001, R-002, ...
   straight down the file, so every topic is one contiguous range and an
   agent can read a whole topic in one `rg`/read.
5. Rules: only what a file actually shows -- never guess. Never copy a
   secret or an env value (names only). At most 200 facts; prefer the ones
   that change how work is done or which way fits. Start the file with a
   `#` comment line saying what it is and when it was written.
6. Re-survey: keep the id of a fact that still holds, set `status=stale` on
   one whose source line changed and re-check it, append new facts at the
   end of their topic block.

These facts stay on this machine. They're sent only as the `repo_claims`
argument of find_ways, used for that one request, and never stored."""


def plan_and_run(task: str) -> str:
    """Do a task with StealthLab: get the known ways with find_ways, compile
    your own plan into .stealth/run.md, do or delegate each step, check it,
    and report what you learned."""
    return f"""Task: {task}

You are the planner. StealthLab returns knowledge; you make the plan and
own every file in `.stealth/`.

1. Facts. If `.stealth/claims.md` is missing, run the survey_repo prompt first.
2. Ask. Call `find_ways(query=<the task>, repo_claims=<text of .stealth/claims.md>)` once.
   - "resolved": you get `procedures` (each with full `steps`, `alternatives`,
     `repo_fit`, and `verified_solution` when one was recorded: the code that passed its checks
     plus its `locator`; if `code` is null, open the locator yourself) and `unresolved`.
   - "ambiguous": if it names a `suggested` candidate, start from that one; otherwise pick the
     closest candidate yourself (or with the user) -- don't re-ask with near-identical wording.
   - "no_match": do the task without a Procedure, but still read any `related_examples`.
   - Any outcome may carry `related_examples`: verified solutions of SIMILAR past tasks. They are
     worked examples, not verified to apply here: compare each to your task, adapt what fits, and
     never copy one as-is.
   Small task? If the way you're following has one or two steps, skip steps 4-5: just do it,
   check it, and go to step 7.
3. Read past discoveries: `stealth://procedures/<procedure_id>/claims` for each chosen Procedure.
4. Write `.stealth/procedures.md`: each Procedure under its own header so its steps sit together:
   `PROCEDURE|P-1|<procedure_id>|v<version>|<name>|goal=<goal_name>`
   `STEP|P-1:<order>|<action|instruction|subgoal>|<do>|locator=<source_locator.uri or ->|needs=<k=v,...>|check=<check or ->`
5. Compile `.stealth/run.md` -- one line per unit of work:
   `{RUN_MD_FORMAT}`
   e.g. `NODE|N-3|ready|Configure page size|step=P-1:3|claims=R-001..R-004|deps=N-2|check=node build.js && test -s out.docx`
   - Expand `subgoal` steps into that sub-Procedure's steps. `instruction`
     steps are real work too: do them from their text.
   - Drop what the repo already has, as `skipped` with the fact that shows it.
   - `claims=` names the contiguous fact ranges the step needs; keep it small.
   - `check=` must be concrete (a command, a file that must exist, a test).
     If the Procedure gives none, write one.
   - status: ready | blocked | running | done | failed | skipped.
   Show the plan to the user and get an OK before changing their code.
6. Do. For each ready node whose deps are done, either do it yourself
   (short plans) or give a subagent ONE line and nothing else:
   `Do node N-3. Read: rg "N-3" .stealth/run.md, then the claims and step lines it names. Reply with: result, proof (diff / command output), anything you learned.`
   Use subagents for long plans, independent steps in parallel, or when you
   want the work checked by someone who didn't do it.
   Optional, to keep cost down: before a node, call `recommend_models(procedure_id,
   candidates=<the models you can run, "model|scaffold">, step_order=<the node's step>,
   step_role=plan|edit|verify|other, instance_key=<one key for this whole run>,
   previous_steps=<earlier nodes: step_order, unit, accepted>, remaining_steps=<nodes still
   to come>)` and run the node (or its subagent) with the first model of the returned
   ladder; after its check, if the ladder has a next model, that is the retry.
7. Check. Run the node's `check` yourself. Pass: mark `done` and note the
   proof. Fail: retry once with the error, else try an alternative
   Procedure, else ask the user. If you used recommend_models, call
   `report_model_run(model, scaffold, accepted=<check passed>, instance_key, procedure_id,
   step_order, step_role, check_kind="tests" or "procedure_check", tokens_in, tokens_out)`
   after every attempt, pass or fail.
8. Learn. When a step needed a fix or there was a better way:
   - rewrite the remaining nodes in `run.md` now;
   - keep `.stealth/claims.md` true, in the survey_repo style (plain sentences that name
     their subject and carry their conditions). Add what this work taught about the repo:
     a decision you made and what you compared ("used the docx npm package, not
     python-docx, because the repo has no Python toolchain"), a feature the change added,
     a check that turned out slow or special, a problem you hit and its workaround. When a
     step showed a fact is wrong, set that line to `status=stale` and append the corrected
     fact -- never edit it in place. When two facts disagree, keep both and say so;
   - if it would help anyone doing this Procedure, not just this repo, call
     `report_discovery(kind, procedure_id, problem, solution, step_order, proof, repo)`
     with kind = fix | missing_step | precondition | better_way | correction | filled_gap.
     Pass `repo` when it only holds for this repository.
9. Finish: tell the user what was done, the proof, and what was learned."""


_V1_PROMPTS = [
    ("survey_repo", "Survey this repo into .stealth/claims.md", survey_repo),
    ("plan_and_run", "Do a task with StealthLab (plan, run, check, report)", plan_and_run),
]


def prompts_for_surface(surface: str = "v2") -> list:
    """v1: only survey_repo + plan_and_run. v2: everything."""
    return list(_V1_PROMPTS) if surface == "v1" else [*_PROMPTS, *_V1_PROMPTS]


def register_prompts(server, surface: str = "v2") -> None:
    """Bind the prompts for `surface` to `server`. Called once from server.py."""
    for name, title, fn in prompts_for_surface(surface):
        server.prompt(name=name, title=title, description=(fn.__doc__ or "").strip())(fn)
