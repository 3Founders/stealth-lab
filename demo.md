# demo.md — The Minimal Shippable Product (v0.1 "earned memory" slice)

This file defines the **smallest end-to-end slice of StealthLab that may ship to
production**, and the exact evidence that proves each claim before release. If a
claim below lacks its proving command passing on the release commit, the slice
does not ship. Companion docs: `commLLM.md` (positioning), `demo-fixture` story
via `backend/scripts/bootstrap_demo.py`, `ROADMAP.md` (band plan).

---

## 1 · What ships

A local-first MCP server that gives one coding agent earned memory:

> ingest the agent's traces → distill evidence-backed procedures → surface them
> on similar tasks → **refuse stale reuse with cited reasons** (audit mode).

Five capabilities constitute the whole product for v0.1. Nothing else is
advertised; everything else is non-goals (§4).

| # | Capability | Real code path | Proving evidence |
|---|---|---|---|
| C1 | Install & boot | `docker compose up -d` (postgres must be **`pgvector/pgvector:pg15`** — plain postgres fails migration 01) + migrations auto-run | `python scripts/migrate.py --status` → all 30 `applied`; first verified engine run 2026-08-25 (`research/claude-code-hooks-v2@429ffa9`) |
| C2 | Traces flow in | Claude Code hooks → `app/services/trace_collector.py` (redaction choke point, dedup, bounded) → `trace_worker.py` → `agent_traces`/`trace_events`; HTTP path: POST `/v1/traces` per-record fault isolation | offline suite green; collector roundtrip test; worker quarantine behavior tests |
| C3 | Procedures get distilled | `procedure_extraction/` registry → strategies → derive (load-bearing preconditions) → validators (**V6 authoring-time invariant check**) → `procedures` table born-correct | suite incl. extraction validators; nothing enters without scope+provenance (`services/v0_gate.py`) |
| C4 | Reuse you can see | `mcp_server/server.py` tool `retrieve_precedent` (direct cosine-similarity match over task_nodes/knowledge_nodes AND, as of 2026-08-27, `procedures` — real `1 - (embedding <=> ...)` pgvector HNSW query, not RRF/graph expansion; see Doc-accuracy note) returns similarity-ranked name/id/similarity — a `procedures` match is always a VERIFIED, approved one | retrieval leave-one-out sanity pattern (n=400, p=.0066 [T-24]) |
| C5 | Refusal with receipts | `check_procedure` → `ALLOW` / `WOULD_REFUSE` citing the superseded claim id (+ changeset id, where one is tracked). **Audit mode only**: the agent is informed, not blocked; every refusal lands in the audit log | precondition-gate adversarial tests (fail-closed cascade); refusal payload shape pinned by tests, incl. new offline tests against the real server tool |

