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

- [x] **Auth and policy for launch — sign-in, private-by-default procedures, publish to the Global Commons, data export/delete, audit trail.**
  — _security-hardening resume / Claude Sonnet 5 — branch `gate-2b`, tag `authpolicy-verified-a610645`._
  What works now:
  - People sign in with Supabase (email + password, or Google). The backend
    checks the sign-in token itself. It never trusts a user id sent in a
    request body.
  - A signed-in person can add a procedure fast (`POST /v1/procedures` and
    `/from_text`, and the `/submit` "Quick add" form). It is private by default;
    only its owner can see it.
  - There is one "publish to the Global Commons" operation. It checks who you
    are, walks the procedure's dependencies, blocks secrets and private paths,
    sanitises, and creates a fresh public candidate. Your private run history
    and evidence stay private. A publication record and audit events are written.
  - Every model or embedding call is checked against a policy table first.
    Private text does not leave to an outside provider unless the policy allows
    that data class. External providers are allowed public data only; the
    self-hosted provider is allowed private data.
  - "Export my data" and "Delete my data" both work. Deletion follows
    dependencies (physical delete vs tombstone), respects legal hold, and keeps
    Global Commons knowledge.
  - Every sensitive change writes an audit row (`audit_events`).
  - Frontend: `/auth` + `/auth/callback`, session restore and refresh, scope
    labels (PRIVATE / ORGANIZATION / GLOBAL CANDIDATE / GLOBAL VERIFIED),
    publication-consequences panel, `/me/privacy` page.
  Commits: `3e47906` `e63ba85` `120a506` `7d1e8fa` `6cea7fc` `7ec8c51` `939921a`
  `f18651a` `a8d25dc` `4ee89d5` `b2265e9` `7f2fbb8` `d9fd731` (+ docs on `gate-2b`,
  not pushed). Migrations 46 (`model_provider_policies`, `publication_records`,
  `data_requests`) and 47 (`procedures.tenant_id`) are applied to the live DB.
  Tests: full offline suite **2330 pass / 18 fail / 314 skip** — the 18 fails are
  old and belong to the ingestion tests (`fake_embeddings.py` patches a method
  that never existed; `test_migration_upgrade_e2e` migration-order bug). Zero new
  fails from this work. Adversarial database tests (cross-user, cross-org,
  publish, IDOR, RLS) run against a throwaway local Postgres + pgvector: **75 pass
  / 0 fail**, plus a live gate smoke.
  Hosted repository execution (Phase 3) is **out of scope for this launch** and
  stays off (`HOSTED_EXECUTION_ENABLED=false`).
  **Not merged to `main` yet.** The auth/policy commits are tangled with the
  retrieval commits in shared files (`skill_ingestion.py`, `embeddings.py`,
  `procedures.py`, the procedure detail page), so they cannot be cherry-picked
  out cleanly. The whole `gate-2b` stack goes to `main` in one step once the
  retrieval team's release gate passes. Full detail:
  `docs/launch_compliance_impl/FINAL-RELEASE-READINESS.md`.

- [x] **Retrieval representation + measured relevance gate + human-facing display + frontend UX**
  — _core-b / Claude Sonnet 5 — branch `gate-2b`, commits `9223b54`..`b50b094`._
  One canonical deterministic procedure retrieval document (`procdoc_v1`:
  name/purpose/when-to-use/steps/tools/deps/domain/constraints/failure-conditions),
  versioned per row alongside the embedding; migrations 44 + 45; all 2478 live
  procedures re-embedded from it (local `mxbai-embed-large` — paid Gemini + Voyage
  quotas both ran dry; one coherent space, 0 failures) with `display_name` /
  `display_description` on every row. A **measured** relevance gate: 57-query /
  855-candidate labelled eval set, cutoff swept and selected by rule
  (`RELEVANCE_GATE_MIN_SIMILARITY = 0.6839`, not guessed). Old vs new representation,
  same model: precision **0.47 → 0.65** at the optimal gate; no-match queries return
  zero. Procedure lexical retrieval leg added over the canonical document. Frontend:
  search card + procedure/solution detail pages lead with what/why/verified/evidence;
  raw "Match 82%" removed. Private-procedure access filter closed a pre-existing gap in
  `_fetch_candidate_pool`. Backend offline suite 2270 pass / 18 fail (merge baseline
  2227 / 27 — +43 passing, −9 failing, zero new); frontend `tsc` + `next build` green.
  Full evidence: `.scratch/retrieval-representation-FINAL-REPORT.md`.

