# API Usage Ledger — Gemini embedding keys

Shared-quota visibility for every process that embeds (tau2 sweeps, backfills,
smoke tests, ad-hoc probes). Free tier per key: **100 RPM · 30K TPM · 1K RPD**,
window resets **midnight Pacific**. Pool totals assume independent projects.

## Key inventory

| # | Key (masked) | Added | Notes |
|---|---|---|---|
| 1 | AQ…pRg | 2026-08-23 | exhausted same day during reembed + phaseJ |
| 2 | AQ…IGw | 2026-08-23 | suspected shared pool with #1 |
| 3 | AQ…9_0w | 2026-08-24 | fresh; first in rotation |

Rotation order lives in `backend/.env` → `GEMINI_API_KEYS` (first = primary).
Transport budget: `settings.embed_tpm_budget` (25K TPM combined) enforced via
`backend/logs/gemini_bucket.json`.

<!-- BEGIN:COUNTERS -->
(counters not yet rendered -- run scripts/gemini_usage_report.py --write)
<!-- END:COUNTERS -->

## Known consumers

| Caller tag | What | Typical burn |
|---|---|---|
| phaseN/O/P… | tau2 banking sweeps (`run_phase*.ps1`) | ~2–6 embeds per sim |
| reembed_procedures | one-off corpus re-embed | ~70 batched reqs / ~215K tokens |
| smoke | ad-hoc verification calls | handful |

Set `$env:CALLER_TAG="<name>"` in any launcher before starting a run so its
calls are attributable in the log.

## Quota incidents

- **2026-08-23**: keys #1+#2 hit RESOURCE_EXHAUSTED mid-day (reembed 698 docs
  + concurrent sweeps). Voyage fallback carried traffic; task_012/008-class
  substrate failures traced to this. Led to: 3-key rotation, TPM bucket,
  and this ledger.
- **2026-08-23 (late)**: phaseJ task_008 died permanently on RateLimitError at
  concurrency 4 — motivated cross-process bucket + preflight checks.

## Conventions

1. Any new run sets `CALLER_TAG` before launching.
2. Before launching a big job, run
   `python backend/scripts/gemini_usage_report.py` and check today's RPD.
3. After incidents, append one line to Quota incidents with cause + fix.
