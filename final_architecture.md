# final_architecture.md — StealthLab v1

How v1 works, in simple points. `final_thing.md` has the product story and
checklist; this file is the architecture as built.

---

## One line

StealthLab **knows** proven ways to do things. The agent in your repo **plans
and does** the work. StealthLab never plans, never runs anything, and never
touches your repo.

---

## Who does what

**StealthLab server**
- Stores the shared Goals, Procedures and claims.
- Searches them: text → Goal → the Procedure that fits your repo.
- Returns knowledge: full steps, alternatives, and why it chose them.
- Saves what agents learn (`report_discovery`), private to the reporter in v1.

**Planner (the main agent, e.g. a Claude Code session)**
- Surveys the repo into `.stealth/claims.md`.
- Calls `find_ways` and reads the knowledge.
- **Compiles the plan itself** into `.stealth/procedures.md` and `.stealth/run.md`.
- Does each step, or hands it to an executor.
- Checks every step's proof.
- Rewrites the plan when something changes, and reports what it learned.

**Executor (optional subagent)**
- Gets one line, reads a few lines from `.stealth/`, does one step, and replies
  with the result, the proof, and anything it learned.
- Only worth using for long plans, parallel steps, or an independent check.
  Short tasks: the planner does everything itself.

---

## The flow

**Once per repo**
1. The agent runs the `survey_repo` prompt and writes `.stealth/claims.md`:
   short facts, each citing file:line.

**Per task**
2. The agent calls `find_ways(query, repo_claims=<claims.md text>)`.
3. StealthLab picks the Goal, then the Procedure that fits the repo facts, and
   returns every step in full.
4. The agent writes `procedures.md` and `run.md` and shows you the plan.
   Nothing changes until you say OK.
5. For each ready step, the agent does it (or sends a one-line packet) and runs
   the step's check.
6. Pass → `done`. Fail → retry, try an alternative Procedure, or ask you.

**Learning**
7. The agent fixes the plan right away and adds new repo facts to `claims.md`.
8. Anything useful beyond this repo goes to `report_discovery`. Next time, it
   comes back through `stealth://procedures/{id}/claims`.

---

## MCP v1 surface

| Kind | Name | What it does | Access |
|---|---|---|---|
| tool | `find_ways(query, repo_claims?)` | Search and return knowledge. No compiling, no running, no file writes. | free (no token) |
| tool | `report_discovery(kind, procedure_id, problem, solution, step_order?, proof?, repo?)` | Save a fix / missing step / better way as a private candidate claim. Text is redacted. | signed-in user |
| resource | `stealth://procedures/{id}/claims` | Past discoveries for that Procedure, then the claims its preconditions cite, then related claims | caller-scoped |
| resource | `stealth://goals/{id}/claims` | Claims related to a Goal | caller-scoped |
| resource | `stealth://claims/{id}` | One claim and its evidence | caller-scoped |
| prompt | `survey_repo(repo_path?)` | How to write `.stealth/claims.md` | runs on the client |
| prompt | `plan_and_run(task)` | How to compile `run.md`, dispatch, check, and report | runs on the client |

- `STEALTHLAB_MCP_SURFACE=v1` is the default.
- `STEALTHLAB_MCP_SURFACE=v2` brings back the older tools (still in code).

---

## What `find_ways` returns

One of three outcomes. It never guesses.
- `resolved`: one Goal is clearly best (or repo facts broke a tie).
- `ambiguous`: 2+ Goals are too close. You pick, or rephrase.
- `no_match`: nothing known. Do the task without StealthLab.

When resolved (trimmed example):

```json
{
  "outcome": "resolved",
  "goal": {"goal_id": "G-root", "name": "Create a DOCX document", "check": null},
  "procedures": [{
    "goal_id": "G-root", "parent": null,
    "procedure_id": "P-1", "version_id": "V-1", "name": "docx-js", "version": 3,
    "what_it_does": "Make .docx files with docx-js",
    "why_chosen": "selected procedure 'docx-js' among 2 feasible / 3 linked",
    "repo_fit": {"verdict": "APPLICABLE", "supporting_fact_ids": ["R-002"], "blocking_fact_ids": []},
    "alternatives": [{"procedure_id": "P-9", "name": "python-docx"}],
    "steps": [
      {"order": 0, "kind": "subgoal", "do": "Install the docx package", "subgoal_id": "G-sub"},
      {"order": 1, "kind": "instruction", "do": "Write build.js that creates a Document"},
      {"order": 2, "kind": "action", "do": "Validate the file",
       "source_locator": {"uri": "https://raw.githubusercontent.com/..."},
       "needs": {"runtime": "python"}, "check": null}
    ]
  }],
  "unresolved": [],
  "repo_facts": {"count": 24, "goal_tiebreak": null, "procedure_check": {"status": "ok"}},
  "next": "Compile this into .stealth/procedures.md and .stealth/run.md yourself ..."
}
```

