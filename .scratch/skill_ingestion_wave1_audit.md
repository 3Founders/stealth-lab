# Structured Skill Ingestion Wave 1 — Phase 0 Audit

Date: 2026-09-07
Branch: `gate-2b`
HEAD: `47a944b19b9118462b0081c8a4d86b291911b4c8`

## Worktree baseline

The worktree was already dirty before this wave began. Existing changes are user-owned
and must not be overwritten or staged by this work:

- modified: `backend/app/local_agent/runner.py`
- modified generated metadata under `backend/stealthlab_backend.egg-info/`
- untracked: `.codex/`, `backend/_mcp_probe.py`, Gate 3 scripts/results/logs, and MCP
  server logs

Recent commits at audit time:

```text
47a944b (HEAD -> gate-2b) gate 2b: add behavioral verification and validate lazy schemas
827d745 (origin/main, main) test: make offline local-runner suite hermetic (mock embedding provider seam)
055041b general fix: gate verification success on real artifact validation + fix context-identity gaming
bd768e6 (tag: v1-final-2026-09-04, origin/fix/report-execution-mcp-contract) fix: report_execution's success_criteria is a structured object, not a JSON string
39bcf0a (feat/corpus-mcp-eval) core-a: 60-seed source resolution (spec section 17)
0b066aa core-a: autonomous run -- reconstructed state (spec section 2)
3d95baf core-a: seed real verified procedures + implementations (eval finding D)
b135444 core-a: repair-tool for dangling sub-procedure pins (eval finding A)
```

## Existing modules and seams

### Source and Artifact

- `backend/app/services/ingestion_sources/base.py`
  - `SourceRef`: URI/repository/path/commit pointer.
  - `SourceArtifact`: fetched UTF-8 content plus SHA-256, source type, URI,
    repository, path, commit, and discovery time.
  - `SourceAdapter`: synchronous `discover` / `fetch` / `fingerprint` interface.
- `backend/app/services/ingestion_sources/skill_md.py`
  - `LocalDirSkillSource`: recursive local SKILL.md discovery.
  - `GitHubSkillSource`: GitHub tree API discovery and raw-file fetch.
- `backend/db/32_ingestion_provenance.sql`
  - `ingested_artifacts`: the durable source-artifact side record, keyed by
    `(source_type, uri, content_hash)`, with repository/path/commit,
    extractor version, procedure links, run, timestamps, visibility, and owner.
  - `ingestion_runs`: per-invocation source spec and metrics.

There is no separate canonical `sources` table and no general blob/object Artifact table.
The existing structured-ingestion Artifact is `SourceArtifact` in memory plus
`ingested_artifacts` in Postgres. Other artifact concepts are typed references
(`state.BlobUri`, evidence `content_ref`) rather than a competing store.

### SKILL.md ingestion

- `backend/app/services/skill_ingestion.py`
  - `parse_skill_md`: minimal hand-rolled flat frontmatter and list parser.
  - `compile_skill_artifact`: parse, injection screen, optional grounded capability
    abstraction, unchanged/stale detection, semantic novelty check, procedure capture or
    supersession, task-node materialization, and provenance write.
  - `run_skill_ingestion`: drives one adapter and records aggregate metrics.
- `backend/scripts/ingest_skills.py`
  - existing argparse CLI for `skill-dir`, `skill-repo`, and `search`.
- `backend/tests/test_ingestion_sources_offline.py`,
  `backend/tests/test_skill_ingestion_offline.py`, and
  `backend/tests/test_skill_ingestion_e2e.py` cover the current path.

### Procedure and ProcedureStep

- `backend/db/18_procedures.sql`: canonical `procedures` table. Steps are planner-neutral
  JSONB; imported procedures begin as `verification_state='candidate'`,
  `approval_status='proposed'`, `staleness='fresh'`, `availability='active'`.
- `backend/app/services/procedures.py`: capture, supersession, outcome tracking,
  lifecycle transitions, duplicate merge, and dedup sweep.
