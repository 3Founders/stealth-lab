# STEALTHLAB RETRIEVAL / EMBEDDING — CURRENT STATE

_Read-only investigation. No code, DB, or commits modified. Branch `gate-2b`,
HEAD `f58d95c`, 2026-09-09. Measured against the live Supabase corpus._

---

## Executive Verdict

**What information is actually embedded for a procedure today?**

One deterministic text block per live procedure version — the *canonical
retrieval document* (`retrieval_document.py::build_procedure_retrieval_document`,
version `procdoc_v1`), stored in `procedures.retrieval_document` and turned into
`procedures.embedding` (1024-dim). It has up to 9 labelled sections:
`Name`, `Purpose`, `When to use`, `Domain`, `Steps`, `Tools`, `Depends on`,
`Constraints`, `Fails when` — each emitted only when the row has content for it.

**Measured over the 2478 live procedures (99.9% are bulk-ingested skills):**

| section | % of corpus that has it |
|---|---|
| Name | 100.0% |
| Purpose | 100.0% |
| Steps | 99.1% |
| Depends on | 7.7% |
| Constraints | 3.0% |
| When to use | 2.7% |
| Tools | 1.3% |
| Domain | 0.7% |
| Fails when | 0.04% (1 row) |

So in practice the embedded text for ~90%+ of procedures is **Name + Purpose +
Steps**, and nothing else.

**Is the current embedding representation sufficient to capture where / how /
why a procedure is useful?**

## PARTIALLY.

