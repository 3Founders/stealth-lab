# P1 results: isolation, SLA, health checks

This is the evidence file for [securityp1.md](../securityp1.md). Every number states its source and date.
Nothing here touched a shared database unless a row says so, with the owner's approval quoted.

## Step 0: what already existed (2026-10-07)

- **Searched:**
  - `origin/main` history (`docs/*isolation*`, `*health*`, `*sla*`, `final_prod_docs/*`);
  - every local worktree (`sl-core-a`, `sl-core-b`, `sl-measure`, `sl-ship`, `sl-research`, `sl-evaluation`,
    `stealth-lab`, `stealth-lab-demo`) for files named `*isolation*`, `*probe*`, `*uptime*`, `*latency*`, `*load*`,
    `*p1_results*`, `*healthz*`, `*readyz*`.
- **Found:** only older code and tests already on `main`: `environment_probe.py`, the `*_isolation_e2e.py` suites, and
  `.scratch/perf_probe.py` (2026-09-03). There are **no** prior isolation run logs, no latency or uptime probe data,
  and no load-test output. Every side worktree is 0 commits ahead of `main`, so they hold nothing unique.
- **Asked the owner (2026-10-07)** whether the other person's results exist outside the repo. There has been no answer
  yet, so every number below is measured here.

## Production migrations (2026-10-07, owner-approved)

The owner gave explicit approval in the session: "i give you the permission to apply those migrations".

| Database | Before | Applied | After |
|---|---|---|---|
| Control (Neon) | 8 pending | `134_way_reports`, `135_route_aware_procedure_refs`, `136_org_governance`, `137_ledger_cost_components`, `138_ledger_timing_and_tier`, `139_org_denials_and_user_usage`, `146_benchmarks_repo_index`, `147_routing_priors` | 0 pending |
| Search members S001–S004 (Neon) | the same 8 pending on each | the same 8 (control set, as `scripts/provision_neon_shards.py` migrates members) | 0 pending, exit 0 each |
| Knowledge shards K001–K084 | the same 8 pending (K001 checked read-only) | **not applied**, waiting for the owner's decision | unchanged |

- `148_routing_priors_search.sql` (`--target search`) does not apply here: there is no single `SEARCH_DATABASE_URL`.
  The search members receive the control set, which already carries 147's columns.
- Only 135 drops anything: four foreign keys, replaced by route-aware triggers. No data changed.

## Test environment

- **Database:** a throwaway PostgreSQL 18.4 cluster on `127.0.0.1:55432` only, created with `initdb` at
  `C:\Users\user\pg-scratch`, plus pgvector; database `kel_v2_isolation`.
- **Migrations:** all 141 applied with `scripts/migrate.py --dsn <loopback>`, 0 pending.
- **Isolation from production:** every database variable named in `backend/.env` and `backend/.neon_shards.env` is
  overridden with the loopback DSN for live runs (`scripts/live_shutdown_check.py` does this itself). The server loads
  `.neon_shards.env` on its own, which is why the override is needed.
- **No Docker** on this machine, so there are no Linux-container runs. Signal tests ran on Windows (see P1-C).

## P1-C: health checks and graceful shutdown

| Item | Result | Evidence |
|---|---|---|
| `/healthz` liveness | done | `app/mcp_server/health.py`, `server.py` route; offline tests |
| `/readyz` readiness: pool, `SELECT 1` within 2 s, newest control migration applied, not draining; 503 names the check, no secrets | done | `tests/test_mcp_health_offline.py`: **12 passed** (2026-10-07). This includes a fake database error carrying a DSN whose text never reaches the body, and the real ASGI app answering without credentials or a database |
| Providers not in readiness | done | body says `"providers": "not_checked"`; `find_ways` degrades to lexical search |
| Probes point correctly | done, **not deployed** | Docker `HEALTHCHECK` targets `/healthz` (`scripts/docker_healthcheck.py`); Railway `healthcheckPath` targets `/readyz` (`railway.mcp.json`) |
| Drain on stop | done | `install_drain_on_signals` chains SIGTERM, SIGINT and SIGBREAK in front of uvicorn's handler; container runs `--timeout-graceful-shutdown 25` |
| Pool close and Sentry/OTel flush on shutdown | done | lifespan `finally`: `pool.close()`, `flush_observability()` |
| **Live shutdown test** | **passed** (Windows, CTRL_BREAK_EVENT) | `scripts/live_shutdown_check.py` on the scratch DB, 2026-10-07 22:47 IST. Details below the table |
| Stale `reserved` ledger rows | done | `org_governance.settle_abandoned[_all]`, plus `admin ledger-settle-abandoned [--apply]`; `tests/test_ledger_abandoned_offline.py`: **3 passed** |
| Migrations stop the container on failure | by design (unchanged) | `sh -c "python scripts/migrate.py && exec uvicorn ..."` |

**The live shutdown test, in detail:**
- `/readyz` returned 200 before the stop.
- A `/readyz` request held on a table lock was in flight when the stop signal arrived. It completed with 200 after 5.0 s.
- The log shows `readiness: draining (signal 21)`, then uvicorn's `Waiting for connections to close`,
  `Application shutdown complete` and `Finished server process`.
- The process exited 4.1 s after the signal.

**Exit code: 3, not 0.** Uvicorn 0.52.1 re-raises the captured stop signal after a clean shutdown
(`uvicorn/server.py`, `capture_signals`), so the status reports the signal: 3 after CTRL_BREAK on Windows, 143 after
SIGTERM on Linux. securityp1.md asked for "exit 0". That is not achievable without overriding uvicorn's signal
handling, and the check therefore passes on 0 or on the re-raised signal **only when** the shutdown log is complete.
**Not run:** SIGTERM in a Linux container (no Docker here). That should run once on Linux before relying on it.

**Abandoned reservations are settled at their worst case, not marked failed.** Whether the provider charged for an
abandoned call is unknown. Marking it `failed` would release the hold and record no cost. Settling it at
`reserved_usd` (`cost_source = 'upper_bound'`, the ledger's existing meaning of "no usage came back") means budgets
never under-count. The cutoff is 30 minutes (provider timeout 120 s, drain 25 s), and anything under 15 minutes is
refused.

## P1-A: multi-tenant isolation

In progress.

## P1-B: SLA

Not started. It needs the owner's decisions (support tiers, on-call) and measurements.