- `backend/app/services/procedure_extraction/schema.py`
  - `ProcedureStep` is explicitly planner-neutral and carries optional goal, inputs,
    preconditions, allowed implementations, expected outputs, verification, failure
    policy, cost budget, and pinned sub-procedure reference.
  - `ExtractedProcedure` is the trace-extraction intermediate used by the existing
    episode pipeline.
- `backend/app/services/procedure_extraction/`: existing trace/episode evidence
  extraction pipeline. It is not directly reusable as a document parser because its
  interface requires `ProcedureEvidence`; the current document compiler correctly
  reuses the canonical `capture_procedure` writer instead.

### TaskGraph and TaskNode

- `backend/app/models/plan.py`
  - `PlanNode` is the concrete runtime TaskNode shape.
  - `TaskGraph` stores the concrete compiled graph.
  - `ExecutionPlan` pins an exact Procedure version and compiled graph.
- `backend/app/execution/procedure_graph.py`
  - converts planner-neutral Procedure steps into linear PlanNodes,
  - recursively resolves pinned sub-procedure references,
  - produces the runtime graph before `compile_plan`.
- `backend/db/01_ontology.sql` contains durable `task_nodes`, originally the ontology's
  reusable task layer rather than compiled plan nodes.

The current SKILL.md compiler additionally creates one durable `task_nodes` row for every
imported step and a `procedures -> task_nodes` `OWNS/DECOMPOSES_TO` edge. This is the main
semantic mismatch for this wave: generic source steps should remain abstract Procedure
steps and become concrete PlanNodes/TaskGraph nodes only during runtime instantiation.

### Implementation

- `backend/db/33_implementation_registry.sql`: durable `implementations` and
  `implementation_tasks`; identity, lifecycle, verification, locator/invocation,
  requirements, provenance/license, content hash, scope.
- `backend/app/execution/implementation_registry.py`: candidate-first registration,
  inspection, task linking, listing, resolution, and capability derivation.
- `backend/app/execution/implementations.py`: implementation-kind vocabulary and
  non-durable selection logic.
- `backend/app/execution/providers.py`: executable provider adapters.
- `backend/app/execution/implementation_executor.py`: PlanNode resolution, binding, and
  dispatch.

No current SKILL.md path inspects bundled scripts or registers Implementation candidates.

### Provenance

Provenance is already represented in several compatible places:

- `procedures.provenance`, `domain_payload.source`, `created_by`, scope, owner, visibility;
- `ingested_artifacts` source URI/repository/path/commit/content hash/extractor/run;
- `implementations.source_ref`, author, license, derived_from, content hash, scope;
- Evidence source/content references and extractor version.

The current GitHub adapter accepts mutable refs and stores the tree response SHA as
`commit`, but does not require a 40-character commit identity. It also has no manifest
source ID, retrieved timestamp distinct from first-seen, source root/subtree, license
metadata, or artifact-bundle hash.

### Embeddings and retrieval

- `backend/app/services/embeddings.py`: existing embedding provider abstraction and
  1024-dimensional storage convention.
- `backend/db/19_procedures_embedding.sql`: procedure embedding column/index.
- `backend/app/services/applicability.py::find_applicable_procedures`: current semantic
  procedure lookup used by ingestion novelty checking and the existing CLI.
- `backend/app/services/retrieval.py`: normal hybrid graph retrieval with RRF.
- `backend/app/services/skill_ingestion.py`: embeds grounded capability text when
  available, otherwise the parsed description; imported state remains candidate.

There is no version-controlled retrieval QA suite for external skill families and no
top-five QA reporter.

### Execution and verification/evidence

- `backend/app/execution/plans.py`: compile/freeze Procedure + task description into an
  exact-version ExecutionPlan and TaskGraph.
- `backend/app/execution/graph_executor.py`: executes graph nodes.
- `backend/app/execution/durable_graph.py`, `durable_run.py`, `durable_resume.py`, and
  `plan_persistence.py`: durable run/graph lifecycle.
- `backend/app/execution/implementation_executor.py`: node-scoped implementation binding
  and provider dispatch.
- `backend/app/execution/evidence.py` and `backend/db/24_evidence.sql`: validated,
  append-only execution/reproduction evidence and the verified-state gate.
