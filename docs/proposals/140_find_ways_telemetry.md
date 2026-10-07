# Proposal: migration 140, find_ways telemetry columns (NOT applied, NOT in backend/db)

Status 2026-10-07: proposal for review. Nothing here has been run. Next free migration number is 140 (139 is the latest).

## Why

`retrieval_decisions` (created in `117_search_log_database.sql`, the search-log database) records one row per `find_ways`,
but everything useful is inside `detail` JSONB and there is no organisation id. That blocks: the customer `find_ways` view,
the adoption funnel, per-org quality, and measuring real cost per call. A measured run on 2026-10-07 showed the call structure
(1 embedding, 2 to 3 judge calls, 7 comparisons, ~0.7k to 0.9k input tokens) is small and countable, so we can store it.

## Changes (all additive and idempotent, no backfill, per hard rule 1)

```sql
-- 140_find_ways_telemetry.sql   (target database: the search-log database, where retrieval_decisions lives)
ALTER TABLE retrieval_decisions
    ADD COLUMN IF NOT EXISTS org_id           UUID,       -- same id as provider_call_ledger.org_id; NULL = no org (personal / anonymous)
    ADD COLUMN IF NOT EXISTS client           TEXT,       -- claude-code | cursor | codex | opencode | ... (was detail.client)
    ADD COLUMN IF NOT EXISTS outcome          TEXT,       -- resolved | ambiguous | no_match | refused (was detail.outcome)
    ADD COLUMN IF NOT EXISTS total_ms         INTEGER,
    ADD COLUMN IF NOT EXISTS plan_ms          INTEGER,    -- router overhead for the model plan
    ADD COLUMN IF NOT EXISTS embed_calls      SMALLINT,
    ADD COLUMN IF NOT EXISTS judge_calls      SMALLINT,
    ADD COLUMN IF NOT EXISTS comparisons      INTEGER,    -- candidate x query judgments sent to the judge
    ADD COLUMN IF NOT EXISTS input_tokens     INTEGER,    -- judge input, from the provider when reported, else NULL (never estimated into this column)
    ADD COLUMN IF NOT EXISTS output_tokens    INTEGER,
    ADD COLUMN IF NOT EXISTS stage_ms         JSONB,      -- {embed, goal_search, goal_rerank, hierarchy, procedure_search, procedure_rerank}
    ADD COLUMN IF NOT EXISTS verdict_mix      JSONB,      -- {goal:{matches,partial,unrelated}, procedure:{applies,partial,not_applicable}}
    ADD COLUMN IF NOT EXISTS degraded_reasons TEXT[];     -- reason codes from meta.degrade(...), for the fallback-removal decision

CREATE INDEX IF NOT EXISTS idx_retrieval_decisions_org_created ON retrieval_decisions (org_id, created_at DESC)
    WHERE org_id IS NOT NULL;

-- Customer isolation: same strict policy pattern as migration 136 (an unset tenant sees nothing).
ALTER TABLE retrieval_decisions ENABLE ROW LEVEL SECURITY;
CREATE POLICY ... sl_org_strict on org_id   -- exact text copied from 136, needs the 136 helper function in this database (see question 1)

CREATE OR REPLACE VIEW v_org_find_ways_daily WITH (security_invoker = true) AS
SELECT org_id, date_trunc('day', created_at) AS day, client, outcome,
       count(*) AS requests,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY total_ms) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) AS p95_ms,
       avg(judge_calls) AS avg_judge_calls, sum(comparisons) AS comparisons,
       sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
       count(*) FILTER (WHERE degraded) AS degraded_requests
FROM retrieval_decisions WHERE org_id IS NOT NULL GROUP BY 1, 2, 3, 4;
```

Writer change, in the same change as the migration (half-gate rule): `_record_find_ways` in `app/mcp_server/server.py` fills the
new columns, and the retrieval service returns the counts it already has in memory (`RetrievalMeta.counts`, `latency_ms`,
`degrade` reasons) plus a small call counter around the judge. `detail` keeps its current keys so nothing reading it breaks.

Proving tests (Appendix C style): offline test that the INSERT carries the new columns; live test that org A cannot read org B's rows
through the view; test that a request with no org writes `org_id` NULL and appears in no customer view.

## What this deliberately does not do

- No backfill. Existing rows keep `org_id` NULL and are invisible to customers (fresh-start rule).
- No prompts, replies, queries or repo facts. `query_sha256` stays a hash. Tokens are counts only.
- No estimated tokens in `input_tokens` / `output_tokens`: NULL means "not reported", as in the ledger.

## Questions for the reviewer

1. `retrieval_decisions` lives in the search-log database, not the main one. Does that database already have the org-RLS helper
   from migration 136 (the `sl_org_strict` policy function) and the app role setup? If not, either add the helper there in 140 or
   keep this table without RLS and serve customers through an API that filters by `org_id` (my preference: RLS, matching 136).
2. Where does the org id come from on an MCP call? Proposal: the same resolution `call_model` uses (`governing_org()`); NULL when none.
3. Retention class: operational metadata, 180 days minimum in India. Is there an existing purge job for the search-log database?
4. Table size: one row per `find_ways`. At 20,000 a day that is ~7M rows a year. Fine for Postgres, but should it be partitioned by month now?
5. Name collision check: are `outcome`, `client`, `total_ms` free on this table in every deployed environment?

## Related finding

`llm_spend` (117) already exists with provider, model, operation, tokens and cost. Internal item 4 (our own LLM spend) can reuse
it instead of a new ledger, so I would not add a table for that.
