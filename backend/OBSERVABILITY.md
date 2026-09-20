# Observability

OpenTelemetry is the only tracing foundation (`app/telemetry.py`). Off unless
`OBSERVABILITY_ENABLED=true`; with it off every span is a no-op and the app
behaves exactly as before. Phoenix is the one supported UI; any OTLP/HTTP
backend also works.

## Run locally for free (no Docker, no cloud)

```bash
pip install arize-phoenix && phoenix serve        # UI + OTLP at http://localhost:6006
OBSERVABILITY_ENABLED=true OBSERVABILITY_BACKEND=phoenix uvicorn app.main:app
```

No install at all: `OBSERVABILITY_BACKEND=console` prints spans to stdout.
Enabled with no backend keeps trace ids (they are stored on canonical events)
but exports nothing.

## Where the truth lives

| Question | Answer from |
|---|---|
| What did Stealth decide? | Postgres: `execution_run_events`, `route_decisions`, evidence tables |
| How did it get there, how long did it take, what did it cost? | Trace |

`execution_run_events` (the existing append-only log) gained `event_version`,
`trace_id`, `span_id` (migration 94) and three types: `claims_retrieved`,
`candidates_reranked`, `candidate_rejected`. Join a trace to state with
`SELECT * FROM execution_run_events WHERE trace_id = '<hex>'`. Events are never
sampled. Spans carry ids, counts, ranks, timings, model names, tokens, cost and
failure codes only, never Claim text, prompts or tool output (`_clean` drops
collections and caps strings; large artifacts use `telemetry.artifact_attrs`).

Spec event names -> stored types: RunStarted `run_started`, CandidateRetrieved
`procedure_retrieved`, ClaimsRetrieved `claims_retrieved`, CandidateReranked
`candidates_reranked`, CandidateRejected `candidate_rejected`, ProcedureSelected
`route_decided`, ImplementationSelected `implementation_bound`, ExecutionStarted
`node_started`, ExecutionFinished `node_succeeded`/`node_failed`,
VerificationCompleted `verification_completed`, RunFailed `run_failed`,
RunCompleted `run_finalized`.

## Trace shape

`mcp.tool.<name>` (each MCP tool) and `stealth.run` (one per drive segment of a
durable run; a resumed run is a second trace with the same `stealth.run_id`)
> `retrieval` > `retrieval.lexical|embedding|cost|claim_retrieval|rerank`,
`verification.nli_jev`, `embedding`, `llm.chat`, `execution` (per node),
`verification`, `persistence`; ingestion: `ingestion.batch` > `ingestion.job` >
`ingestion.fetch|compile`.

Search in Phoenix by `stealth.failure_code`, `stealth.model`,
`stealth.shard_id`, `stealth.retrieval_stage`, `stealth.run_id`.

Failure codes (`FailureCode`): RETRIEVAL_ERROR, CLAIM_LOOKUP_ERROR,
RERANKER_ERROR, MODEL_TIMEOUT, MODEL_ERROR, DB_ERROR, SHARD_UNAVAILABLE,
SANDBOX_ERROR, EXECUTION_ERROR, VERIFICATION_FAILED, INGESTION_PARSE_ERROR,
INGESTION_ERROR, DUPLICATE_OBJECT.

## Environment

| Var | Default | Meaning |
|---|---|---|
| `OBSERVABILITY_ENABLED` | false | master switch |
| `OBSERVABILITY_BACKEND` | none | none / console / otlp / phoenix |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | OTLP/HTTP base (`/v1/traces` appended); phoenix defaults to localhost:6006 |
| `STEALTH_TRACE_SAMPLE_RATE` | 0.1 | full-trace fraction for routine successes |
| `STEALTH_BENCHMARK` | false | keep every trace in full |
| `STEALTH_SHARD_ID` | primary | value of `stealth.shard_id` |
| `STEALTH_MODEL_PRICES` | unset | JSON `{"model-prefix": [usd_per_1M_in, usd_per_1M_out]}`; unset => cost omitted |
| `OTEL_SERVICE_NAME` | stealthlab | service / Phoenix project |

Sampling is tail-based at export: any errored or `force_keep` span (failed
verification) or benchmark mode exports the whole trace; otherwise the rate
decides between the full trace and its root span only.
