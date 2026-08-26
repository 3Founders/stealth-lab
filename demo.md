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
| C1 | Install & boot | `docker compose up -d` (postgres must be **`pgvector/pgvector:pg15`** — plain postgres fails migration 01) + migrations auto-run | `python scripts/migrate.py --status` → all 23 `applied`; first verified engine run 2026-08-25 (`research/claude-code-hooks-v2@429ffa9`) |
| C2 | Traces flow in | Claude Code hooks → `app/services/trace_collector.py` (redaction choke point, dedup, bounded) → `trace_worker.py` → `agent_traces`/`trace_events`; HTTP path: POST `/v1/traces` per-record fault isolation | offline suite green; collector roundtrip test; worker quarantine behavior tests |
| C3 | Procedures get distilled | `procedure_extraction/` registry → strategies → derive (load-bearing preconditions) → validators (**V6 authoring-time invariant check**) → `procedures` table born-correct | suite incl. extraction validators; nothing enters without scope+provenance (`services/v0_gate.py`) |
| C4 | Reuse you can see | `mcp_server/server.py` tool `retrieve_precedent` (hybrid RRF + graph expansion over pgvector HNSW) returns procedure + confidence + provenance chain | retrieval leave-one-out sanity pattern (n=400, p=.0066 [T-24]) |
| C5 | Refusal with receipts | `check_procedure` → `ALLOW` / `WOULD_REFUSE` citing the exact superseded claim ids + changesets. **Audit mode only**: the agent is informed, not blocked; every refusal lands in the audit log | precondition-gate adversarial tests (fail-closed cascade); refusal payload shape pinned by tests |

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

- [ ] Full offline suite green: `cd backend && python -m pytest tests -q`
      (last: **914 passed / 106 skipped / 0 failed**, 2026-08-25)
- [ ] Migration chain applied clean on a throwaway
      `pgvector/pgvector:pg15` container via `scripts/migrate.py`
      (engine-verified; static text checks alone do NOT count)
- [ ] `python scripts/bootstrap_demo.py` runs the scripted two-phase story:
      phase A produces traces→procedures; phase B retrieves precedent AND
      triggers a `WOULD_REFUSE` after the fixture breaks a precondition claim
- [ ] Audit log shows the refusal line with claim ids (`cl_*` → `cl_*`)
- [ ] `check_procedure` response shape matches the pinned contract:

```json
{ "verdict": "WOULD_REFUSE",
  "procedure": "proc_pagination_v1",
  "reason": "precondition claim cl_17 'pydantic v1 compatible' superseded by cl_23",
  "evidence": ["changeset_09", "execution_41"],
  "capability_note": "0 failures recorded, environment changed" }
```

- [ ] README quickstart works from a clean clone: compose up → add MCP server →
      one task solved twice, second time citing precedent
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
