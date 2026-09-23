# final_thing.md — StealthLab v1

What we're building, in plain words, plus what v1 contains and what moves to v2.
Everything not listed under v1 is v2: the code stays, it's just not part of v1.

---

## The idea in one paragraph

An agent working in your repo asks StealthLab "what's the best known way to do
this?" StealthLab returns a step-by-step plan built from proven Procedures,
picked using facts about *your* repo. The agent does the work. Whatever it
learns along the way — a fix, a missing step, a better way — it reports back, so
the next person gets the improved version.

---

## The whole thing, in simple words

### Setup (once per repo)
1. You connect the agent to StealthLab. Reading needs no login; contributing does.
2. The agent looks around your repo and writes down plain facts in
   `.stealth/claims.md`: "Node 20", "uses docx@9", "tests run with `pnpm test`".
   Each fact points at the file line it came from, and facts are grouped by
   topic so related ones sit next to each other.

### Doing a task
3. You ask for something: "add a DOCX export."
4. The **planner** (the main agent) calls `find_ways` and sends your repo facts along.
5. StealthLab finds the best-matching Goal and Procedure. Your repo facts pick
   the right one and rule out ones that can't work here. It returns a
   step-by-step plan.
6. The planner saves the plan into `.stealth/`: one line per step saying what to
   do, which facts matter, and how to check it worked. It shows you the plan.
7. For each ready step, the planner sends a small **executor** agent a one-line
   pointer. The executor greps the few lines it needs, does the work, and reports
   back: result, proof (diff, test output), and anything it learned.
8. The planner checks the proof. Pass → step done. Fail → retry, try another
   way, or ask you.

### Learning from it
9. If an executor had to fix something or found a better way, the planner:
   - updates the plan right away for the remaining steps,
   - adds new repo facts to `claims.md`,
   - reports anything useful beyond your repo with `report_discovery`
     (private to you in v1).
10. Next time that Procedure is used, your discoveries come back with it,
    through the `stealth://procedures/{id}/claims` resource.

### What you see
Connect → the agent learns your repo → you ask → you approve a plan → you watch
it work and answer questions only where needed → you get the result plus
"here's what we learned."

---

## The tiny-packet rule

The planner never writes executors a long prompt. It sends one line:

```
Do node N-3. Read: rg "N-3" .stealth/run.md
```

That line in `run.md` says everything else:

```
NODE|N-3|ready|Configure page size|step=P-7:3|claims=R-001..R-004|deps=N-2|check=node build.js && test -s out.docx
```

- `claims.md` is grouped by topic, so `R-001..R-004` is one contiguous block:
  one read or one `rg`.
- `procedures.md` keeps each Procedure's steps under its header, so `P-7:3` and
  its neighbours sit together.
- Every record is one greppable line.

Repo fact format (the existing `claims.md` grammar):

```
CLAIM|R-001|current|stack|repository|Node 20.11|source=.nvmrc:1#sha=9f2c|version=1
CLAIM|R-002|current|deps|repository|Uses docx@9.1 for Word output|source=package.json:23#sha=a41b|version=1
```

Rules for repo facts: every fact cites file, line, and content hash (stale when
the file changes, never guessed). They're private and scoped to the repository
by default. For this repo they override global claims.

---

## MCP v1 surface

**Two tools:**

| Tool | What it does | Access |
|---|---|---|
| `find_ways(query, ...)` | Fuzzy query → Goal → Procedure → DAG plan. `execute=False` returns just the plan and needs no token. `execute=True` also runs it, and needs a token. | read (plan) / exec (run) |
| `report_discovery(kind, procedure_id, problem, solution, step_order?, proof?, repo?)` | Records what was learned as a **private candidate claim** linked to the Procedure step. `kind` is one of fix, missing_step, precondition, better_way, correction, filled_gap. Proof text is redacted for secrets. | sign-in required |

**Three read-only resources** (claims only, returned as grep-friendly `CLAIM|…`
lines in contiguous blocks, scoped to the caller):

| Resource | Returns |
|---|---|
| `stealth://procedures/{procedure_id}/claims` | Discoveries recorded against it, then claims its preconditions cite, then claims related to its goal |
| `stealth://goals/{goal_id}/claims` | Claims related to the Goal |
| `stealth://claims/{claim_id}` | One claim with its evidence |

No prompts in v1.

**Switching:** `STEALTHLAB_MCP_SURFACE=v1` is the default. `STEALTHLAB_MCP_SURFACE=v2`
brings back the full legacy surface. v2 tools are still importable Python
functions; they just aren't registered on MCP in v1.

---

## Still to build for v1

- [x] v1 surface gating (2 tools + claim resources, no prompts)
- [x] `report_discovery` (private candidate claim, redacted, linked to step)
- [x] related-claims resources (grouped, caller-scoped, never unrestricted)
- [ ] **Repo survey:** agent prompt/skill that writes `.stealth/claims.md` from
      repo files, grouped by topic, with `source=file:line#sha`
