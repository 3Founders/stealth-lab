# Production setup required from Chaitanya — retrieval / procedural-memory workstream

_Variable names are taken verbatim from `backend/app/config.py` (pydantic
`Settings`, upper-cased in the environment) and `frontendv1/src/lib/`.
**No secret values are printed here.** Verify commands below never echo a
secret._

Scope: only the credentials/config the **retrieval / embedding /
procedural-memory** workstream needs. Supabase Auth, OIDC, hosted-repo
authorization, Global Commons publication, deletion/export, tenant
isolation and compliance belong to the separate launch-compliance
workstream and are **not** covered here beyond the shared `DATABASE_URL`.

---

## 1. `DATABASE_URL` / `DATABASE_URL_DIRECT`

| | |
|---|---|
| service | Supabase Postgres (pgvector) |
| why | the procedure corpus, `retrieval_document`, embeddings, `display_*`, migrations, `llm_spend`, eval data all live here |
| where | backend runtime, worker, CI/CD, `scripts/migrate.py`. **Not** frontend. |
| secret | **yes** (contains the DB password) |
| browser-exposable | **never** |
| verify without printing | `python -c "import os;from dotenv import load_dotenv;load_dotenv('backend/.env');print('set' if os.environ.get('DATABASE_URL') else 'MISSING')"` ; connectivity: `python backend/scripts/migrate.py --status` (prints migration state, no secret) |
| prod settings | `DATABASE_URL` = the **pooled** (transaction-mode / PgBouncer) string for the app; `DATABASE_URL_DIRECT` = the **direct/session** string — `scripts/migrate.py` must use the direct one (DDL under a transaction pooler is unsafe; `app/db/session.py` docstring). Pooled connections need `create_pool(..., statement_cache_size=0)`. |
| quota/billing | Supabase project on a plan that allows the connection count the worker + API need; pgvector extension enabled (migration 01 requires `pgvector/pgvector:pg15`, not stock `postgres:15`). |
| region / retention | choose the Supabase region for your data-residency policy; the procedure corpus is treated as StealthLab-internal content. |

---

## 2. Production embedding provider  *(final choice: see `embedding-model-benchmark.md` — S1)*

`app/services/embeddings.py` supports exactly three providers: **gemini**,
**voyage**, **local**. The chosen one drives BOTH the corpus embedding
(migration) and every query embedding (`_configured_provider()` uses the
first entry of `EMBEDDING_PROVIDER_CHAIN`, unless `USE_LOCAL_MODELS=true`
which forces `local`). A single provider defines one embedding space —
never a runtime fallback chain.

### 2a. If the selected model is **Voyage** (`voyage-3-large`)

