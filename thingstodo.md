# thingstodo.md — shared work log for every agent

This file is the single place where every agent (human or AI) writes down what
they are doing. It exists so that many agents can work at the same time without
stepping on each other or repeating work.

## The rules (read before you touch anything)

1. **Before you start a task**, add a line for it under "IN PROGRESS" below.
   Write your agent name, the date, what you are about to do, and which files or
   areas you will touch. Do this *first*, before any code change.
2. **While you work**, keep your line updated if the plan changes.
3. **When you finish**, move your line from "IN PROGRESS" to "DONE" and put a
   `[x]` in front of it. Add one plain sentence about what actually changed and
   the commit hash if there is one.
4. **If you stop without finishing**, move the line to "PAUSED / HANDOFF" and
   write what is left and anything the next agent needs to know.
5. **Never delete** someone else's line. Only add or move your own.
6. Keep the language simple. Short sentences. No jargon that a new reader would
   not understand.
7. If two agents want the same task, the one who wrote their line first keeps it.
   The other picks something else.

---

## DONE (already finished this session)

- [x] **Fixed the leaderboard "stale solution" bug (Bug #7).** The product
  leaderboard used to rank fixes that were built on out-of-date knowledge. Now it
  hides those, marks them `STALE`, and keeps their old numbers only for history.
  Pushed to `main`.
- [x] **Fixed the privacy bug on benchmark and evaluation reads (Bug #8).** Before,
  anyone could read a private problem's benchmarks and evaluations. Now those
  reads check the same permission as the problem itself. The MCP server now uses
  the real caller identity, never full-access-bypass. Pushed to `main` and tagged
  `v1-final-2026-09-03.2`.
- [x] **Built the claim-graph web view.** Turned it on. It shows claims and how
  they relate, plus similarity links. Reachable at `/claim-graph`.
- [x] **Built the procedure and task-node web view.** New page at
  `/procedure-graph`. Shows procedures as circles (colored by how verified they
  are, red ring if stale) and task nodes as blue squares, with edges for
  versions, breakdown into tasks, and sub-procedure links. Connected to the live
  backend. Pushed to `main`.
- [x] **Fixed 4 problems from `problems.md` (A, B, C, D):**
  - A: cleaned up 3 broken "verified" procedures that pointed at missing
    sub-procedures. Wrote a repair script.
  - B: added a missing database column (`candidates.no_action_justified`) with a
    new migration file and applied it to the live database.
  - C: the MCP procedure tools now accept both kinds of procedure id (the stable
    handle and the raw table row key).
  - D: added real seed data — 3 procedures (2 fully verified) and 2 runnable
    implementations — so the registry tools return real results.
  - All 4 pushed to `main`.
- [x] **Wrote down the state of the big 60-source run.** File:
  `.scratch/autonomous_run_state.md`. It records the git state, the database
  state, what features already exist, and the decisions made for this run.
- [x] **Resolved all 60 source seeds.** File:
  `.scratch/better_ways_corpus/source_resolution/seeds.jsonl` plus a `README.md`.
  Result: 21 sources have a confirmed real artifact, 15 more are probably real,
  the rest are article-only, guessed, or could not be found. The 60 seeds
  collapse into 9 "families" (groups that are really the same procedure). Pushed
  to `main`.
- [x] **Checked the claim-graph `KeyError: 'relation'` bug.** It was already
  fixed earlier. Confirmed by running the tests (11 passed).

---

## IN PROGRESS (someone is working on this right now)

- [ ] **Retrieval representation + relevance gate + human-facing display metadata + frontendv1 search UX**
  — _core-b / Claude Sonnet 5 — started 2026-09-08, branch `gate-2b`._
  Building one canonical deterministic procedure retrieval document (name +
  goal + applicability + steps + tools + deps + domain + constraints +
  failure conditions), versioning it and the embedding, re-embedding the
  ~2400 live procedures from it, adding a measured (not guessed) relevance
  gate from a labelled eval set, and adding `display_name` /
  `display_description` with a backfill. Frontend: search card, procedure
  and solution detail pages stop showing raw "Match 82%" and lead with
  capability / applicability / verification / evidence.
  Files: `backend/app/services/{retrieval_document,embeddings,skill_ingestion,
  procedures,applicability,domain_search,solution_search,retrieval}.py`,
  new `backend/db/44_*.sql`, new backfill under `backend/scripts/`, new eval
  suite under `backend/tests/`, `frontendv1/src/app/search/page.tsx`,
  `frontendv1/src/app/procedures/[id]/page.tsx`,
  `frontendv1/src/app/solutions/[id]/page.tsx`,
  `frontendv1/src/lib/api/{types,client}.ts`,
  `backend/app/api/{search,solutions,procedures}.py`.
  Coordinated with "frontend integration stealth-lab" (owns auth only) and
  "evidence-tracking-temporal-reasoning" (no overlap; migration 44 is mine).

- [ ] **MCP evaluation harness** — _core-a / Claude Sonnet 5 — started 2026-09-08._
  Building the end-to-end test that goes through the real MCP interface:
  search a procedure → retrieve it → inspect it → plan → execute → check the
  result with plain rules → then check it again with a separate LLM judge →
  write an evaluation → search again. The LLM judge must not be the same model
  that produced the answer. Will reuse the existing LLM setup in
  `backend/app/debate/panel.py` (do not build a second one). New code goes under
  `backend/app/eval/` or `backend/scripts/`, with unit tests.
  Files likely touched: new harness module + new test file. Will push to `main`
  when it passes.

---

## TO DO (not started — pick one, add your line to IN PROGRESS first)

- [ ] **Representative end-to-end run for each of the 9 families.** Take at least
  one real source per family and run it all the way through the real MCP surface
  (ingest → implementation → execute → verify with rules → verify with LLM judge
  → evaluation → admit or reject). Save one artifact file per source under
  `.scratch/better_ways_corpus/evaluations/`. Depends on the MCP evaluation
  harness above being done first.
- [ ] **Composite score: baseline vs Stealth.** Build the comparison that shows,
  across the representative sources, how the plain baseline does versus going
  through Stealth. One combined benchmark.
- [ ] **Final report + `run_state.json`.** Write an honest summary of the whole
  run: which sources were admitted, which are promising, which lack evidence,
  which were rejected, which were blocked. No made-up results. Machine-readable
  `run_state.json` next to it.
- [ ] **Full regression gate.** Run the whole offline test suite and the
  packaging checks. Record pass/fail counts. Fix anything this run broke; do not
  touch pre-existing unrelated failures beyond noting them.

---

## IN PROGRESS

- **Cline (Phase 1 security boundaries), 2026-09-08.** Implementing Phase 1 of the
  launch-compliance work (Supabase Auth + hosted repo authorization + audit_events).
  Full detail of what I am doing:
  - **Task:** Phase 1 of STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1. Phase 0 audit was
    approved; decisions 1–7 from the founder are implemented.
  - **What exists so far (all uncommitted, verified intact in the working tree):**
    - `backend/app/services/audit.py` (NEW) — one centralized audit event writer,
      to be reused by publication/withdrawal/export/authz transitions. No ad-hoc
      audit systems.
    - `backend/app/services/workspace_registry.py` (NEW) — hosted repository
      authorization boundary: caller-supplied `repo_path` is no longer the
      authorization mechanism in hosted mode; workspace identity resolves
      server-side against `registered_workspaces`; local/loopback mode keeps
      `repo_path` (documented, unchanged behavior).
    - `backend/db/41_phase1_security_boundaries.sql` (NEW) — creates
      `audit_events` + `registered_workspaces`, adds `org` to the
      `visibility_level` enum. NOT yet applied to any database.
    - `backend/app/config.py` (MOD) — Supabase Auth config (issuer
      `{url}/auth/v1`, JWKS derivation, ES256/RS256 only, never HS256),
      hosted-mode flags, boot-posture guards for half-configured Supabase.
    - `backend/app/services/authn.py` (MOD) — Supabase preset in
      `OidcConfig.from_settings`; user_id derived from verified tokens, never
      client-supplied fields.
    - `backend/app/services/access.py` (MOD) — `org` visibility in the ONE
      centralized predicate builder (existing public/commons rows untouched;
      new USER_PRIVATE/ORG_PRIVATE enforce real ownership/membership).
    - `backend/app/mcp_server/server.py` (MOD) — hosted-mode guard wired into the
      two repo-executing MCP tools (`find_best_way`, `reproduce_procedure`).
    - `backend/tests/test_phase1_security_boundaries_offline.py` (NEW) —
      impersonation, tenant isolation, workspace authz, Supabase verifier,
      migration assertions. All 56 pass.
  - **Test status:** my new suite is green (56/56). Full offline suite:
    2208 passed / 27 failed / 301 skipped. **Triage result: all 27 failures are
    NOT from my work.** They trace to (a) `Embedder._embed_via_chain` /
    `embedding_model_id` / `embedding_provider` changes and (b) migrations
    39/40/42/43 — both belong to the parallel ingestion work-stream (commits
    284c9ef, bc2d4c9, c624f8f). I did not touch `embeddings.py`, `procedures.py`,
    or those migrations.
  - **Heads-up for other agents:** mid-session the branch switched from `main`
    to `gate-2b` (both at c624f8f now) — someone is moving branches while I
    work. My changes are in the working tree on `gate-2b`. A safety stash
    `stash@{0} (On main: phase1-verify)` holds a copy of my edits; do not drop it.
  - **Next steps:** commit Phase 1 with test counts in the message, then STOP
    (no migration applied, no Phase 2+ work, per founder instruction).
  - Files I will still touch: only the ones listed above + `thingstodo.md`.

- **security-hardening resume / Claude Sonnet 5 — started 2026-09-08, branch `gate-2b`.**
  Completing "Supabase Auth + Launch Security" (STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1).
  Phase 0 reconstruction audit is done and written to
  `docs/launch_compliance_implementation_ledger.md` — read that for the full
  phase 0–8 state. Now doing **Phase 1 (authentication)** only, then stopping to
  notify the founder.
  Files I will touch for Phase 1:
  - BACKEND: `backend/app/api/deps.py` (add ONE central `require_authenticated_user`
    dependency + wire org-membership resolution into `get_scope`),
    `backend/app/main.py` (boot-posture: account for the Supabase preset +
    `hosted_execution_enabled`), `backend/app/services/authn.py` (surface a typed
    request principal; no rewrite of the validator), new
    `backend/tests/test_supabase_auth_*` , apply migration
    `backend/db/41_phase1_security_boundaries.sql` (already committed, additive,
    idempotent — never applied).
  - FRONTEND (`frontendv1/`): `src/lib/auth.ts`, `src/app/auth/page.tsx`, new
    `src/lib/supabase/*`, `package.json` (+`@supabase/supabase-js`,
    `@supabase/ssr`), `.env.local.example`; a 1-line merge into
    `src/lib/api/client.ts` (auth-header source).
  COORDINATION: `frontend integration stealth-lab` is listed as "auth only"
  owner — could not reach them via agent messaging; if that agent is active on
  frontend auth, this line yields the frontend half to them and I take backend
  only. `retrieval representation frontend` (core-b) owns
  `frontendv1/src/lib/api/{client,types}.ts` — my touch there is additive
  (header source), will rebase around their changes.

---

## PAUSED / HANDOFF (started, not finished — read before picking up)
## PAUSED / HANDOFF (started, not finished — read before picking up)

_(nothing here yet)_

---

## Prompts to give other agents

Copy one of these into a new agent. Fill in the `<...>` parts.

### Prompt A — generic worker

> You are joining a shared task run. **First, open `thingstodo.md` at the repo
> root and read all of it, especially "The rules".** Then:
>
> 1. Pick the single task `<TASK NAME>` from the "TO DO" section (or the specific
>    task I name here).
> 2. Before you write any code, add a line under "IN PROGRESS" in
>    `thingstodo.md`: your agent name, today's date, one sentence on what you are
>    about to do, and the files or areas you will touch. Save the file.
> 3. Do the task. Keep your line updated if your plan changes.
> 4. When done: move your line to "DONE", put `[x]` in front of it, add one plain
>    sentence about what changed and the commit hash.
> 5. If you have to stop early: move your line to "PAUSED / HANDOFF" and write
>    exactly what is left and what the next agent needs to know.
>
> Rules: never delete another agent's line. Only add or move your own. Keep the
> writing simple. Commit with the prefix `core-a:` and include the pytest
> pass/skip/fail counts in the commit message. Re-fetch and rebase onto
> `origin/main` before pushing; fast-forward only, never force-push.

### Prompt B — end-to-end family run (needs the MCP harness done first)

> Read `thingstodo.md` at the repo root first, all of it.
>
> Your task: take the family `<FAMILY NAME>` from
> `.scratch/better_ways_corpus/source_resolution/README.md`, pick its best real
> source, and run it fully through the **real MCP interface** (not the Python
> functions directly): search → retrieve → inspect → plan → execute → check with
> plain rules → check again with the independent LLM judge → write the
> evaluation. Save one artifact file for this source under
> `.scratch/better_ways_corpus/evaluations/`.
>
> Before you start: add your line under "IN PROGRESS" in `thingstodo.md`
> (agent name, date, which family, which files). When done: move it to "DONE"
> with `[x]`, one sentence, and the commit hash. If a source is genuinely weak,
> mark it `INSUFFICIENT_EVIDENCE` or `REJECT` honestly — do not invent a passing
> result. Never conflate a setup/infrastructure failure with the source being
> bad.
>
> Follow the same commit and push rules as everyone else (`core-a:` prefix,
> pytest counts in the message, rebase on `origin/main`, fast-forward only).

### Prompt C — reviewer / regression gate

> Read `thingstodo.md` at the repo root first.
>
> Your task: run the full offline test suite and the packaging checks. Record the
> pass / skip / fail counts. Compare against the last known-good counts in the
> recent commit messages. If this run introduced a new failure, find and fix the
> cause (do not weaken the test). Pre-existing unrelated failures: just list
> them, do not fix them here.
>
> Add your line under "IN PROGRESS" before you begin. Move it to "DONE" with the
> final counts when finished.
