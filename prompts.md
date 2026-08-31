# 🚀 STEALTH LAB — PHASE 2

## Build the Global Procedural Library ingestion compiler

You have completed Phase 1.

Read these FIRST:

1. `.scratch/phase1_current_architecture.md`
2. `.scratch/phase1_schema_contract.md`

Treat them as architectural constraints.

Now implement the first production-quality version of the **Global Procedural Library ingestion pipeline**.

The core principle:

> **Public procedural knowledge must be compiled into the EXISTING Stealth Lab procedure/task substrate.**

Do NOT create a parallel database.

Do NOT create `global_procedures`.

Do NOT create `ingested_tasks`.

Do NOT flatten procedures into documents.

---

# PRODUCT GOAL

We want:

```text
public source
    ↓
source artifact
    ↓
procedure extraction
    ↓
canonical Procedure
    ↓
Task nodes
    ↓
global library
    ↓
retrieval
```

Initial sources:

1. SKILL.md
2. GitHub skill repositories
3. agentic workflow files
4. manually curated seed procedures

Start with SKILL.md.

---

# 1. TEST FIRST

Before implementation, add/update tests for:

1. parsing
2. extraction
3. procedure creation
4. task creation
5. provenance
6. embeddings
7. deduplication
8. version detection
9. retrieval

Every new behavior must have a failing test first.

---

# 2. SKILL.MD SOURCE ADAPTER

Use the existing ingestion/extraction architecture.

If a source adapter abstraction already exists, extend it.

Otherwise add only the smallest necessary source-adapter abstraction.

Create or extend:

`app/services/ingestion_sources/skill_md.py`

Potential interface:

```python
class SkillMdSource:
    async def discover(...)
    async def fetch(...)
    async def fingerprint(...)
```

Do not make it GitHub-specific internally.

---

# 3. EXTRACTION

Use:

`app/services/procedure_extraction/`

and its existing `ExtractionStrategy` interface.

The SKILL.md extractor should produce the existing `ExtractedProcedure`.

Extract:

* name
* goal
* capability_statement
* steps
* inputs
* expected outputs
* applicability/predicates
* slots where explicitly justified
* verification
* failure conditions
* provenance

Do not invent:

* preconditions not supported by source
* invariants not supported by source
* dependencies not supported by source
* capabilities not supported by evidence

Prefer `ABSTAIN`/rejection over hallucinated structure.

---

# 4. PROCEDURE VS TASK

This is critical.

The external skill:

```text
1. inspect repository
2. locate entrypoint
3. trace feature
4. modify code
5. verify
```

must become:

```text
Procedure
   ↓
Task 1
Task 2
Task 3
Task 4
Task 5
```

where appropriate.

Use the EXISTING `task_nodes` table.

Do not make a new task table.

Do not put scheduling edges into `ProcedureStep`.

Procedure steps stay planner-neutral.

---

# 5. MAP TO CURRENT PROCEDURE SCHEMA

Use the real `procedures` table from migration 18.

Populate only fields the source supports.

Potential mapping:

```text
SKILL.md name
    → procedures.name

SKILL.md goal
    → procedures.goal

structured steps
    → procedures.steps

parameters
    → procedures.parameter_schema

explicit applicability
    → procedures.preconditions / scope

explicit expected effects
    → procedures.expected_effects

explicit postconditions
    → procedures.postconditions

explicit invariants
    → procedures.invariants

explicit failures
    → procedures.failure_conditions

source metadata
    → provenance/domain/domain_payload/etc.
```

Do NOT populate semantic fields with invented values.

---

# 6. CAPABILITY STATEMENT

Use the EXISTING:

`procedures.capability_statement`

and the existing V4 validation rule.

The capability statement must be abstract and reusable.

Bad:

```text
"Fix the bug in foo/bar/auth.py."
```

Good:

```text
"Diagnose and modify an existing authentication implementation
in an unfamiliar software repository."
```

Use the existing GroundedHybridExtractor where appropriate.

Do not create another abstraction mechanism.

---

# 7. PROVENANCE

Every imported procedure must retain source provenance.

Use the existing procedure provenance fields.

At minimum preserve:

* source type
* source URL
* repository
* path
* commit/version if known
* content hash
* extractor/version
* ingestion timestamp

If the repository already has a structured source representation, use it.

Do not create duplicate provenance infrastructure.

---

# 8. EMBEDDINGS

