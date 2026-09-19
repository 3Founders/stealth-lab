# Subprocessors & Third-Party Data Processing

**STATUS: DRAFT — REQUIRES LEGAL REVIEW.**

This list is derived from what's actually referenced in `backend/requirements.txt`,
`backend/.env.example`, and related config as of 2026-09-09 — not from a claimed vendor
relationship. **We do not assert that any provider below has a signed DPA/SCC with StealthLab
unless a founder/counsel confirms that separately** — the codebase cannot prove a contract exists.

## Currently configured / in active use

| Provider | Purpose | Evidence | Data involved |
|---|---|---|---|
| **Supabase** | Authentication (Supabase Auth issuing OIDC JWTs); in hosted mode, likely also the Postgres database | `backend/.env.example` (`SUPABASE_PROJECT_URL`, `SUPABASE_JWT_AUDIENCE`), `frontendv1/src/lib/supabase/client.ts` | Account email, user id, session tokens; in hosted mode, all application data stored in that Postgres instance |
| **Anthropic (Claude)** | Model calls (debate panel member, per `.env.example`) | `backend/.env.example` (`ANTHROPIC_API_KEY`), `backend/requirements.txt` (`anthropic`) | Procedure/claim text sent for model processing |
| **OpenAI** | Model calls (debate panel member) | `backend/.env.example` (`OPENAI_API_KEY`), `requirements.txt` (`openai`) | Same as above |
| **Fireworks AI** | Model calls (debate panel member) | `backend/.env.example` (`FIREWORKS_API_KEY`) | Same as above |
| **Google (Gemini / `google-genai`)** | Model calls (independent judge role) and/or embeddings | `backend/.env.example` (`GOOGLE_API_KEY`), `requirements.txt` (`google-genai`) | Same as above |
| **Voyage AI** | Embeddings for search | `backend/.env.example` (`VOYAGE_API_KEY`), `requirements.txt` (`voyageai`) | Redacted procedure/trace text sent for embedding |

## Optional (only active if explicitly configured)

| Provider | Purpose | Evidence | Notes |
|---|---|---|---|
| **Sentry** | Error/trace observability for the backend ASGI apps and worker | `backend/requirements.txt` (`sentry-sdk[fastapi]`), `backend/app/observability.py` | No-op unless `SENTRY_DSN` is set — "optional at runtime" per the requirements-file comment |
| **General Compute** | Hosted open-weight model access (alternative to the closed-frontier panel above) | `backend/.env.example` (`USE_GENERAL_COMPUTE`, `GENERAL_COMPUTE_*`) | Off by default (`USE_GENERAL_COMPUTE=false`) |
| **Ollama (self-hosted/local)** | Local model serving for development, no external network call | `backend/.env.example` (`USE_LOCAL_MODELS`, `LOCAL_BASE_URL`) | Off by default; when on, routes to a local endpoint you run yourself — not a third-party subprocessor in the usual sense |

## Not found in the codebase

No evidence was found of: Stripe/payment processors, SMS/email delivery providers, CRM/marketing
tools, or dedicated cloud hosting vendor SDKs beyond what's implied by Supabase/deployment config
(`render.yaml`). **[FOUNDER: if you use Render, AWS, GCP, or another host directly, add it here —
this list only reflects what appears as application-level dependencies/config in the reviewed
code, not your infrastructure/hosting arrangement.]**

## What this list does not tell you

- **DPA/SCC/contractual status** with any provider above — undetermined from the code; confirm
  with the founder/legal team before making any compliance claim.
- **Data residency/region** for each provider — not configured explicitly anywhere reviewed;
  default provider regions apply unless stated otherwise in a provider-specific config not found
  here.
- **Retention at the provider** — governed by each provider's own terms, not StealthLab's.

Per `STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` LC-005, a formal provider/model policy registry
(with region, retention, training-use, subprocessor, and DPA fields, plus a `can_send()` decision
point gating which data classes may go to which provider) was specified but listed as **not yet
implemented** as of the compliance ledger dated 2026-09-08. Until that lands, treat provider
routing as governed by API-key presence and cost limits only (`GOVERNANCE_ENABLED`,
`DAILY_LLM_BUDGET_USD` in `.env.example`) — **not** by a data-classification policy.

---
*Generated from repository state on 2026-09-09. Cross-references: `backend/requirements.txt`,
`backend/.env.example`, `backend/app/observability.py`,
`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` (LC-005).*
