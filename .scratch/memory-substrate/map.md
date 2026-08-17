# Map: Memory Substrate

## Destination

A locked architecture specification for milestone 1 — a general experiential + procedural memory substrate, with Claude Code agentic-trace ingestion as its first domain on top — grounded in the existing `backend/` code, with representation, schema, migration strategy and implementation order settled well enough that implementation can begin with no major unknowns.

## Notes

**Source of requirements:** [spec.md](../../spec.md) at the repo root. Read it before resolving any ticket.

**Domain:** Python 3.13, FastAPI, asyncpg, pgvector. Raw SQL migrations in `backend/db/`, no ORM, no Alembic. Pydantic models are hand-written mirrors of the SQL, bridged by explicit `from_row()`.

**Every session calls:** the `grilling` and `domain-modeling` skills. Research tickets call `research`; prototype tickets call `prototype`.

**Standing preferences for this effort:**

- Cite exact existing files/classes/functions in every resolution. Do not invent existing functionality.
- Preserve existing data and IDs; prefer compatibility links over rewrites.
- Do not rename an existing concept merely because a greenfield design would name it differently.
- Keep these distinct — never collapse into one generic "memory node": event · observation · episode · claim · state · procedure · procedure execution · trace · evidence · agent · task node · HTN/DAG · motif.
- Provenance and justification are canonical; confidence is derived from evidence, never the other way round.
- Prefer deterministic local processing over LLM calls. The synchronous agent execution path must never depend on the memory compiler.

**Scoping decisions already made** (2026-08-17, with the repo owner):

1. **Substrate first, then Claude Code on top.** Whether the SWE-bench experiment survives is a question to ask *after* implementation — it is not a driver.
2. **The HTN/DAG engine moves into `backend/`**, and its structural flaws get fixed as part of the move — not just relocated.
3. **Auth/isolation is a discussion, not a default.** Supabase and WorkOS are explicitly on the table.

**The map plans; it does not build.** Tickets resolve decisions. Ticket 01 is the one exception — it produces the inventory document the other tickets cite.

## Decisions so far

<!-- one line per closed ticket: gist of the answer, then the link -->

- [Architecture inventory](issues/01-architecture-inventory.md) — full inventory written to
  [inventory.md](inventory.md); key surprises: `claims.py` already implements a working
  claim/truth-state/supersession primitive on top of `knowledge_nodes` (not a greenfield
  gap), procedure representation is structurally HTN-shaped today (`method_library.py`
  tags decompositions onto `task_nodes`), and `hierarchy.py`/`retrieval.py` already cover
  most of the target locality/retrieval hierarchy. Four concrete defects confirmed
  (`embedding_joint` ghost column, `public_generated` enum/model drift, pool
  double-management bug, undocumented migrations 06–10).
- [Claude Code hook schema](issues/07-claude-code-hook-schema.md) — full findings written to
  [research/claude-code-hook-schema.md](research/claude-code-hook-schema.md); spec.md's 13
  named hook events are all real (not speculative), but the official surface is actually 31
  events, omitting whole families spec.md never scoped (`Notification`, `ConfigChange`,
  `FileChanged`, `Elicitation*`, etc.); hooks are documented as best-effort delivery only, with
  no retry/crash guarantees and non-deterministic ordering, directly confirming spec.md's
  instinct that memory correctness must not depend on hooks firing perfectly; the JSONL
  transcript's per-line schema is explicitly undocumented and stated to change between
  releases, so its completeness relative to hooks is an open empirical question, not a
  documented fact; no schema-version field exists anywhere in hooks or the transcript.
- [OpenTelemetry GenAI semantic conventions](issues/08-otel-genai-semconv.md) — full findings
  written to [research/08-otel-genai-semconv-findings.md](research/08-otel-genai-semconv-findings.md);
  key surprise: the GenAI conventions were split out of the main semantic-conventions repo
  into an untagged, pre-1.0 `semantic-conventions-genai` repo in June 2026, making this
  repo's `OTEL_SPEC_VERSION = "1.30.0-experimental"` pin and `OTEL_ACTION_MAP` key strings
  both stale/mismatched; coverage is bimodal — LLM-call/tool-call fields (tool id/name/
  input/output, model invocation, token counts, error info) are well covered, but
  Identity/Intent/Environment fields (episode_id, actor_id, user_goal, cwd, repo/branch/
  commit, permission state, provenance) have no semconv equivalent at all, and semconv's
  span parent-child nesting does not model this repo's episode/trace/event grouping;
  evidence points to a hybrid derive-for-LLM-shaped-fields/diverge-for-session-shaped-fields
  outcome, left for ticket 06 to decide.