- **HOW** — yes. Steps are embedded for 99% of rows. This is the one dimension
  that is well covered. The earlier fear ("rich source → only name + short
  description → embedding") is **not** occurring: 0 rows are Name+Purpose only.
- **WHAT / WHY** — partially. `Purpose` is the model-abstracted
  `capability_statement` (or the raw `goal`) — a one-line "what it does". No
  separate rationale/mechanism field is embedded.
- **WHEN (applicability)** — no, effectively. Only 2.7% of rows carry any "When
  to use" text; structured `preconditions` exist on ~41 of 2478 rows.
- **WHEN NOT (exclusions)** — no. `scope`/`exclusions` are not rendered into
  the document at all (see Phase 2). 0% coverage.
- **FAILURE modes** — no. 1 row in the entire corpus.
- **TOOLS / DEPENDENCIES** — mostly no (1.3% / 7.7%), though the builder *does*
  render them when present.
- **CONTEXT ("where it was demonstrated useful")** — no. Not in the document,
  and (Phase 8) barely reconstructable from other tables for this corpus.

The representation *design* can carry all of this — the builder has a section
for nearly every dimension. The **data feeding it doesn't exist** on the current
corpus, because the dominant ingestion path (`skill_ingestion`) hard-codes
`preconditions=[]`, `invariants=[]`, `postconditions=[]`,
`failure_conditions=[]` and never renders `scope`/`exclusions`.

---

## 1. Branch / Commit

| | |
|---|---|
| Repo | `github.com/3Founders/stealth-lab` (`origin`) |
| Branch investigated | `gate-2b` |
| HEAD | `f58d95c` — `board: thingstodo -- log frontend tidy-up …` |
| origin/main | `33d4c05` — `Merge branch 'gate-2b' into main` |
| Relationship | HEAD is **40 commits ahead of, 0 behind** origin/main. The foundational retrieval commits `9223b54` (canonical representation) and `122466d` (migration 44) are **already on `main`** via `33d4c05`. The egress fix `09f77af`, `TEST_DATABASE_URL` `2062aba`, and doc updates are `gate-2b`-only, unpushed (local `gate-2b` is 54 ahead / 1 behind `origin/gate-2b`). |
| Working tree | clean of tracked changes; only untracked local files (zips, keys, scratch). |
| Latest retrieval/embedding implementation | `gate-2b`. Key files: `app/services/retrieval_document.py`, `applicability.py`, `domain_search.py`, `relevance_gate.py`, `retrieval.py`, `skill_ingestion.py`, `scripts/backfill_procedure_embeddings.py`, `db/44_procedure_retrieval_representation.sql`. |

---

## 2. Actual Ingestion → Embedding Pipeline

There are **two** production ingestion paths that create procedures. Both
converge on the same retrieval-document builder and the same `Embedder`.

### Path A — skill/document ingestion (produced ~2475 / 2478 of the live corpus)

```
SKILL.md / skill package / repo doc
  │  app/services/skill_ingestion.py
  ▼
parse_skill_md(content)                         [skill_ingestion.py:113]
  → ParsedSkill{ name, description, steps[str],  [skill_ingestion.py:69-78]
                applies_when?, compatibility?,
                allowed_tools[], frontmatter, instructions(body) }
  ▼
_screen_untrusted_document(parsed)              [injection screen; fail-closed]
  ▼
_abstract_capability(client, parsed)            [skill_ingestion.py:804]
  → capability_statement            ← ONE General-Compute model call; the ONLY
                                      LLM step; abstains (None) if screened
  ▼
_parsed_skill_procedure_shape(parsed)           [skill_ingestion.py:282]
  → procedures-column dict:
      name, goal(=description), capability_statement, steps=[{order,goal}],
      domain, domain_payload{applies_when, tool_requirements, compatibility,
                             dependencies},
      preconditions=[], invariants=[], postconditions=[],
      failure_conditions=[]         ← HARD-CODED EMPTY
  ▼
build_skill_retrieval_document(...)             [skill_ingestion.py:318]
  → build_procedure_retrieval_document(shape)   [retrieval_document.py:302]
  → retrieval_doc  (text)
  ▼
Embedder().embed_one_with_metadata(retrieval_doc, input_type="document")
                                                [skill_ingestion.py:1157 / 492]
  → goal_vec (1024 floats) + EmbeddingMetadata{model_id, provider,
                                               input_type, text_sha256}
  ▼
capture_procedure(pool, name, goal, steps, domain_payload,
    embedding=goal_vec, embedding_model_id=…, embedding_provider=…,
    retrieval_document=retrieval_doc,
    retrieval_document_version=RETRIEVAL_DOCUMENT_VERSION,   ("procdoc_v1")
    retrieval_document_sha256=…,
    display_name=…, display_description=…, display_metadata_version=…)
                                                [procedures.py:~110; INSERT at :230]
  ▼
Postgres `procedures` row (Supabase + pgvector)
```

Sub-cases within Path A:
- `ingest_skill_md(embed=False)` — fast bulk import: writes the row with **no
  vector**, `retrieval_document_version = "import_pending_reembed"`; the
  `--representation` backfill embeds it later. (`skill_ingestion.py:441-518`)
- source revision detected → `supersede_procedure(...)` with the new
  `retrieval_document` + fresh vector (`skill_ingestion.py:1199`).
- worker/queue variant: `app/services/ingestion_jobs.py` claims jobs
  (`ingestion_jobs` table, `claimed_by`) and calls the same
  `compile_skill_artifact` path.

### Path B — organic extraction from traces (produced ~41 / 2478)

```
agent trace → episode → observations/claims
  │  app/api/ingest.py → trace_redaction.redact_event → storage
  ▼
app/services/procedure_extraction/  (registry.py, strategies.py, derive.py)
  derive_preconditions(pool, evidence)          [derive.py:92]  ← from project_state()
  derive_failure_conditions(evidence)           [derive.py:384]
  derive scope / postconditions
  ▼
capture_procedure(... preconditions=[…real…], failure_conditions=[…], …)
  ▼
build_procedure_retrieval_document runs over a row that DOES have
"When to use" (preconditions) and "Fails when" content
```

This is the minority path. It is the only one that populates the structured
applicability fields the retrieval document is designed to render.

### The `Embedder` (shared by both paths and by query time)

`app/services/embeddings.py::Embedder`
- `_configured_provider()` — `provider=` arg wins, else `USE_LOCAL_MODELS=true`
  forces `local`, else first of `EMBEDDING_PROVIDER_CHAIN` (default
  `"gemini,voyage"`). **Currently: `local:mxbai-embed-large`** (`.env` has
  `USE_LOCAL_MODELS=true`).
- `_embed_gemini` — `contents=list(texts)` sent **whole**, no truncation;
  `task_type=RETRIEVAL_DOCUMENT`/`RETRIEVAL_QUERY`; `output_dimensionality=1024`
  (MRL reduction of the *output vector*, not the input).
- cross-call in-memory cache keyed by `(model, dim, task_type, sha256(text))`.
- No `governance.record()` call — embedding spend is **not** in `llm_spend`.

---

## 3. Exact Current Embedding Representation

`build_procedure_retrieval_document` (`retrieval_document.py:302-357`) emits
`"\n".join(f"{label}: {body}")` over these sections **in this fixed order**,
skipping any with no content:

| # | Label | Source field(s) | Cap |
|---|---|---|---|
| 1 | `Name` | `display_name` or `name`, de-slugged | — |
| 2 | `Purpose` | `capability_statement` or `goal` or `name` | 1200 ch |
| 3 | `When to use` | `domain_payload.applies_when` + trigger-shaped `goal` + rendered `preconditions` | 1500 ch |
| 4 | `Domain` | `domain` | — |
| 5 | `Steps` | `steps[].goal/description/action/name/title/text`, de-duped, numbered | 40 steps / 4000 ch |
| 6 | `Tools` | `domain_payload.tool_requirements` + `tool_requirements`, sorted-unique | 40 |
| 7 | `Depends on` | resolved `procedure_dependencies` refs or `domain_payload.dependencies`, last path segment, de-slugged | 40 |
| 8 | `Constraints` | `invariants[].expr` + `postconditions` (`ensures …`) + `domain_payload.compatibility` | 1500 ch |
| 9 | `Fails when` | `failure_conditions[].when/description` — only if it names a triggering condition | 1200 ch |

Normalization (`_norm`, `retrieval_document.py:95-117`): NFC unicode, strip
markdown emphasis, drop control / `Cf Co Cs Cn So` categories, collapse
whitespace, trim. Every collection `sorted()` before render. No timestamps, no
`now()`, no randomness → byte-identical output for identical content.

### Real example — a typical corpus row (verbatim from the live DB)

```
Name: Defer tool-schema loading until needed
Purpose: Reduce fixed per-turn token cost of an agent's available tool surface
by deferring full tool-schema loading until a tool is actually needed
Steps: 1. Advertise only a tool's name (not its full JSON-Schema definition) in
the default system context for less-common tools 2. Fetch and load a tool's full
schema only when the agent actually decides to invoke it
```
(411 chars ≈ ~100 tokens. Name + Purpose + Steps. No When-to-use, Tools,
Constraints, Fails-when — this row has no data for them.)

### Real example — a richer row (has `Depends on`, longer `Steps`)

```
Name: Parallel-agent git-worktree isolation
Purpose: Run multiple coding agents concurrently on one repository without their
file/git operations colliding
Steps: 1. Before running N agents concurrently against the same repository,
create a separate linked git worktree for each agent (git worktree add) instead
of sharing one working directory 2. Have each agent checkout, edit, and commit
only within its own linked worktree
```
(425 chars ≈ ~105 tokens.)

Corpus doc length: **p50 543 ch, p90 2160 ch, max 5098 ch, min 67 ch**.

---

## 4. Current Field Coverage

`exists in DB` = the column/JSONB key exists in the schema.
`available to builder` = `build_procedure_retrieval_document` reads it.
`in embedding text` = actually rendered for a non-trivial fraction of the live
corpus (measured %).

| Procedure information | In embedding text? | Exact source | Notes |
|---|---|---|---|
| canonical name | ✅ 100% | `procedures.name` (de-slugged) | `Name:` line |
| display name | ✅ 100% | `procedures.display_name` (preferred over `name`) | `Name:` line |
| goal | ✅ (as fallback) | `procedures.goal` | used for `Purpose:` only if no `capability_statement` |
| description / capability | ✅ 100% | `procedures.capability_statement` (model-abstracted) or `goal` | `Purpose:` line |
| problem statement | ⚠️ implicit | folded into `Purpose` | no dedicated field |
| applicability / when to use | ❌ ~2.7% | `domain_payload.applies_when` + `preconditions` | builder renders it; data almost never present |
| when NOT to use | ❌ 0% | `procedures.scope` / `procedures.exclusions` | **builder does not read these at all** |
| prerequisites | ❌ ~2.7% | `preconditions` (via `_render_preconditions`) | ~41/2478 rows have structured preconditions |
| assumptions | ❌ ~0% | `required_state` | **builder does not read `required_state`** |
| constraints | ⚠️ 3.0% | `invariants` + `domain_payload.compatibility` | rendered when present; rare |
| invariants | ⚠️ 3.0% | `procedures.invariants[].expr` | `Constraints:` line |
| steps | ✅ 99.1% | `procedures.steps[]` | `Steps:` line — the one well-covered dimension |
| tools | ⚠️ 1.3% | `domain_payload.tool_requirements` / `tool_requirements` | rendered when present |
| dependencies | ⚠️ 7.7% | `procedure_dependencies` / `domain_payload.dependencies` | human-readable ref only, ids stripped |
| expected outcome | ❌ ~0% | `postconditions` / `expected_effects` | `postconditions` → `Constraints:` as `ensures …`; `expected_effects` **not read** |
| failure conditions | ❌ 0.04% | `procedures.failure_conditions[]` | `Fails when:` — 1 row in the whole corpus |
| failure modes | ❌ 0.04% | same | — |
| examples | ❌ never | (no field) | not captured, not embedded |
| claims | ❌ never | `knowledge_nodes` linked via `preconditions[].claim_id` | not joined into the document |
| provenance | ❌ excluded by design | `provenance`, `domain_payload.source` | explicitly excluded (`retrieval_document.py:29-38`) |
| source context | ❌ never | `ingested_artifacts.uri` etc. | not joined in |
| evidence summary | ❌ excluded by design | `evidence_refs`, `verification_stats` | explicitly excluded |
| execution history | ❌ excluded by design | `verification_stats` | explicitly excluded |
| verification state | ❌ not embedded (used as filter) | `procedures.verification_state` | applicability cascade only |
| staleness | ❌ not embedded (used as filter) | `procedures.staleness` | applicability cascade only |
| availability | ❌ not embedded (used as filter) | `procedures.availability` | applicability cascade only |
| IDs / timestamps / audit | ❌ excluded by design | `id`, `procedure_id`, `t_*`, `owner_id`, … | explicitly excluded (`retrieval_document.py:29-38`) |

---

## 5. Procedure Schema → Retrieval Representation

Base table `db/18_procedures.sql`; retrieval columns added by
`db/44_procedure_retrieval_representation.sql`.

| Concept | Schema field(s) that COULD represent it | Rendered into `procdoc_v1`? |
|---|---|---|
| WHAT (problem solved) | `goal`, `capability_statement` | ✅ `Purpose` |
| WHEN (use when) | `domain_payload.applies_when` (prose), `preconditions` (structured JSONB), `required_state` | ⚠️ `When to use` — reads `applies_when` + `preconditions`, **not** `required_state` |
| WHEN NOT (exclusions) | `scope` (JSONB), `exclusions` (JSONB) | ❌ **not read by the builder** |
| WHY (mechanism/rationale) | — no dedicated field. `capability_statement` sometimes implies it. `instructions` (full body) holds it for skills but is discarded. | ❌ |
| HOW (steps) | `steps` (JSONB array) | ✅ `Steps` |
| CONDITIONS (pre/assump/constr/inv) | `preconditions`, `required_state`, `invariants`, `postconditions` | ⚠️ `preconditions`→When, `invariants`+`postconditions`→Constraints; `required_state` **not read** |
| TOOLS | `domain_payload.tool_requirements`, `tool_requirements` | ✅ `Tools` (when present) |
| DEPENDENCIES | `procedure_dependencies` table, `domain_payload.dependencies` | ✅ `Depends on` (refs only) |
| OUTCOME (success) | `postconditions`, `expected_effects` | ⚠️ `postconditions`→Constraints; `expected_effects` **not read** |
| FAILURE | `failure_conditions` (JSONB) | ✅ `Fails when` (when it names a trigger) |
| EVIDENCE (where tested) | `evidence_refs`, `source_episode_ids`, `verification_stats`, `episode_evidence` rows | ❌ excluded by design |
| CONTEXT (env/tasks demonstrated) | `domain`, `domain_payload.compatibility`, `verification_stats.context_keys_seen` | ⚠️ `domain`→Domain (0.7%), `compatibility`→Constraints; `context_keys_seen` **not read** |
| PROVENANCE (origin) | `provenance`, `domain_payload.source`, `ingested_artifacts` | ❌ excluded by design |

**Schema gaps** (no field represents the concept at all):
- **WHY / mechanism / rationale** — no column. For skills the reasoning lives in
  `instructions` (the SKILL.md body), which is parsed but **not stored on the
  procedure and not embedded**.
- **Worked examples** — no field.
- **"Demonstrated-useful context"** as first-class data — only indirectly via
  `verification_stats.context_keys_seen` (opaque keys) and linked episodes.

---

## 6. Query → Retrieval Pipeline

`app/services/domain_search.py::search_global` → `_search_procedures` →
`applicability.py::find_applicable_procedures`.

```
user query string
  │  NO normalization — not lowercased, not rewritten, not expanded
  ▼
embedder.embed_one(query, input_type="query")          [domain_search.py:563]
  → query_vec (1024)   ·   task_type = RETRIEVAL_QUERY (gemini) / no prefix (local)
  │  raw query string ALSO passed as goal_text for the lexical leg
  ▼
find_applicable_procedures(goal_embedding=query_vec, goal_text=query, …)
  │  candidate_pool_size = 200
  ▼
_fetch_candidate_pool  — up to 3 ranked lists, each LIMIT 200:  [applicability.py:445]
   1. COST     ORDER BY jsonb_array_length(preconditions) ASC        (cheap-first)
   2. VECTOR   ORDER BY embedding <=> query_vec ASC                  (cosine on procdoc_v1 embedding)
   3. LEXICAL  to_tsvector('english', retrieval_document)            (full-text on procdoc_v1)
              @@ to_tsquery(<query words OR-joined>), ts_rank DESC
   → fuse_rrf(the lists)  → top 200 candidate ids                    [retrieval.py::fuse_rrf]
  ▼
for each candidate: check_hard_constraints (NON-COMPENSATORY cascade)  [applicability.py:234]
   temporal validity  →  (verification_state=='verified' & approval)   IF require_verified
   →  staleness  →  availability  →  scope/exclusions
   →  preconditions (via project_state, closed-world, fail-closed)
   →  invariants (z3)
   any violation ⇒ DISQUALIFIED (not a low score)
  ▼
survivors re-scored:  _similarity_score = 1 - (embedding <=> query_vec)   [applicability.py:733]
   fused again via fuse_rrf with capability ranking
  ▼
back in _search_procedures:
   scope_type / repository_id / project_id post-filter                 [domain_search.py:281]
  ▼
RELEVANCE GATE:  passes_relevance_gate(_similarity_score)              [domain_search.py:291]
   drop if similarity < RELEVANCE_GATE_MIN_SIMILARITY (0.6839)
   zero results is a valid answer; fails OPEN if cutoff is None
  ▼
presentation shaping: display_name, display_description,
   applicability_summary (build_applicability_summary),
   failure_modes (build_failure_modes),
   relevance_label / relevance_reason, evidence_summary, provenance, scope
  ▼
final result list
```

**What the query is compared against:** the **canonical `retrieval_document`** —
semantically via its `embedding` (cosine), lexically via its `tsvector`. Both
legs use the *same* full document (Name+Purpose+Steps+…), **not** `name` alone
and **not** `goal` alone.

**Retrieval uses, in order:** cost + semantic + lexical candidate generation →
RRF fusion → non-compensatory applicability cascade (temporal, verification,
approval, staleness, availability, scope/exclusions, preconditions, invariants)
→ similarity+capability RRF re-rank → scope post-filter → measured relevance
gate → presentation. `require_verified=False` for browse/search (ticket-13
exception); every other hard constraint still applies.

**`require_verified` cold-start:** if `require_verified=True` and too few
verified visible procedures exist, `should_disable_procedure_retrieval` returns
True and retrieval is disabled entirely (`applicability.py:73`).

---

## 7. "Where / How / Why Useful" Coverage

| Dimension | Captured in the embedded document today? | Detail |
|---|---|---|
| **HOW** | ✅ well | `Steps` present on 99.1% of rows |
| **WHAT** | ✅ | `Purpose` (abstracted capability) on 100% |
| **WHY** | ❌ | no schema field; SKILL.md body (`instructions`) parsed then discarded |
| **WHERE it applies** | ❌ ~2.7% | `When to use` rarely populated; `scope`/`exclusions` never rendered |
| **WHERE it must NOT apply** | ❌ 0% | builder ignores `scope`/`exclusions` |
| **CONDITIONS** | ❌ ~3% | `preconditions` on ~41 rows; `required_state` never rendered |
| **OUTCOME** | ❌ ~0% | `expected_effects` never rendered; `postconditions` rare |
| **FAILURE** | ❌ 0.04% | 1 row |
| **CONTEXT demonstrated useful** | ❌ | `domain` on 0.7%; episode/evidence context not joined into the doc (by design) |
| **TOOLS / DEPS** | ⚠️ 1.3% / 7.7% | rendered when present, usually absent |

**What is lost:** for the bulk-ingested corpus, everything except *what it does*
and *the steps*. A query like *"how do I keep a big MCP toolset from blowing the
context window — but I'm on a system with no lazy-loading support"* can match on
topic (Purpose+Steps) but the document contains nothing that says where this
technique does/doesn't apply, so the relevance gate (cosine only) cannot tell a
compatible match from an incompatible one. This is the measured
"wrong-environment" leak (FINAL-REPORT §11): 7 of ~12 such queries surface a
constraint-violating procedure above the gate.

---

## 8. Critical Question — "Where it was useful"

**Can we construct a meaningful "where/how/why this procedure was useful"
retrieval representation from existing data today?**

### PARTIALLY — and only for the ~41 organically-extracted rows, not the corpus.

What exists per source:

| signal | table.column | present for corpus (Path A) | present for extracted (Path B) |
|---|---|---|---|
| task type / problem type | `episodes` / `task_nodes` (via `source_episode_ids`) | ❌ (no source episode) | ✅ linked |
| environment / repo characteristics | `procedures.scope_type` + `scope_entity_id` (`global`/`entity`), `domain_payload.compatibility` | ⚠️ scope shard-key only; `compatibility` on 3% | ⚠️ same |
| tools used | `domain_payload.tool_requirements` | ⚠️ 1.3% | ⚠️ |
| dependencies | `procedure_dependencies` | ⚠️ 7.7% | ⚠️ |
| applicability conditions | `preconditions` (JSONB) | ❌ hard-coded `[]` | ✅ `derive_preconditions` from `project_state()` |
| successful use cases | `episode_evidence` / `evidence_refs` / `verification_stats` (`attempts`, `successes`, `distinct_contexts`, `context_keys_seen`) | ⚠️ counters only, mostly zero | ⚠️ counters |
| failure conditions | `failure_conditions` (JSONB) | ❌ hard-coded `[]` | ✅ `derive_failure_conditions` |
| where observed | `ingested_artifacts.uri` / `source_type` | ✅ but not joined into the doc | ✅ episode ids |

**Answer:**
- For the **bulk skill corpus (2475 rows):** effectively **NO**. The ingestion
  path stores no episode link, hard-codes the structured applicability fields
  empty, and `verification_stats` counters are near-zero (little execution
  history). The only "context" signals are `domain` (0.7%) and
  `compatibility` (3%).
- For the **extracted rows (~41):** **PARTIALLY**. `preconditions` +
  `failure_conditions` + linked `episodes` give a real "applied when X, failed
  when Y, observed in episode Z" story — but only `preconditions` and
  `failure_conditions` currently reach the retrieval document; the episode
  context does not.
- **No schema change needed to do better for Path B**; a real improvement for
  Path A needs either richer source parsing (keep `applies_when`, parse
  "when not to use" / "prerequisites" sections from the SKILL.md body) or an
  extraction step that populates the structured fields. Both are out of scope
  for this read-only pass.

---

## 9. What Must Be Excluded (and already is)

`retrieval_document.py:29-38` names the exclusions; all are enforced by the
builder never reading them:

| Field | Why excluded |
|---|---|
| `id`, `procedure_id`, `family_id` | random UUIDs — zero semantic signal; would break "same content → same text" |
| every `t_*` timestamp (`t_valid`, `t_invalid`, `t_created`, `t_expired`), `created_at`, `updated_at` | volatile; a re-embed on an unchanged procedure must produce an identical vector |
| `evidence_refs`, `source_episode_ids` | evidence identifiers, not capability description; change as evidence accrues |
| `verification_stats` (attempts/successes/counters/quarantine) | mutable operational telemetry; would make the vector drift on every execution |
| `embedding*` columns, `domain_payload['embedding']` | self-referential |
| `provenance`, `domain_payload['source']` | origin bookkeeping; not what the procedure *does* |
| `created_by`, `owner_id`, `approved_by` | identity/audit; also a privacy concern |
| `domain_payload['resource_manifest']` | file hashes/sizes — noise |
| `verification_state`, `staleness`, `availability`, `approval_status` | these are **filters**, not content — embedding them would let a "verified" query match on the word rather than on capability; the applicability cascade owns them |
| secrets / private paths | the canonical doc excludes bookkeeping by construction; `trace_redaction` covers the trace path (not this one) — a residual risk noted in CHITANYA-SETUP §6 |

The exclusion list is sound. The one gap on the *inclusion* side (not exclusion)
is that `scope`/`exclusions`/`required_state`/`expected_effects` are also not
read — those *should* be in, and aren't.

---

## 10. Versioning / Backfill

| Capability | Status | Implementation |
|---|---|---|
| retrieval-document version | ✅ | `RETRIEVAL_DOCUMENT_VERSION = "procdoc_v1"` (`retrieval_document.py:60`); stored per row in `procedures.retrieval_document_version` (`db/44`) |
| import sentinel | ✅ | `RETRIEVAL_DOCUMENT_IMPORT_VERSION = "import_pending_reembed"` for rows whose vector came from outside the recipe |
| content hash | ✅ | `procedures.retrieval_document_sha256` — backfill skips a row whose canonical text is unchanged |
| embedding model version | ✅ | `procedures.embedding_model_id` (e.g. `local:mxbai-embed-large`), `embedding_provider`, `embedding_dim`, `embedding_input_type`, `embedding_text_hash` (`db/42`) |
| deterministic reconstruction | ✅ | builder is pure: `sorted()` everything, no `now()`/random, fixed section order, unicode-normalized. Verified drift: **2/200 sampled shas** (FINAL-REPORT §18) — ~1%, flagged for a `--representation` re-run. |
| backfill support | ✅ | `scripts/backfill_procedure_embeddings.py` — `--representation` (re-embed rows off-version or content-changed), `--embed-missing`, `--display-metadata`; resumable, atomic per row, failures logged to `.backfill_state/procdoc_v1.failed.jsonl` and retried; keeps old vector on failure. `--force` re-does all rows (needed for a provider switch). |
| resumability index | ✅ | `idx_procedures_retrieval_document_pending` partial index (`db/44`) |
| embedding rate ledger | ⚠️ | `embedding_rate_windows` table + `logs/gemini_usage.jsonl`, but **not** `llm_spend` |

Versioning is the strongest part of the system.

---

## 11. Retrieval Evaluation Quality

`tests/data/retrieval_eval_v1.jsonl` — **57 queries**, each with ~15 labelled
candidates (855 labels total), labels `0/1/2/3` (irrelevant / related-not-useful
/ useful / excellent). `tests/data/retrieval_abstention_v1.jsonl` — 28 no-match
/ wrong-fit queries.

**Does the eval test "does this procedure solve the user's problem?" rather than
"does it have a similar name?"** — **Mostly yes, by construction.** The bucket
distribution:

| bucket | n | tests |
|---|---|---|
| `exact_match` | 15 | direct task match |
| `paraphrase` | 10 | same intent, reworded |
| `vocab_mismatch` | 7 | **different words, same task** — the anti-"name similarity" bucket |
| `no_match` | 7 | must return nothing |
| `technically_related_irrelevant` | 5 | shares tech, wrong task |
| `overlapping_terms_wrong_intent` | 5 | **shares words, wrong intent** — anti-lexical |
| `neighboring_domain` | 4 | adjacent domain |
| `generic` | 4 | vague query |
| abstention set | +28 | `completely_unrelated`, `wrong_domain`, `superficial_wording_diff_intent`, `same_tool_diff_objective`, `same_objective_incompatible_env`, `family_cousin_do_not_reuse` |

Coverage present: direct match, paraphrase, vocabulary mismatch, context-specific
(abstention `same_objective_incompatible_env`), wrong-intent, generic-vs-specific,
no-match, family-cousin. **Coverage thin/absent:** explicit "how/why" phrased
queries as their own bucket (some paraphrases are "how" shaped but it is not a
labelled category).

**What the "0.53 recall / 0.65 precision" numbers actually measure:** precision
and recall **against LLM-assigned labels**, at the swept optimal cosine cutoff,
on this 57-query set (or the 448-procedure bounded index for the model
benchmark). They measure "does the ranker put label≥2 candidates above the
threshold" where label≥2 was decided by Claude, cross-checked by `gpt-oss-120b`
with only **76.9% same-relevant-class agreement** (`label-validation.md`). So:
- They are **valid for relative comparisons** (old vs new representation: P
  0.47→0.65; model vs model — labels held constant).
- They are **not** a validated absolute quality claim, and **not** evidence that
  the embedding "understands" applicability or mechanism — the eval barely
  contains rows where applicability is the deciding factor, and the corpus
  documents barely contain applicability text to match on.
- `recall 0.53` in particular means: at the precision-favoring production
  threshold, ~half the candidates a labeller judged useful fall below the gate.
  It is a statement about the ranker + threshold on this label set, not proof
  of good embeddings.

Human-label validation of the 91-pair sample is the outstanding item that would
convert these to release-grade absolute numbers.

---

## 12. Exact Gaps (ranked)

### P0 — blocks the product thesis ("match because it understands the problem")

1. **Applicability is not embedded.** `When to use` present on **2.7%**,
   structured `preconditions` on ~41/2478, `scope`/`exclusions`
   **never rendered by the builder** (`retrieval_document.py` has no branch for
   `proc["scope"]` / `proc["exclusions"]`). Result: retrieval matches on topic
   only; the relevance gate (cosine) cannot separate a compatible match from an
   incompatible one. Measured: 7/~12 wrong-environment queries leak
   (`test_retrieval_abstention_e2e.py::test_incompatible_environment_gap_is_measured`,
   FINAL-REPORT §11).
2. **Ingestion Path A hard-codes the structured fields empty.**
   `skill_ingestion.py:311-315` and `:311` (`_parsed_skill_procedure_shape`)
   set `preconditions=[] invariants=[] postconditions=[] failure_conditions=[]`
   for every bulk-ingested skill. The builder can render them; the pipeline
   never provides them. This is why the corpus can't express where/when/why.
3. **"WHY / mechanism" has no home.** No schema column; the SKILL.md body
   (`ParsedSkill.instructions`) is parsed and then dropped — not stored on the
   procedure, not embedded. The product thesis explicitly wants "why it works".

### P1 — materially degrades quality but not thesis-fatal

4. **`required_state` and `expected_effects` are ignored by the builder** even
   though they are real columns that would feed `When to use` / `Expected
   outcome`.
5. **Failure modes: 1 row in the entire corpus.** `Fails when` is designed and
   working; there is no data.
6. **"Demonstrated-useful context" not represented.** `episode` links,
   `verification_stats.context_keys_seen`, `evidence` are excluded from the doc
   (correctly, as raw form) but no *summarised* context line replaces them.
7. **Eval set is thin on applicability-deciding rows and has no human labels.**
   57 queries, model labels, 77% independent agreement. Can't make an absolute
   claim; can't prove applicability understanding because the eval barely tests
   it.
8. **Corpus is 99.9% engineering fixtures.** All quality numbers describe search
   over bulk-imported skill docs, not a real end-user procedure corpus.

### P2 — cleanup / hygiene

9. **2/200 retrieval-document sha drift** (~1%) — a `--representation` re-run
   resolves it.
10. **Embedding spend not in `llm_spend`** — only `gemini_usage.jsonl` + the TPM
    bucket. `governance.record()` is never called on the embed path.
11. **`Domain` on 0.7%** — the field is almost always NULL for skill imports;
    `scope_type`/`scope_entity_id` carry the real shard key but aren't in the
    doc.
12. **Provider-policy gate is a no-op on this path** — `Embedder` is constructed
    without `data_classification`/`policy_pool` in `skill_ingestion` and the
    backfill, so `_enforce_provider_policy` does nothing (CHITANYA-SETUP §6).

---

## 13. Evidence Appendix

| Claim | Evidence |
|---|---|
| Canonical doc builder + sections + order + caps | `backend/app/services/retrieval_document.py:302-357` (`build_procedure_retrieval_document`); caps `:73-80`; normalization `:95-117`; exclusions doc `:29-38` |
| Version constant | `retrieval_document.py:60` `RETRIEVAL_DOCUMENT_VERSION = "procdoc_v1"`; import sentinel `:67` |
| Retrieval columns added | `backend/db/44_procedure_retrieval_representation.sql:36-42` (ALTER), `:47-52` GIN FTS index, `:58-62` pending partial index |
| Base procedure schema | `backend/db/18_procedures.sql:33-137` (`preconditions`, `required_state`, `expected_effects`, `postconditions`, `invariants`, `failure_conditions`, `scope`, `exclusions`, `steps`, `domain`, `domain_payload`, `verification_state`, `staleness`, `availability`) |
| `capability_statement` / `scope_type` etc. columns | `backend/db/21_band1_contracts.sql:27-28`; `db/42_worker_ingestion_integrity.sql:6-9` (`embedding_provider`, `embedding_input_type`, `embedding_text_hash`) |
| Path A ingestion + hard-coded empty structured fields | `backend/app/services/skill_ingestion.py:282-315` (`_parsed_skill_procedure_shape`), `:318-330` (`build_skill_retrieval_document`), `:388-518` (`ingest_skill_md`), `:1090-1319` (`compile_skill_artifact`); embed call `:1157` / `:492` |
| `ParsedSkill` fields (body discarded) | `skill_ingestion.py:69-78` (dataclass), `:113-212` (`parse_skill_md`); `instructions` set `:208`, never persisted on the procedure |
| Single model call for capability | `skill_ingestion.py:804` (`_abstract_capability`), `:1111-1113` |
| Path B structured derivation | `backend/app/services/procedure_extraction/derive.py:92` (`derive_preconditions`), `:384` (`derive_failure_conditions`) |
| `capture_procedure` INSERT with retrieval columns | `backend/app/services/procedures.py:110-233` (signature + INSERT), retrieval-doc params `:127-129`, sentinel logic `:189-207` |
| Embedder / provider selection | `backend/app/services/embeddings.py:206-210` (`_configured_provider`, override-first then `use_local_models`), `:326-393` (`_embed_gemini`, `contents=list(texts)` whole, `output_dimensionality` `:366`), cache key `:156-160` |
| Query pipeline | `backend/app/services/domain_search.py:518-593` (`search_global`), query embed `:563` `input_type="query"` no normalization, `:228-336` (`_search_procedures`), relevance gate `:291` |
| Candidate legs (cost/vector/lexical) + RRF | `backend/app/services/applicability.py:445-508` (`_fetch_candidate_pool`), vector `:482`/`:492` `embedding <=> $1::vector`, lexical `_PROC_LEXICAL_SQL` `:432-441` over `retrieval_document`, `fuse_rrf` `:506` |
| Non-compensatory cascade | `applicability.py:234-320` (`check_hard_constraints`), `:619-780` (`find_applicable_procedures`), similarity `:733` `1 - (embedding <=> $1::vector)` |
| Relevance gate constant | `backend/app/services/relevance_gate.py` `RELEVANCE_GATE_MIN_SIMILARITY = 0.6839`, `RELEVANCE_LABEL_STRONG_SIMILARITY = 0.7317`, `RELEVANCE_GATE_VERSION = "relgate_v1"` |
| Backfill script modes / resumability | `backend/scripts/backfill_procedure_embeddings.py:1-34` (doc), `:109-244` (`backfill_representation`), `_EMBED_BATCH=64` `:49`, `--force` `:125-129`, fail log `:78` / `:207-213`, argparse `:361-393` |
| Measured section coverage (2478 live rows) | live query, `.scratch/.../check_space.py` + `doc_coverage.py` (this session): Name/Purpose 100%, Steps 99.1%, When-to-use 2.7%, Domain 0.7%, Tools 1.3%, Depends-on 7.7%, Constraints 3.0%, Fails-when 0.04%; doc len p50 543 / p90 2160 / max 5098 ch; 99.9% `is_engineering_fixture` |
| Corpus on one space | live query: `('local:mxbai-embed-large','procdoc_v1', 2478)` — single row |
| Eval dataset shape | `backend/tests/data/retrieval_eval_v1.jsonl` — 57 queries, 8 buckets (exact_match 15 / paraphrase 10 / vocab_mismatch 7 / no_match 7 / technically_related_irrelevant 5 / overlapping_terms_wrong_intent 5 / neighboring_domain 4 / generic 4); `retrieval_abstention_v1.jsonl` — 28 across 6 categories |
| Independent-label agreement | `.scratch/retrieval-release-closure/label-validation.md` — 91 pairs, exact 50.5%, ±1 92.3%, same-class 76.9% |
| Wrong-environment leak measured | `backend/tests/test_retrieval_abstention_e2e.py::test_incompatible_environment_gap_is_measured` (ceiling 8); `.scratch/retrieval-release-closure/FINAL-REPORT.md` §11 |
| Representation A/B result | `FINAL-REPORT.md` §2 — precision 0.469 → 0.653 same model; `retrieval_eval_v1.before_after.json` |
| sha drift | `FINAL-REPORT.md` §18 — 2/200 |
| Branch/commit state | `git log`/`git rev-list --left-right --count origin/main...HEAD` → `0  40`; HEAD `f58d95c`; origin/main `33d4c05` |

---

## 8 (recommendation). Ideal Retrieval Document — conceptual, NOT implemented

Based only on the current schema. The builder already emits most of this; the
change is *what feeds it*, plus 4 fields it should also read.

```
PROCEDURE:   <display_name>
GOAL:        <capability_statement | goal>
USE WHEN:    <domain_payload.applies_when>  +  <rendered preconditions>
             +  <required_state, rendered>                         ← builder does not read required_state today
DO NOT USE WHEN:  <scope constraints, rendered>  +  <exclusions, rendered>   ← builder reads NEITHER today
WHY:         <mechanism/rationale>                                 ← NO schema field; would need one, or keep a
                                                                    distilled line from the SKILL.md body
HOW:         <steps>                                               ← already good
REQUIRES:    <tool_requirements>  +  <dependency refs>             ← already emitted when present
CONDITIONS:  <invariants>  +  <compatibility>
EXPECTED OUTCOME:  <postconditions>  +  <expected_effects>         ← builder does not read expected_effects today
FAILURE MODES:     <failure_conditions>                           ← already emitted; needs data
CONTEXT:     <domain>  +  a SHORT summarised "observed useful in: <task/env types>"
             derived from linked episodes / verification_stats.context_keys_seen  ← not assembled today
CLAIMS:      <short text of knowledge_nodes linked via preconditions[].claim_id>  ← not joined today
EVIDENCE CONTEXT:  high-level only ("verified across N contexts"), NOT counters,
             NOT evidence ids, NOT timestamps
```

Still excluded (unchanged from today, correct): UUIDs, timestamps, evidence/audit
ids, raw counters, verification bookkeeping, provenance dicts, owner/approver
identity, secrets, resource manifests.

Deterministic + versioned: keep the `procdoc_vN` + sha256 + `--representation`
backfill machinery exactly as is; any recipe change bumps the version.

**Not implemented in this pass. This is analysis only.**