**Doc-accuracy note (updated 2026-08-27):** the gaps flagged by the same-day
outside-eye pass (previously recorded here, full detail in
`.scratch/research/outside-eye-demo-readme-pass.md`) are now closed.
`check_procedure` landed as a real tool (#9) in
`backend/app/mcp_server/server.py`, a thin wrapper reusing the existing
decision logic in `app/services/applicability.py` rather than reinventing
it. `docker-compose.yml` / `backend/Dockerfile` exist, have been boot-tested
clean on real Docker (zero fixes needed), and the 30-migration chain applies
clean on a genuinely fresh disposable DB — confirmed twice, on two separate
fresh volumes. `check_procedure`'s `evidence` field always cites the
superseded claim id, but only cites a changeset id when one exists — v1
never scoped ChangeSet coverage to knowledge-node supersession (a
pre-existing, disclosed limitation), so the two-item evidence array in §3's
pinned contract example is the changeset-backed case; §3 now also carries a
real engine-verified example of the claim-id-only case.

**C4 fix (2026-08-27):** `retrieve_precedent` previously could not return a
`procedures` row at all — its candidate set (`reuse_detection.
_vector_candidates`) only ever queried `task_nodes`/`knowledge_nodes`, even
though the embedding column, HNSW index, and a real vector-similarity query
over `procedures` already existed elsewhere in this codebase
(`applicability.py`'s `find_applicable_procedures`). This was bootstrap_demo.
py's own disclosed "HONEST FINDING" (Question #7). Fixed by fusing in
`applicability.verified_procedure_candidates()`, which reuses the SAME
cold-start gate (`should_disable_procedure_retrieval`) and the SAME
verified+approved rule `check_hard_constraints` enforces everywhere else —
**not** a new, looser path. The row above's older "hybrid RRF + graph
expansion" description was also simply wrong for this tool (that's
`HybridRetriever`, a different code path); corrected to describe what
`retrieve_precedent` actually runs.

The claim is still not "any procedure, plus confidence and a provenance
chain" — worded precisely now: a `procedures` match here is always
VERIFIED and approved, ranked by raw similarity (no "confidence" score, no
provenance chain — those are name/id/similarity only, same shape as the
task_nodes/knowledge_nodes candidates it's fused with). A freshly-extracted
(candidate) procedure — exactly what bootstrap_demo.py's Phase A produces —
still will NOT show up via `retrieve_precedent`, by design (2026-08-27
founder ruling: surfacing unverified procedures here was considered and
rejected as a false-reuse risk). `check_procedure` remains the only path
for reasoning about one specific, not-yet-verified procedure.

**C2 entry-point note (2026-08-28):** the runnable entry point behind the C2
row above is `backend/scripts/run_ingestion.py` (`--once` or `--interval N`)
— it reads collector `.jsonl` files and drives them through
`process_collector_file()`/`process_pending_jobs()` into `trace_events` and
`observations`, free of charge (no model calls). It does not go further on
its own: §3's reuse-demonstration checklist item below has the
engine-verified measurement (episode assembly bypassed entirely, break is at
observation → claim). Quickstart mentions for this script were missing from
`README.md`, `README_MCP_SERVER.md`, and this file until now — added
alongside this note (2026-08-28), closing the gap that same checklist item
flagged.

One install-flow gap found running this for real, now fixed: `docker
compose up -d` after a `git pull` used to silently keep running the OLD
image unless you added `--build`. The canonical `docker compose up`
instructions, in `docker-compose.yml`'s header comment, now always say
`--build`, with a note explaining why (2026-08-27).

## 2 · Production posture (non-negotiable at ship)

1. **Loopback-first**: server binds `127.0.0.1`; bearer token is authentication,
   not authorization — documented in SECURITY.md threat model.
2. **Redaction default-on**: `trace_redaction.py` is the single choke point;
   no raw-prompt persistence flag exists in v0.1.
3. **Raw write primitive stays gated**: `apply_change_set` requires explicit
   opt-in env flag; never advertised in tool listings by default.
4. **Fresh-start honesty**: no backfills, no legacy shims — a fresh install sees
   exactly what the schema births (verified by the engine run above).
5. **Apache-2.0** + plain-language data statement (local-first, opt-in telemetry
   only, never train on user traces).

## 3 · Ship checklist (all must be true on the release commit)

- [x] Full offline suite green: `cd backend && python -m pytest tests -q`
      (last: **1368 passed / 115 skipped / 0 failed**, 2026-08-27)
- [x] Migration chain applied clean on a throwaway
      `pgvector/pgvector:pg15` container via `scripts/migrate.py`
      (engine-verified — 30/30 applied, 0 pending, 0 errors, confirmed on
      two separate fresh volumes, 2026-08-27)
- [x] `python scripts/bootstrap_demo.py` runs the scripted two-phase story:
      phase A produces a real procedure with real preconditions derived
      from `project_state()`; phase B genuinely invalidates the claim
      behind one precondition and `check_procedure` flips ALLOW ->
      WOULD_REFUSE citing it. Engine-verified on a fresh DB, 2026-08-27 —
      independently checked against the raw rows (the `SUPERSEDES` edge
      and both claims' `truth_state`), not just the script's own output.
- [x] Audit log shows the refusal line with claim ids (`cl_*` → `cl_*`) —
      real example below, from the same run
- [x] `check_procedure` response shape matches the pinned contract, pinned by
      tests against the real server tool (2026-08-27). Contract example
      (illustrative, the changeset-backed evidence case):

```json
{ "verdict": "WOULD_REFUSE",
  "procedure": "proc_pagination_v1",
  "reason": "precondition claim cl_17 'pydantic v1 compatible' superseded by cl_23",
  "evidence": ["changeset_09", "execution_41"],
  "capability_note": "0 failures recorded, environment changed" }
```

  Real engine-verified example (2026-08-27, claim-id-only case — the more
  common shape in practice, per the doc-accuracy note above):

```
WOULD_REFUSE cf7b2b55-0bdf-4f33-8eb6-f007b5842c00: precondition claim
6942ee6f-22ac-435c-923d-2763a2d0282c (subject='project:bootstrap-demo-project'
predicate='has_test_runner' object='pytest') superseded by
cefb6b7e-e6f0-4e31-883b-ce04da8e0fd0
evidence: ['claim:6942ee6f-22ac-435c-923d-2763a2d0282c',
           'claim:cefb6b7e-e6f0-4e31-883b-ce04da8e0fd0']
```

- [x] README quickstart, **connection/infrastructure half** — clean clone →
      `docker compose up -d --build` → 30/30 migrations applied → MCP server
      added → real client connected. Engine-verified 2026-08-28 on a fresh
      volume, with this Claude Code instance as the real MCP client (not a
      stub): README_MCP_SERVER.md's own auth checks pass (unauthenticated
      `POST /mcp` → 401, authenticated → 200), `claude mcp list` reports
      Connected over Streamable HTTP, and 9 tools resolve — matching
      `server.py`'s `@server.tool()` list. Two caveats found in the doing:
      **`--build` is load-bearing and was missing from C1** (without it
      `docker compose up -d` silently reuses a stale image and yields a
      false pass), and README_MCP_SERVER.md's "The 7 tools" table is stale
      (missing `check_procedure`, `decide_decomposition`). **Fixed
      2026-08-28**: table now lists all 9, and the `apply_change_set` vs.
      `submit_approval` section grew a second case for `decompose_task` →
      `decide_decomposition` (the doc previously told callers to apply
      `decompose_task`'s output via `apply_change_set`, which `server.py`'s
      own `decompose_task`/`decide_decomposition` docstrings already say is
      wrong — `apply_change_set`'s own docstring in `server.py` still says
      the opposite and has not been corrected).
- [ ] README quickstart, **reuse-demonstration half** — one task solved
      twice, second citing precedent. OPEN, and structurally so rather
      than merely untried: on a fresh install ordinary agent tool use
      creates no procedure, so `check_procedure` has no `procedure_id` to
      receive and `retrieve_precedent` correctly returns "No precedent
      found". Measured 2026-08-28 by solving two real tasks through normal
      tool use, then running the real ingestion chain
      (`scripts/run_ingestion.py`, 3811 collector records, 3313 jobs) over
      this session's own 1.1MB hook trace. The chain reaches
      `agent_traces` 13 → `trace_events` 3813 → `observations` 2697, then
      stops: `episodes` 0, `knowledge_nodes` 0, `claim_sources` 0,
      `procedures` 0, `evidence` 0. The break is at
      **observation → claim**, with episodes bypassed entirely by this
      path (`run_ingestion` never calls `assemble_episodes`), and
      `extract_procedure()` still reachable only via `find_best_way`
      (`server.py:719`, its sole live caller). Closing this needs either a
      live model call or a documented zero-cost path.
      **Partial credit, cite rather than reading this as fully red:**
      `bootstrap_demo.py` already covers the *decision* half of C4 "reuse
      you can see" on a fresh DB — a real extracted procedure, real
      derived preconditions, and ALLOW → WOULD_REFUSE citing real claim
      ids. What it does not cover is precedent arriving from the agent's
      *own prior work*, which is what this row is for.
      Also undocumented regardless (**since fixed 2026-08-28**, see the C2
      entry-point note above): `run_ingestion.py` used to appear zero times
      in `README.md`, `README_MCP_SERVER.md`, or this file, so nobody
      following the quickstart would have run it; all three now document it.
- [x] SECURITY.md + data statement published; uninstall = drop volume
      (`SECURITY.md` + `DATA_STATEMENT.md`, repo root, 2026-08-27)

## 4 · Explicit non-goals for v0.1 (do not claim, do not demo)

- Blocking enforcement of refusals (audit mode only until Band 3)
- `explain_failure` / `explain_decision` receipts UI (depends on execution-graph
  backward tracing — lands with Band 1.7 consumers; beta-flag only if wired)
- Hosted anything; multi-tenant; team scopes beyond single-user local
- Crypto-shredded deletion (Band 5, D4-ratified design), capability-based
  auto-routing thresholds (Band 1.9b)
- Multi-client conformance matrix beyond Claude Code + one OpenAI-compatible client

## 5 · Why this is the right minimum

Every element maps to a differentiator nobody else ships — failure → belief
revision → capability decay → cited refusal — while every element excluded is
either unbuilt (honesty rule §0: never ship a claim without its proving test)
or table stakes competitors already have. The slice demonstrates the closed
loop end-to-end with zero staged output: same graph, same tools, same audit log
a real user gets.