Every procedure participating in retrieval must have a real embedding.

Use:

`Embedder().embed_one(...)`

using the repository's established embedding conventions.

The test must fail if:

```text
ingested procedure.embedding IS NULL
```

Do not introduce a second embedding model/configuration.

---

# 9. CANONICALIZATION / DEDUPLICATION

Use existing:

`find_applicable_procedures`

and existing applicability/similarity logic where appropriate.

Implement:

```text
new candidate
    ↓
existing equivalent/similar procedure?
    ↓
yes → attach/merge provenance or review
no  → insert
```

Do not destroy source lineage.

Do not merge merely because names are similar.

Use:

* goal
* capability statement
* task signatures
* semantic similarity
* source provenance

as signals.

---

# 10. PROCEDURE VERSIONING

Use the existing procedure version system.

Do not overwrite prior versions.

A changed source should produce either:

* a new version of the same logical `procedure_id`, or
* a new logical procedure if semantics changed materially.

Preserve exact source hash/version.

Never silently replace a prior version.

---

# 11. STALENESS

Implement the minimum useful V1 freshness mechanism.

For every imported artifact retain:

```text
content_hash
source_version / commit if available
first_seen
last_seen
```

When source changes:

```text
old version
    ↓
new source content
    ↓
candidate new procedure version
```

If the source explicitly references deprecated APIs or old package versions, create/propagate the existing staleness semantics.

Do NOT build a general automated repair system yet.

---

# 12. CONCRETE PROVING EXAMPLES

The seed corpus must include canonical coding procedures such as:

The way we want to implement this, is using the scope field in procedure, claim and task schema. 

```text
explore_repo
understand_architecture
find_entrypoint
trace_feature
identify_change_surface
debug_test_failure
review_pr
safe_refactor
migrate_deprecated_api
detect_deprecated_api_usage
```

Only create these when source evidence supports them.

For `migrate_deprecated_api`, demonstrate an applicability condition such as:

```text
package_version >= required_version
```

using the current predicate system.

---

# 13. SOURCE TYPES

Design source-type dispatch so later we can add:

```text
SKILL.md
GitHub workflow
documentation
tutorial
agent trace
research paper
benchmark
```

But implement only SKILL.md now.

The abstraction should be:

```text
Source
  ↓
Artifact
  ↓
Extractor
  ↓
ExtractedProcedure or ExtractedClaim, or both
```

not:

```text
GitHub importer
  ↓
hard-coded SQL
```

---

# 14. INGESTION MANIFEST

Every ingestion run should produce metrics:

```json
{
  "run_id": "...",
  "sources_seen": 10,
  "artifacts_seen": 100,
  "candidates": 120,
  "accepted": 83,
  "duplicates": 24,
  "rejected": 13,
  "stale": 3,
  "errors": 0
}
```

Use the existing ingestion-jobs infrastructure if possible.

---

# 15. CLI

Extend the current CLI/scripts rather than creating a random new command system.

Support a workflow equivalent to:

```bash
ingest skill-dir ./skills
ingest skill-repo <github-url>
search procedure "explore unfamiliar repository"
```

The precise command syntax should match the existing project.

---

# 16. LIVE TEST

Create a live proving test that:

1. fetches a REAL public SKILL.md source
2. runs the actual extractor
3. writes the actual current `procedures` table
4. writes real task nodes where appropriate
5. creates a real embedding
6. preserves provenance
7. retrieves the imported procedure
8. independently verifies the database rows

Do not call the ingestion successful merely because your own code printed `PASS`.

Check the actual persisted rows.

---

# 17. REQUIRED OUTPUT

At the end produce:

`.scratch/phase2_ingestion_report.md`

Include:

* sources used
* procedures ingested
* task nodes created
* duplicates
* rejected candidates
* stale candidates
* extraction failures
* exact schema surfaces used
* known limitations

---

# 18. STOP CONDITION

Do NOT implement SLM/WASM execution yet.

Do NOT implement full UGC.

Do NOT implement the full research claim graph.

Do NOT redesign retrieval.

The exit condition is:

```text
REAL PUBLIC SKILL
      ↓
REAL PROCEDURE
      ↓
REAL TASK NODES
      ↓
REAL EMBEDDING
      ↓
REAL PROVENANCE
      ↓
REAL GLOBAL RETRIEVAL
```

using the CURRENT Stealth Lab substrate.
