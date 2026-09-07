# Structured skill ingestion wave 1 report

## Infrastructure

Implemented the existing SourceAdapter → SourceArtifact → Procedure path in
`backend/app/services/ingestion_sources/github_corpus.py` and
`backend/app/services/skill_ingestion.py`. The adapter resolves an exact Git
commit, takes a bounded filtered git snapshot, discovers recursive `SKILL.md`
files, packages local resources, rejects unsafe/symlink/cache paths, and hashes
content deterministically. The parser preserves unknown YAML frontmatter,
tool requirements, compatibility, resources, and explicit dependency links.

Additive database migrations:

- `39_structured_skill_ingestion.sql`: source snapshots, package metadata,
  resource/dependency manifests, and Procedure→Implementation/dependency links.
- `40_ingested_artifact_extractor_identity.sql`: allows corrected extractor
  versions to create immutable Procedure versions for unchanged upstream bytes.

Existing `ingestion_jobs` now supports `ingest_skill_package`, one package per
retryable job, idempotent queue insertion, and failed-job resume. A bounded
worker proof completed 3/3 jobs successfully.

CLI additions preserve the existing script framework:

```text
python scripts/ingest_skills.py discover --manifest ../config/skill_sources.yaml
python scripts/ingest_skills.py ingest --source addy-agent-skills
python scripts/ingest_skills.py ingest --source github-awesome-copilot --queue-only
python scripts/ingest_skills.py ingest --resume --process-jobs --worker-limit 100
python scripts/ingest_skills.py retrieval-qa --suite tests/fixtures/skill_retrieval_queries.json
```

## Sources

| Source | Resolved SHA | Discovered | Parsed/queued | Notes |
|---|---|---:|---:|---|
| addy-agent-skills | `48cb1168aeaaa70dfc2bbf709eddfa2a8ed8129a` | 25 | 25 | 20 new candidates; 5 canary idempotent repeats; 1 script candidate |
| obra-superpowers | `b36e0829c6d0140e93cfef2ca599b1b07d4a7797` | 14 | 14 | 8 Implementation candidates; 23 dependency records after parser v5 |
| github-awesome-copilot | `f38fb6cf039b835990d0f49dc161d7c2af99ef69` | 435 | 435 queued | Three worker jobs proven; remaining jobs staged |
| microsoft-skills | `02e0b2f852b39ea00c43283f999b83fc12079273` | 198 | 198 queued | Staged for bounded workers |
| openai-plugins | `1e285826e604f66f7208f7ac4dba0fe8341d1f57` | 535 | 535 queued | Current tree contains SKILL.md packages despite repository format label |
| openai-agents-python-skills | `d3761b3e7b55b147610991bf736ce5ea0161cc82` | 14 | 14 | Repo-local scope retained; 13 Implementation candidates |
| agent-skills-spec | `69ef37e9424c0a7ea9dd2293b559e43ec8176379` | 0 | 0 | Specification/examples source; parser conformance fixtures added |

No canonical procedures were marked verified. Source-reported text remains
separate from Stealth execution/evidence state.

## Quality findings

The canary caught and fixed checklist flattening and embedding that omitted
abstract workflow steps. A later source run still shows that some Superpowers
documents use long bullet-heavy prose; those remain candidates and are called
out for follow-up extraction refinement rather than being claimed verified.

Bundled scripts are registered as `candidate`/`unverified` deterministic
Implementations and linked through `procedure_implementations`; no generic
source step creates a durable TaskNode.

The dry-run Procedure deduplication sweep found 58 existing clusters across the
database. No merges were applied because the clusters include unrelated legacy
test data; source-level merge application requires an operator review.

## Retrieval

The 15-query canary suite achieved 15/15 expected families in top five and
14/15 at rank one. Detailed JSON is in `.scratch/skill_canary_retrieval.json`.

## Execution

No imported Procedure was executed as a live task. This is intentional: all
imported rows remain candidates and no verification/evidence claim is inferred
from ingestion. The existing runtime TaskGraph/TaskNode seam is available for
future instantiation.

## Remaining gaps

- Complete bounded processing of the 1,168 queued scale-source jobs.
- Improve bullet-heavy Superpowers extraction where no explicit workflow
  headings exist.
- Add a dedicated non-SKILL artifact route if future OpenAI Plugins revisions
  contain only manifests/API schemas.
- Add automatic cross-source dependency resolution after all package jobs
  finish, plus reviewed canonical Procedure merges.
- Add live Procedure→TaskGraph→TaskNode execution/evidence tests.
- Generate per-source retrieval QA and final execution evidence after the
  queued corpus is processed.