| var | notes |
|---|---|
| `VOYAGE_API_KEY` | **secret**, backend/worker/CI only, never browser. Verify: `python -c "import os;from dotenv import load_dotenv;load_dotenv('backend/.env');print('set' if os.environ.get('VOYAGE_API_KEY') else 'MISSING')"` then a 1-doc probe: `python -c "import asyncio;from dotenv import load_dotenv;load_dotenv('backend/.env');from app.services.embeddings import Embedder;print(len(asyncio.run(Embedder(provider='voyage').embed_one('probe'))))"` (prints `1024`, no secret). |
| `EMBEDDING_PROVIDER_CHAIN` | set to `voyage` (single provider). |
| `EMBEDDING_MODEL` | `voyage-3-large` (already the default; pin it, don't rely on the default). |
| `USE_LOCAL_MODELS` | must be `false` in production. |
| **billing / quota — ACTION REQUIRED** | the current key is on the **free tier without a payment method**: **3 RPM / 10 000 TPM**, plus 200 M free series-3 tokens. That rate limit makes both the initial ~2 478-doc migration and ongoing ingestion writes impractical. **Add a payment method at `https://dashboard.voyageai.com/`** for the org that owns this key to unlock standard rate limits. The 200 M free tokens still apply. |
| model pin | `voyage-3-large`, 1024-dim (matches `VECTOR(1024)` / `embedding_dimension`). |
| endpoint / region | Voyage is single-region SaaS; no region control. |
| data-use / retention policy | confirm Voyage's data-use terms are acceptable for StealthLab-internal procedure text before enabling in production. StealthLab has **no code-level provider-policy gate** on the embedding path (see §6) — approval is a manual/contractual decision. |

### 2b. If the selected model is **Gemini** (`gemini-embedding-001`)

| var | notes |
|---|---|
| `GEMINI_API_KEY` and/or `GEMINI_API_KEYS` | **secret**, comma-separated list rotated on 429. The three keys currently configured are **free-tier and were `RESOURCE_EXHAUSTED`** during the prior migration. |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` (default; pin it). |
| `EMBEDDING_PROVIDER_CHAIN` | `gemini`. |
| `EMBED_TPM_BUDGET` | shared rolling-minute token budget (default 25 000, ~17 % under the free tier's 30 K/min). Raise it to match a paid tier. |
| `GEMINI_USAGE_LOG` | `true` writes `backend/logs/gemini_usage.jsonl` telemetry (per attempt). |
| **billing / quota — ACTION REQUIRED** | enable billing on the Google Cloud / AI Studio project(s) that own these keys, or the migration cannot complete. Free tier ≈ 100 RPM / 30 K TPM / 1 K RPD per key. |
| region | Gemini API region per the Google project. Confirm data-use/training policy (`gemini-embedding-001` via the paid API is not used for training, but verify for your account tier). |

### 2c. If the selected model is **local** (`mxbai-embed-large` via Ollama)

| var | notes |
|---|---|
| `USE_LOCAL_MODELS` | `true` (currently set in `backend/.env`). |
| `LOCAL_BASE_URL` | default `http://localhost:11434/v1` — must point at a reachable Ollama/OpenAI-compatible server **in the production runtime**, not a developer laptop. |
| `LOCAL_EMBEDDING_MODEL` | `mxbai-embed-large` (1024-dim). Pin it. `ollama pull mxbai-embed-large` on the host. |
| billing | none (self-hosted); the **operational cost is running and scaling the Ollama host** with the throughput the query path needs (single cold call was ~16 s in this environment). |
| policy | data never leaves your infrastructure — the strongest privacy posture. |

---

## 3. Model-generated display-name provider (sections 3 + 4)

Uses the **existing** General Compute provider abstraction
(`ingestion_jobs._extraction_client`, OpenAI-compatible). No new LLM
abstraction was added.

| var | notes |
|---|---|
| `USE_GENERAL_COMPUTE` | `true` (currently set). |
| `GENERAL_COMPUTE_API_KEY` | **secret**, backend/worker/CI only, never browser. Verify: presence check as above; probe: `python -c "import os;from dotenv import load_dotenv;load_dotenv('backend/.env');from app.config import settings;from openai import OpenAI;print([m.id for m in OpenAI(api_key=settings.general_compute_api_key,base_url=settings.general_compute_base_url).models.list().data])"` (prints the model list, no secret). |
| `GENERAL_COMPUTE_BASE_URL` | `https://api.generalcompute.com/v1` (default; pin it). |
| models used | generation = `gemma-4-31B-it` (cheapest capable model served); judge = `gpt-oss-120b` (independent). Both pinned in `scripts/regenerate_display_metadata.py` (`GEN_MODEL`, `JUDGE_MODEL`). Provider list confirmed live: `gpt-oss-120b`, `deepseek-v3.1`, `deepseek-v3.2`, `gemma-4-31B-it`, `minimax-m2.7`. |
| billing / quota | ensure the General Compute account has quota for a one-time batch (≈ 40 real generations × 2–3 calls + a possible re-run for the ~1900 `disp_v1` audit) plus ongoing per-ingestion generation. Observed: `gemma-4-31B-it` intermittently returns HTTP 429 "currently experiencing high demand" — the script has 6× exponential backoff; a higher-priority tier removes it. |
| data-use policy | display-name generation sends the procedure's own `name / goal / capability_statement / applies_when / steps / constraints / domain` to General Compute. **Confirm this is acceptable** for procedures that may be private (see §6). Private-procedure display generation should be gated or disabled until a provider-policy check exists. |
| browser-exposable | **never**. |

---

## 4. Governance / spend accounting

| var | notes |
|---|---|
| `GOVERNANCE_ENABLED` | `true` (default). Gates money-spending endpoints via `app/services/governance.py` (rate limiter + `BudgetLedger` → `llm_spend` table). |
| `DAILY_LLM_BUDGET_USD` | `10.0` (raise for production migration + ingestion volume). |
| `PER_VIEWER_DAILY_BUDGET_USD` | `1.0`. |
| `DEFAULT_TENANT_ID` | single-tenant placeholder; leave as the default UUID until the auth workstream lands multi-tenant. |
| **GAP** | `governance.estimate_cost` prices `voyage` / `gemini` but **the `Embedder` embedding path does not call `governance.record(...)`** — embedding spend is only in `backend/logs/gemini_usage.jsonl` + the TPM bucket, not the `llm_spend` ledger. See `FINAL-REPORT.md` §21. Display-name generation via the OpenAI client is likewise not routed through the spend ledger by the closure scripts. |

---

## 5. Observability

| var | notes |
|---|---|
| `SENTRY_DSN` | optional; **secret-ish** (a DSN is a write key). Backend runtime + frontend (a separate public DSN for the browser if you want frontend error reporting — do **not** reuse the backend DSN). If unset, Sentry is a no-op. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` default. |
| `ENVIRONMENT` | set to `production` (default `local`) — tags Sentry events and gates posture asserts. |
| `RELEASE` | set to the deploy SHA/tag for Sentry release tracking. |
| `GEMINI_USAGE_LOG` | `true` → `backend/logs/gemini_usage.jsonl`. Ensure `backend/logs/` is writable and rotated in production (it is now gitignored). |
| retrieval telemetry | `stealthlab.retrieval` logger emits per-search: cascade survivors, gate-dropped count, returned count, zero-result flag, `RELEVANCE_GATE_VERSION`, cutoff — **no query text, no procedure content** (only `len` + a 10-hex sha of the query). Ensure your log pipeline captures this logger. |

---

## 6. Provider-policy / data-classification — OPEN ITEM

`CLAUDE.md` references a "provider routing policy" but there is **no
code-level gate** that checks a procedure's classification before its text
is sent to an external embedding or LLM provider. Current mitigations:

- the canonical retrieval document (`retrieval_document.py`) excludes ids,
  timestamps, evidence, provenance bookkeeping, audit fields **by
  construction** — so volatile/identity data is not embedded;
- `_configured_provider()` enforces exactly one provider (one vector
  space), not a silent fallback;
- `trace_redaction` covers trace ingestion, **not** the retrieval-document
  or display-generation paths.

**Action for Chaitanya:** make an explicit, recorded decision that the
selected embedding provider and General Compute are approved to receive
StealthLab procedure text (including any private procedures). If private
procedures must be excluded, that gate has to be built before enabling
`PRIVATE_VISIBILITY_ENABLED` — it is out of scope for this closure and is
listed as a release risk.

---

## 7. Frontend (retrieval-relevant only)

| var | notes |
|---|---|
| `NEXT_PUBLIC_API_URL` | `frontendv1/src/lib/api/client.ts`; the browser calls this. Set to the production backend origin. **Public by design** (prefix `NEXT_PUBLIC_`). No secret. |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `frontendv1/src/lib/auth.ts` — **auth workstream**, listed only so you know the retrieval UI shares the same authenticated `fetch`. The anon key is designed to be browser-public; RLS is what protects data. |
| `FRONTEND_ORIGIN` (backend) | CORS allowlist on the backend; set to the deployed frontend origin. |

---

## 8. MCP (retrieval tools are exposed over MCP)

| var | notes |
|---|---|
| `STEALTHLAB_MCP_TOKEN` | **secret**; shared bearer for the MCP server when OIDC is not configured (`deployment_mode='single_user'`). For a hosted/multi-user deployment set `DEPLOYMENT_MODE=shared` **and** `OIDC_ISSUER` + `OIDC_AUDIENCE` (auth workstream) — the MCP server refuses to boot in `shared` mode without them. |
| `MCP_WORKER_COUNT` | must stay `1` (the TasksExtension store is in-memory; `uvicorn --workers 1`). |

---

## Verification checklist (run before declaring the retrieval workstream configured)

```
# 1. DB reachable + migrations applied through 45 (41 is the auth workstream's)
python backend/scripts/migrate.py --status | tail -6

# 2. selected embedding provider answers a 1-doc probe with dim 1024
python -c "import asyncio;from dotenv import load_dotenv;load_dotenv('backend/.env');\
from app.services.embeddings import Embedder;e=Embedder();\
print(e.embedding_model_id(), len(asyncio.run(e.embed_one('probe'))))"

# 3. corpus fully embedded in ONE space, matching the query side
python -c "import asyncio,os;from dotenv import load_dotenv;load_dotenv('backend/.env');import asyncpg;\
print(asyncio.run(asyncpg.connect(os.environ['DATABASE_URL']).fetch(\
'select embedding_model_id, retrieval_document_version, count(*) from procedures \
where t_invalid is null and embedding is not null group by 1,2')))"

# 4. General Compute answers (display-name provider)
python -c "from dotenv import load_dotenv;load_dotenv('backend/.env');from app.config import settings;\
from openai import OpenAI;print([m.id for m in OpenAI(api_key=settings.general_compute_api_key,\
base_url=settings.general_compute_base_url).models.list().data])"

# 5. governance table exists
python -c "import asyncio,os;from dotenv import load_dotenv;load_dotenv('backend/.env');import asyncpg;\
print(asyncio.run(asyncpg.connect(os.environ['DATABASE_URL']).fetchval(\
\"select to_regclass('llm_spend')\")))"
```

None of the above print a secret.