- [x] **Cut Supabase egress (was 7 GB against a 0.382 GB database).**
  — _core-b / Claude Sonnet 5 — branch `gate-2b`, commits `09f77af`, `2062aba` (not pushed)._
  The search code was doing `SELECT * FROM procedures`, which pulls the big
  embedding vector (about 15 KB of text per row) on every query, for up to 200
  rows per search, even though nothing reads it. Added a fixed column list that
  leaves out the three heavy columns and used it at 5 query spots. Search
  results are the same. Also added a `TEST_DATABASE_URL` setting so the
  database tests can run against a local throwaway Postgres instead of the real
  Supabase one. 176 retrieval tests pass (1 unrelated failure that was already
  there).

- [x] **Retrieval release-closure measurement pass.**
  — _core-b / Claude Sonnet 5 — branch `gate-2b`, committed earlier this workstream._
  Ran a controlled embedding-model benchmark (local vs Gemini vs Voyage on the
  same procedures, queries and labels): Gemini wins on ranking quality, but
  using it needs a paid Google plan, so the corpus stays on the local model for
  now. Built a 28-query abstention test set: genuine "no match" returns nothing
  22/22; found and measured a real gap where "same tool, wrong environment"
  queries still leak (7 of ~12). Tried LLM regeneration of 568 flagged display
  names — only 7 passed an independent judge because those rows are test
  fixtures with no real content, so the rest keep their plain deterministic
  names. Full write-up: `.scratch/retrieval-release-closure/FINAL-REPORT.md`.
  Release gate for this workstream: **NOT PASS** (see PAUSED / HANDOFF).

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

- [x] **Tidied the V1 web app and fixed the sign-in header.**
  — _frontend integration / Claude Sonnet 5 — branch `gate-2b` (two styling commits landed on `main`)._
  - The background is now a light warm off-white. The top bar sticks to the top
    of the page and blurs whatever scrolls under it.
  - Removed the "Procedures" and "Tasks" links from the top bar, and deleted
    their two near-empty landing pages. You still open a procedure or a task from
    search or from a problem page. Commits `ce8700c`, `b8e133e` (on `main`).
  - Fixed a bug: the "Sign in" button stayed on screen after you had signed in.
    Added a small piece that reads the sign-in state and shows your name and a
    "Sign out" button instead. Commit `4abf2f0` (on `gate-2b`).

- [x] **Built the people layer — opt-in public profiles, people search, contributor leaderboard.**
  — _frontend integration / Claude Sonnet 5 — branch `gate-2b`, not pushed._
  The system already recorded who made each procedure and claim, but there was
  no way to see a person or look one up. This adds that.
  - New: an opt-in public profile. It is OFF by default. If you turn it on (from
    `/me/privacy`), your name and your contribution counts become public. You are
    told exactly what becomes public before you choose, and you can switch it
    back to private at any time. Turning it on is recorded with a timestamp, and
    every change writes an audit row.
  - New backend: a `contributor_profiles` table (migration 48), `GET`/`PUT
    /v1/me/profile`, and a public read surface — `GET /v1/contributors/search`,
    `/v1/contributors/leaderboard`, `/v1/contributors/{id}`. A private or missing
    profile returns "not found", so it never leaks that an account exists.
  - The counts (procedures authored, verified procedures, claims, Commons
    publications) are worked out live from the existing owner columns each time.
    Nothing is stored as a running total.
  - "Export my data" now includes the profile and its disclosure state. "Delete
    my data" removes the public listing.
  - New pages: `/people` (search by name), `/contributors/[id]` (one public
    profile), `/leaderboard` (rank contributors, switch the metric). The top bar
    gains "Leaderboard" and "People".
  - Commits: backend `5c6b2f1`, frontend `f43f6de`, plan note `a610645`.
    Migration 48 is applied to the live DB. New backend offline tests: 12 pass
    (`test_contributor_profiles_offline.py`); the data-rights test file stays
    green. Frontend `tsc` and `next build` both pass (20 routes).
  - Known limit: profile counts cover only 3 of the ~18 tables that record an
    owner. A fuller "count everything a person made" pass is still open.