- [Canonical trace model](issues/06-canonical-trace-model.md) — three new/existing tables, not
  a rework of `traces`: a new `trace_events` table (atomic events) and a new trace-header table
  sit alongside the untouched `traces` (kept exactly as-is for its real consumers,
  `triggers.py`/`layer2.py`); `episodes` becomes the real episode-assembly target. Trace is a
  grouping *below* episode (one turn/subagent run), not identical to it — spec.md's separate
  `trace_id`/`episode_id` fields and ticket 08's `gen_ai.conversation.id` findings both point
  the same way. Hybrid OTel-semconv adoption per ticket 08's own recommendation: derive for
  LLM/tool-call-shaped fields, diverge (own namespace) for Identity/Intent/Environment fields.
  Existing `task_node_id` FK on `traces` untouched; the new tables simply don't carry it.
- [Isolation and auth posture](issues/09-isolation-and-auth.md) — stay local-first,
  single/few-trusted-owner for milestone 1; do not adopt Supabase Auth or WorkOS yet, consistent
  with `mcp_server/server.py`'s own already-shipped single-tenant, loopback-only posture. Every
  new table gets `owner_id` from row one and every new query goes through `access.py` — the
  `tenant_id` cautionary tale, not repeated. Project isolation is its own axis (a `project_id`
  column), not folded into episode. Blanket encryption is already covered by Supabase's platform
  defaults; field-level encryption for redaction-adjacent cases is cross-referenced to ticket 18,
  not decided here. If a provider is adopted later, identity only — `access.py` stays the sole
  authorization authority, never Supabase RLS.
- [Migration mechanism and data migration](issues/17-migration-mechanism.md) — keep raw SQL, add
  a `schema_migrations` ledger + one documented runner script + a CI test diffing code against
  schema (the two known drifts, `embedding_joint` and missing `public_generated`, were both
  schema-vs-code consistency failures a test would have caught — not something a migration
  framework's revision tracking addresses, and Alembic's real value needs SQLAlchemy models this
  stack doesn't have). Seed data split out of the DDL sequence. No migration needed for
  `episodes`/`traces` (inherited from ticket 06, untouched), `edges`, or `knowledge_nodes`'
  existing node types — new shapes arrive alongside old ones. `claim`-type rows blocked on
  ticket 03's still-open answer, not invented here. Method-library rows becoming procedures get a
  fresh UUID plus an explicit link edge, never a reused id. SWE-bench corpus stays exactly where
  it is, untouched — out of scope per this map's own locked decision.

## Not yet specified

- **Evidence as a first-class thing.** No evidence table exists today; citations are resolved at query time and never persisted. Whether evidence becomes its own record, an edge kind, or a property depends on how claim/observation/procedure representation land.
- **Provenance/justification graph shape** — how "why do we believe this" is actually queried, and what makes it TMS-ready without being a TMS. Sharpens once claim and observation representations are fixed.
- **Procedure execution records** — the shape of what every reuse writes back (procedure version, bound parameters, initial state, concrete plan, deviations, cost). Depends on the procedure model and the HTN relocation together.
- **The reuse feedback loop** — how execution outcomes update reliability and scope without auto-rewriting a verified procedure after one failure.
- **Revalidation harness** — spec.md wants it internal-only initially, exposed as a test harness rather than an autonomous process.
- **API surface** — the concrete routes. spec.md sketches paths but says integrate with existing conventions (`/v1/...`, 8 existing routers).
- **Testing strategy** for the new layers — and what to do about the untested core that already exists (`retrieval.py`'s RRF arithmetic has zero coverage; `graph_store.traverse_from` is untested and marked NOT LOAD TESTED in-source).
- **Motif layer** — spec.md wants it eventually, non-executable, hypothesis-only, with supporting episodes and contradiction counts. Too far past the frontier to phrase sharply.

## Out of scope

- **The SWE-bench Pro experiment and its A/B/C/D arms** — the owner's call: build the substrate and the Claude Code layer, then revisit whether the benchmark is still wanted. Returns as a fresh effort if so, not as a resumption of this map.
- **RDF/OWL or any heavyweight ontology** — spec.md, explicit.
- **A complete TMS** — spec.md, explicit. Design so future retraction is possible; do not build propagation.
- **Mobile / personal-device state** — spec.md, explicit. The state model must merely not preclude it; that constraint lives in ticket 10.
- **Executable motifs** — motifs stay hypotheses; spec.md forbids making abstract motifs directly executable initially.