- `backend/app/execution/artifact_validation.py` and `behavioral_validation.py`: real
  success validation seams.

The runtime compilation seam already exists. The ingestion wave should preserve enough
Procedure-step metadata for this seam; it must not create a second runtime architecture.

### Jobs and workers

- `backend/app/services/ingestion_jobs.py` and the ingestion-job tables from the trace
  pipeline implement independently claimable, retryable jobs with per-row status and
  error retention.
- Existing job types are trace normalization, observation promotion, and procedure
  extraction. The SKILL.md corpus compiler is currently a synchronous batch loop and is
  not registered as one-package-per-job work.

## Actual current path

```text
GitHub URL / local directory
    -> GitHubSkillSource or LocalDirSkillSource
    -> discover SKILL.md paths
    -> fetch only each SKILL.md as SourceArtifact
       (URI + repository + path + resolved tree SHA + SHA-256)
    -> parse_skill_md
       (flat frontmatter, description, applies_when prose, bullets/numbered lines)
    -> optional guarded model capability abstraction
    -> semantic novelty check through find_applicable_procedures
    -> capture_procedure or supersede_procedure
       (candidate Procedure, planner-neutral steps, provenance/domain_payload)
    -> embedding from capability statement or description
    -> one durable task_nodes row per imported step  [semantic mismatch]
    -> ingested_artifacts provenance row
    -> applicability/retrieval search over procedure embeddings
```

## Missing or insufficient for the seven-source corpus

1. No version-controlled seven-source manifest or manifest loader.
2. GitHub refs are not required to resolve to an immutable commit before discovery.
3. The fetch unit is only SKILL.md text, not a bounded content-addressed skill package.
4. No resource collection for scripts/references/assets/examples/templates/configs or
   directly referenced local files.
5. No path-traversal, symlink-escape, file-count/size/archive expansion, binary-junk, or
   duplicate-resource defenses suitable for untrusted public repositories.
6. Frontmatter parsing is flat and string-only; it loses structured metadata and does not
   normalize `allowed-tools` / `allowed_tools` while preserving unknown fields faithfully.
7. Existing list extraction treats every bullet anywhere as a top-level Procedure step;
   it does not reliably preserve headings, branches, termination, verification, failure
   handling, or dependencies.
8. Existing ingestion prematurely creates durable TaskNodes from generic source steps.
9. No dependency extraction/resolution or explicit unresolved-reference persistence.
10. No bundled-script/CLI/API Implementation candidate extraction or linkage metadata.
11. Tool requirements are not preserved beyond raw frontmatter.
12. No source-level license metadata and no stable manifest `source_id` on artifact rows.
13. Dedup is a single embedding threshold on description. Existing post-hoc merge is
    stronger but still needs source-aware structural comparison for this corpus.
14. No independently retryable per-skill-package job type, resume command, or partial
    source status report.
15. No canary selector, manual-inspection report, cross-source report, or retrieval QA
    fixture/runner.
16. GitHub repositories that are not SKILL.md corpora (OpenAI Plugins) and the format-spec
    corpus need routing modes behind the same Source/Artifact interface.
17. No parser conformance corpus for Agent Skills specification examples.
18. No runtime resolver proof that a PlanNode goal can recover relevant Procedures,
    requirements, and Implementation candidates without preloading all tools.
19. Live execution/evidence paths exist, but no wave-specific 3–5 procedural tests have
    yet been selected or run. Imported rows must remain candidates until real evidence
    supports promotion.

## Reuse decision

Extend the existing `ingestion_sources` + `skill_ingestion` deep module and existing
argparse script. Keep `SourceArtifact`, `ingested_artifacts`, `procedures`,
`implementations`, `ingestion_runs/jobs`, procedure embeddings/retrieval, runtime
Procedure-to-TaskGraph compilation, and evidence unchanged as the canonical seams.

Required additions should be additive: a manifest-driven GitHub package adapter,
normalized package intermediate, resource/dependency/implementation metadata, provenance
columns/tables only where the existing side record cannot represent required cardinality,
job handlers for scale, QA/reporting commands, and tests. No first-class runtime `Skill`
object and no ingestion-time concrete TaskGraph are justified.