---

## IN PROGRESS (someone is working on this right now)

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

_(security-hardening / auth+policy: moved to DONE — see the top of the DONE list.)_

---

## PAUSED / HANDOFF (started, not finished — read before picking up)
## PAUSED / HANDOFF (started, not finished — read before picking up)

- **Retrieval release gate — NOT PASS.**
  — _core-b / Claude Sonnet 5 — 2026-09-09, branch `gate-2b`._
  The retrieval representation, relevance gate, display surfaces, frontend and
  access-control ordering are done and measurably better. The gate does not
  pass because these items need a person, not more code:
  1. **Pick and pay for a production embedding model.** Gemini is the measured
     winner but its free tier is exhausted; needs a paid Google plan. Until
     then the corpus stays on the local model. (`CHITANYA-SETUP.md`)
  2. **Re-embed all 2478 procedures** in whichever model is chosen, then
     **re-derive the relevance threshold** against the full corpus. Both are
     scripted (`backfill_procedure_embeddings.py --representation`,
     `eval_retrieval_quality.py --measure`); they just need step 1 first.
  3. **Human-label the 91-pair sample** (`label-validation-sample.jsonl`). An
     independent model agrees with the eval labels only ~77% on the
     relevant/not-relevant call, so absolute precision numbers are not yet
     release-grade.
  4. **Decide the "wrong environment" gap.** The relevance gate matches topic,
     not compatibility, so "run the web test suite against a native iOS app"
     still returns the web procedure. Either accept it with a downstream
     "retrieved is not the same as safe to reuse" rule, or add real
     preconditions / a compatibility check.
  5. **Small cleanups:** record embedding spend in `llm_spend`; pass the
     data-classification gate from the re-embed script; register a pgvector
     binary codec in `db/session.py` to shrink egress further.
  Full detail and exact commands: `.scratch/retrieval-release-closure/FINAL-REPORT.md` §22.

- **Deploy the V1 web app (`frontendv1`) to Vercel — done look-only; real backend still to wire.**
  — _frontend integration / Claude Sonnet 5 — 2026-09-09, branch `gate-2b`._
  Deployed the current `frontendv1` working tree to the Vercel project
  `bestprocedures` under team **`stealth13`** (which already owned the domain).
  - Live, public, no login gate: **https://bestprocedures.vercel.app**
    Build passed, 20 routes.
  - This pre-existing project had its framework preset set to "Other", so the
    first deploy failed on a missing `public/` dir. Fixed by adding
    `frontendv1/vercel.json` with `"framework": "nextjs"` (committed).
  - Production env vars set on the `stealth13` project:
    `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY` are the real
    values, so Supabase sign-in works. `NEXT_PUBLIC_API_URL` is
    `http://localhost:8000` on purpose — so search, problems, people,
    leaderboard and profile pages error / show empty on the live site until a
    real backend exists. Accepted trade-off.
  - Left to do for a working site: (1) stand up / tunnel the FastAPI backend,
    then `vercel env rm NEXT_PUBLIC_API_URL production` + re-add the real URL +
    `vercel deploy --prod` from `frontendv1/` (CLI must be on scope `stealth13`);
    (2) add `https://bestprocedures.vercel.app/auth/callback` to the Supabase
    project's Auth redirect URLs or Google sign-in bounces.
  - Dead end left behind: an earlier deploy went to a `bestprocedures` project
    under a different team (`chaitanyas-projects-45a1182b`) at
    `https://bestprocedures-beta.vercel.app`. Harmless; delete it from that
    team's dashboard when convenient.
  - Deploys use the local working tree, not a git push.

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
