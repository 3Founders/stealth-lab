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

1. Facts. If `.stealth/claims.md` is missing, run the survey_repo prompt first. Read
   `.stealth/SUMMARY.md` whole if it exists, then look the task's files, symbols and error text up in
   this repo's own solved problems: `rg -i '^<term>\\|' .stealth/index/terms.idx` -> library ids.
2. Ask. Call `find_ways(query=<the task>, repo_claims=<text of .stealth/claims.md>, ...)` once, adding
   this repo's library arguments when they exist: `stealthlab-mcp library payload` prints exactly
   `library_rows`, `route_obs` and `repo_identity` (pass them as they are).
   - `library_matches` (if any) come first: problems already solved IN THIS REPO. Read each one
     (`rg '^(GOAL|PROC|STEP)\\|<id>' .stealth/library.md`) and its diff (`.stealth/library/solutions/<id>.diff`)
     before anything global. A `stale` one touched files that changed since: re-check it before reuse.
   - "resolved": you get `procedures` (each with full `steps`, `alternatives`,
     `repo_fit`, and `verified_solution` when one was recorded: the code that passed its checks
     plus its `locator`; if `code` is null, open the locator yourself) and `unresolved`.
   - "ambiguous": if it names a `suggested` candidate, start from that one; otherwise pick the
     closest candidate yourself (or with the user) -- don't re-ask with near-identical wording.
   - "no_match": do the task without a Procedure, but still read any `related_examples`.
   - Any outcome may carry `related_examples`: verified solutions of SIMILAR past tasks. They are
     worked examples, not verified to apply here: compare each to your task, adapt what fits, and
     never copy one as-is.
   - `routing_rows` (with a model plan): save them with
     `stealthlab-mcp library route --from-reply <reply.json>` so this repo's outcomes count against them.
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
   after every attempt, pass or fail, and count it locally too:
   `stealthlab-mcp library obs <R-id from routing.md> --model <m> --scaffold <s> --ok|--fail`.
8. Learn. When a step needed a fix or there was a better way:
   - rewrite the remaining nodes in `run.md` now;
   - keep `.stealth/claims.md` true, in the survey_repo style (plain sentences that name
     their subject and carry their conditions). Add what this work taught about the repo:
     a decision you made and what you compared ("used the docx npm package, not
     python-docx, because the repo has no Python toolchain"), a feature the change added,
     a check that turned out slow or special, a problem you hit and its workaround. When a
     step showed a fact is wrong, set that line to `status=stale` and append the corrected
     fact -- never edit it in place. When two facts disagree, keep both and say so;
   - record what was solved HERE once its check passes, so the next task in this repo finds it:
     `stealthlab-mcp library add --title "<the problem, as a goal>" --check "<the command that proved it>"
     [--g <goal_id from find_ways>] [--p <procedure_id>] [--unit <package path>] [--step "<kind>|<do>|<check>"]...`
     (it re-runs the check and refuses if it fails; it saves the diff and rebuilds the indexes);
   - if it would help anyone doing this Procedure, not just this repo, call
     `report_discovery(kind, procedure_id, problem, solution, step_order, proof, repo)`
     with kind = fix | missing_step | precondition | better_way | correction | filled_gap.
     Pass `repo` when it only holds for this repository.
9. Finish: tell the user what was done, the proof, and what was learned."""


_V1_PROMPTS = [
    ("survey_repo", "Survey this repo into .stealth/claims.md", survey_repo),
    ("plan_and_run", "Do a task with StealthLab (plan, run, check, report)", plan_and_run),
]


def register_prompts(server) -> None:
    """Bind the survey_repo and plan_and_run prompts to `server`. Called once from server.py."""
    for name, title, fn in _V1_PROMPTS:
        server.prompt(name=name, title=title, description=(fn.__doc__ or "").strip())(fn)
