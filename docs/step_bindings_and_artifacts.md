# Step bindings, source locators, and preserved artifacts (there is no Implementation object)

Product decision (2026-09-20): an "implementation" is **a one-step procedure, or a step in a multi-step procedure**.
Procedures carry a source locator, every step carries its own, and a step carries how it is executed. The global
`implementations` object (tables, registry, selection, lifecycle, telemetry, REST `/implementations`, MCP tools) was removed in
migration 98. The `implementation_wrong` **failure class** (spec v4 §36: "the mechanism failed", as opposed to
`procedure_wrong`) is a diagnosis vocabulary, not the object, and stays.

## The layers

```
GitHub repository
  → Source                       sources            repository URL, publisher/owner, license, source type   (migration 64)
  → Ingested Artifact            ingested_artifacts repo-relative path, commit, content_hash, role, mime/language,
                                                    byte_size, content_ref (object storage), extraction_status,
                                                    admission_decision (screening), execution_allowed           (32/49/98)
  → Artifact blocks/chunks       artifact_blocks
  → Observation / Claim          "this repo contains scripts/validate.py" / "it appears to validate Python repos"
  → Candidate Procedure          one-step procedure whose step binding points at the artifact
  → screened executable Procedure → verified Procedure (execution evidence)
```

Finding a script never makes it executable. Skill-package ingestion (`skill_ingestion._persist_script_procedures`):

1. **preserves** each bundled script as an immutable `ingested_artifacts` row (`role='executable_source'`, keyed by content hash;
   bytes to object storage when `OBJECT_STORAGE_URL` is set, else `extraction_status='metadata_only'`),
   `execution_allowed=false`, `admission_decision` NULL (unscreened);
2. proposes a **candidate** one-step Procedure (`capture_procedure(procedure_dedup=True)`, `source_key = skill-script:<bundle hash>:<path>`)
   whose step has `source_locator` (uri/path/commit/content hash) and
   `binding = {kind: source_artifact, source_artifact: <artifact id>, entrypoint, runtime, args, sandbox_policy: isolated}`, and lists the
   artifact in `procedures.source_artifacts` with its role.

Running it (`step_binding._execute_source_artifact`) is refused unless **all** hold: artifact exists and is `executable_source`;
`execution_allowed`; `admission_decision='admitted'`; bytes are stored and still hash to `content_hash`; runtime has a sandbox
executor (only `python` today). A DB check forbids `execution_allowed` on any other role, so a style/design reference can be
retrieved for context and can never be executed.

## Procedure JSON

```json
{
  "name": "Run repository validation",
  "goal": "Validate the repository using the discovered script",
  "source_locator": {"source_id": "...", "uri": "https://raw.githubusercontent.com/o/r/<commit>/scripts/validate.py", "commit": "<sha>", "content_hash": "<sha256>"},
  "source_artifacts": [{"artifact_id": "...", "path": "scripts/validate.py", "role": "executable_source", "execution_allowed": false}],
  "steps": [{
    "order": 0, "description": "Run scripts/validate.py", "goal": "verification",
    "source_locator": {"...": "per-step span, or the procedure's marked inherited:true"},
    "binding": {"kind": "source_artifact", "source_artifact": "<artifact id>", "entrypoint": "scripts/validate.py",
                "runtime": "python", "args": ["--strict"], "sandbox_policy": "isolated"}
  }]
}
```

Style/design references: `{"artifact_id": "...", "path": "src/components/Button.tsx", "role": "style_reference", "execution_allowed": false}`
(`procedures.source_artifacts`). **Not wired yet:** ingesting non-script repository files (style/design/source references) from a GitHub repo
as artifacts — the validation, columns and gating exist, the ingestion adapter that creates them does not.

## Binding vocabulary (closed; validated at ingestion, `services/source_locators.py`)

`kind` ∈ `tool, model, mcp_tool, adapter, sandbox, command, runtime, slm_artifact, http_api, binary, wasm, container, source_artifact`.
The primary value sits under the kind's own key (`mcp_tool: "github.search"`); the address is one of `endpoint` (http/model endpoint),
`server_url` (MCP server), `path` (binary / wasm module), `image` (container), `entrypoint`; plus `args`, `env_refs` (credential
**names** only, never values), `sandbox_policy`, `locator`, `parameters`, `verifier`, `resources`. Unknown keys are rejected;
`mcp_tool/http_api/binary/wasm/container` require an address; `endpoint`/`server_url` must be http(s)/ws(s)/grpc URLs.

| binding kind | executor | runs today |
|---|---|---|
| `command`, `sandbox` | deterministic (sandbox) | yes |
| `mcp_tool`, `tool` | tool (MCP streamable-http) | yes (needs `server_url`) |
| `http_api`, `adapter` | api (HTTP) | yes |
| `model` | frontier | yes (default for an unbound step) |
| `source_artifact` | deterministic, gated as above | python only |
| `slm_artifact`, `runtime`, `binary`, `wasm`, `container` | — | storable + validated; a run **fails loudly**, never substitutes |

## Execution

`PlanNode.binding` is copied from the step at compile time (`procedure_graph._step_binding`); `step_binding.execute_node` dispatches on it
(frontier when unbound). Goal trees (`goal_resolution`) have `chosen ∈ {step, procedure, unresolved}`: a step carrying a binding is an
executable leaf; fallback is at Procedure level. Cost telemetry is per `(procedure_id, step_order)` in `step_execution_telemetry`.
`execution_run_nodes.binding`, event `step_bound`, `plan_deviation` (`binding_diverged_from_plan`), `.stealth` run lines (`binding=`).

## Migration 98 and the fold

Migration 98 snapshots every legacy implementation (+ procedure links + which run node pinned it) into `legacy_implementation_fold`,
then drops the tables/columns. `python -m app.ingestion.admin fold-implementations` converts the snapshots through the normal capture
path: linked → binding on that procedure's steps (`supersede_procedure`); unlinked → one-step procedure. Re-runnable; nothing is folded into
an executable/verified state. Drop `legacy_implementation_fold` once the fold reports no failures.