**Step kinds**
- `action`: has a script or tool (`binding`) and a `source_locator` to fetch.
- `subgoal`: its own Goal, with its own entry in `procedures`.
- `instruction`: plain text; the agent does it directly.

**Gaps and order**
- `check: null` means the Procedure gives no success check, so the planner
  writes one. Nothing is invented.
- There are no node ids and no order. `procedures` is a reading order only;
  the planner decides the real order.

---

## How repo facts are used

- They're sent with each request, used once, and never stored or logged.
  Limit: 200 facts / 64 KB.
- **Picking the Goal:** only when search is `ambiguous`. A cheap word-overlap
  score breaks the tie only if one Goal fits clearly better (same 0.12
  margin). Otherwise the result stays ambiguous.
- **Picking the Procedure:** for each feasible Procedure, the 5–20 most related
  facts go to the judge chain (JEV → Gemini → Gemma), together with:
  - its written preconditions, as **REQUIRED**: if one is contradicted (≥ 0.75),
    the Procedure is dropped;
  - its steps' runtime needs (e.g. `runtime: python`), as
    **IMPLEMENTATION_BINDING**: if contradicted, the Procedure moves to the
    back but isn't dropped, because you could install Python.
- **When the judge is down:** the order is left as-is and the result says
  `not_checked`. The plan is never blocked.
- **Budget:** at most 5 judge calls per request.

---

## The local files (all written by the planner)

**`.stealth/claims.md`**: repo facts, grouped by topic so a topic is one
contiguous range:
```
CLAIM|R-001|current|runtime|repository|Node 20.11|source=.nvmrc:1#sha=9f2c1ab|version=1
```
- Topics, in this order: stack, runtime, deps, build, test, lint, ci, layout,
  conventions, env, absent.
- `sha` is the first 7 characters of `git hash-object <file>`, so a fact goes
  stale when its file changes.
- Never a secret or an env value.

**`.stealth/procedures.md`**: each chosen Procedure, with its steps under it:
```
PROCEDURE|P-1|<procedure_id>|v3|docx-js|goal=Create a DOCX document
STEP|P-1:2|action|Validate the file|locator=https://...|needs=runtime=python|check=-
```

**`.stealth/run.md`**: the plan, one line per unit of work:
```
NODE|N-3|ready|Configure page size|step=P-1:3|claims=R-001..R-004|deps=N-2|check=node build.js && test -s out.docx
```
Status is one of ready, blocked, running, done, failed, skipped.

**The one-line packet to an executor:**
```
Do node N-3. Read: rg "N-3" .stealth/run.md, then the claims and step lines it names.
```

A "node" is just a line in `run.md`: the planner's own to-do item. It has no
server object and no database row.

---

## Access and privacy

- Reads are free. An anonymous caller gets a read-only public token. A blank
  `Authorization: Bearer` counts as no header.
- `report_discovery` needs a real signed-in user. The claim is `private`, owned
  by that user, and linked to the Procedure step.
- `visibility` (public / private / org) is the only access control.
  `scope_type` just says what a claim is about.
- The server never reads or writes the user's filesystem.

---

## What was removed, and why

The server used to compile and run plans. That was only needed when the server
ran the work itself, or shared one plan across machines. In v1 the agent does
both locally, so these are deleted (git keeps the history):

- **Code:** `goal_compiler.py`, `goal_execution.py`, `goal_verification.py`,
  `goal_cost.py`, `cost_math.py`, `stealth/artifacts.py`,
  `mcp_server/goal_run_page.py`.
- **MCP tools:** `compile_goal`, `execute_goal`, `estimate_goal_cost`,
  `get_goal_run_status`, `list_goal_artifacts`, `get_goal_artifact`.
- **Routes and formats:** the `/goal-run` viewer routes and the `goal_run.md`
  format.
- **`find_ways` parameters:** `execute` and `workspace_root`.

**Kept:**
- `goal_resolution.py` (`find_ways` uses it) and `step_binding.py` /
  `procedure_graph.py` (the shape of a step or Procedure; REST and ingestion use them).
- The old durable run path (`plans.py`, `plan_persistence.py`, `durable_run.py`,
  `durable_resume.py`, `graph_executor.py`). The REST runs API and
  `local_agent/runner.py` still use it. It isn't part of v1, and removing it is
  a later cut, together with its website pages.

---

## Still open

- **Duplicate Goals:** "docx-js library" and "'docx' library" are the same npm
  package, so no repo fact can separate them, and search stays ambiguous.
  They need merging.
- **Server keep-alive:** a restart that checks itself, plus a health check. Today
  the local MCP server can die silently.
- **Unowned public writes:** `init_workspace`-style raw writes land public
  with no owner. They aren't exposed in v1; fix them before any repo-fact sync.
- **Sharing, verification and credits for discoveries:** v2.
- **Status docs:** `proj_status.md` / `demo.md` don't describe this design yet.