- [x] **`find_ways` takes repo claims** (`repo_claims` = the claims.md text,
      ≤200 facts / 64 KB, request-scoped, never stored). Goal step: when search
      is ambiguous, repo fit (token overlap) breaks the tie only if one Goal
      fits clearly better, using the same 0.12 margin; otherwise it stays
      ambiguous. Procedure step: 5–20 most related facts per feasible Procedure
      go to the existing NLI/JEV judge chain. Procedures whose REQUIRED
      condition is contradicted (≥0.75) are dropped, the rest are re-ordered by
      verdict, and the chosen one carries `repo_fit` with supporting/blocking
      fact ids. Judge down → `not_checked`, plan still returned. Live-verified:
      JEV judged a real procedure APPLICABLE citing R-001/R-003/R-004.
- [ ] **Feed step requirements to the judge:** today the judge sees only
      written preconditions, not what a step's binding needs (e.g.
      `runtime: python`). Live run: "no Python toolchain" did not block a
      Python-script procedure because it had no written precondition.
- [ ] **Merge duplicate Goals:** "docx-js library" and "'docx' library" are the
      same npm package, but they're two Goals, so no repo fact can separate
      them and search stays ambiguous.
- [ ] **Richer plan output:** step description, source locator, success check,
      and claim ids per node
- [ ] **Goal path writes the local pages:** `goals.md`, `procedures.md`,
      `claims.md` (grouped), and a `run.md`-style node list for the chosen plan
- [ ] **Planner skill for Claude Code:** plan → dispatch tiny packets to
      subagents → verify with the check → revise plan → `report_discovery`
- [ ] Fix `init_workspace`-style raw writes before any repo facts are synced
      (they land public with no owner)
- [ ] Update `proj_status.md` / `demo.md` to this definition of the product

---

## Moved to v2 (check later)

### Tools, grouped (all still in code; exposed with `STEALTHLAB_MCP_SURFACE=v2`)

- **Old Procedure-first path:** `find_best_way`, `reproduce_procedure`,
  `search_procedures`, `get_procedure`, `check_procedure`,
  `check_applicability`, `retrieve_precedent`, `submit_procedure`,
  `decide_procedure`, `report_execution`
- **Split-up Goal tools (folded into `find_ways`):** `resolve_intent`,
  `search_goals`, `inspect_goal`, `list_goal_procedures`, `create_goal`,
  `explain_goal_route`, `compile_goal`, `execute_goal`, `estimate_goal_cost`,
  `get_goal_run_status`, `list_goal_artifacts`, `get_goal_artifact`
- **Durable multi-agent runs (need the old path's run rows):** `continue_run`,
  `inspect_run`, `resume_execution_run`, `retry_run_node`,
  `report_node_progress`, `record_run_update`, `declare_file_intent`,
  `verify_completion`, `generate_review_packet`, `get_route_decision`
- **`.stealth/` mechanics:** `init_workspace`, `project_knowledge`,
  `open_exploration`, `close_exploration`, `preview_local_sync`,
  `commit_local_sync`, `record_stealth_edit`, `list_stealth_edits`,
  `unsync_local_project`
- **Claim graph:** `get_claim_graph`, `get_relevant_claims` (claims now come
  through the v1 resources)
- **Trace ingestion:** `ingest_trajectory`, `inspect_trajectory`,
  `list_trajectory_events`, `run_semantic_extraction`, `inspect_extraction`,
  `list_extraction_objects`, `inspect_trajectory_provenance`,
  `reextract_trajectory`

### Resources and prompts
- Resources: `stealth://procedures/{id}`, `stealth://problems/{id}`,
  `stealth://problems/{id}/solutions`, `stealth://evaluations/{id}`,
  `stealth://runs/{id}`
- All six orchestration prompts (they name v2 tools)

### Ideas deferred to v2
- **Sharing discoveries publicly:** one publish path with scrub, screening,
  and license checks. Today there are two inconsistent paths (`publish_procedure`
  vs economy submissions), claims can't be published, and Goals have no gate.
- **Verification of shared discoveries:** the server reruns the submitted proof,
  a reviewer approves, or other people repeat the success.
- **Credits for discoveries:** paid on verification, mostly from reuse by
  others; no pay for self-use or duplicates; reversed if suppressed.
- **Multi-machine / multi-agent runs:** shared leases, handoff, database run
  records.
- **Server-side execution of step bindings** (sandbox, screened source artifacts).
- **Trace ingestion from `.claude/` and other harness folders.**
- **Folding `semantic_scope` into `scope_type`**, and pruning `scope_type` to
  global / organization / repository / user.
- **Problem / Solution / Evaluation over MCP** (the website uses REST for this).

### Known issues that v1's smaller surface sidesteps (still to fix in the v2 code)
- `project_knowledge` reads private data with no visibility check, and was
  anonymously callable. It's not exposed in v1.
- `init_workspace` writes repo facts to the public commons with no owner.
  Not exposed in v1.
- Local file paths embedded in public Source rows (exploration, local sync).
- Private rows written under the shared token have no owner.
- `step_execution_telemetry` has no visibility or owner.
