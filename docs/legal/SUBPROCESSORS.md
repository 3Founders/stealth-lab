# Subprocessors and third-party data processing

**STATUS: DRAFT. Needs founder confirmation of the "Confirm" column and counsel review.** Updated 2026-10-07 from `backend/requirements*.txt`, `backend/app/config.py`, `backend/app/services/{embeddings,object_storage}.py`, `docs/deploy/hosted-mcp.md`, `docs/provider_connections.md` and the deployment notes. The code cannot prove a contract, a region or a retention setting, so those are left for you to confirm. This replaces the 2026-09-09 version, which listed the debate-panel vendors as if they were in the MCP path.

## A. In the data path of the hosted MCP service

| Provider | Purpose | Customer data it can receive | Region | Confirm |
|---|---|---|---|---|
| **Neon** (Postgres, pgvector) | Primary database: procedures, goals, outcomes, ledger | Everything we store (see `final_prod_docs/02_data_inventory_and_retention.md`) | us-east-2 per deployment notes | Project region; signed DPA; encryption-at-rest statement; backup window |
| **Supabase** | Sign-in and the OAuth 2.1 server | Email, user id, session tokens | **[project region]** | Region; DPA |
| **Google Vertex AI** | Embeddings and model judgment for `find_ways`; ADC, no API key | The task description, and procedure text sent for judging and embedding | `us-central1` for embeddings; `global` for newer chat models (`config.py: vertex_region, vertex_llm_location`) | Project id; data-use terms for the Vertex model used (zero retention, no training); region |
| **Cloudflare R2** | Object storage for large raw artifacts (documents, traces, logs), content-addressed | Raw ingestion artifacts, redacted by `trace_redaction` | **[R2 jurisdiction]** | What is actually stored; the jurisdiction setting |
| **Container host** (Railway per `railway.mcp.json`) | Runs the MCP server | All request traffic in transit; logs | **[region, not set in the repo]** | Host, region, log retention |

## B. Configured fallbacks, off unless a key is set

The code tries providers in order, so any one can receive the same text as Vertex does if the earlier ones fail (`embedding_provider_chain = "gemini,voyage"`; `semantic_provider_fallbacks = "vertex,gemini,gemma"`).

| Provider | Purpose | Switch | Confirm |
|---|---|---|---|
| **Google Gemini API** (API key) | Embeddings and judging fallback | `GEMINI_API_KEY(S)` | Whether set in production; the free-tier terms differ from Vertex and may allow use for product improvement. **Do not leave a free-tier key set for customer data** |
| **Voyage AI** | Embeddings fallback | `VOYAGE_API_KEY(S)` | Whether set; retention terms |
| **General Compute** | Hosted open-weight judge | `USE_GENERAL_COMPUTE` (default false) | Whether used |
| **OpenRouter** | Judge route | `OPENROUTER_*` | Whether used |
| **Sentry** | Error reports, no source code intended | `SENTRY_DSN` | Whether set; scrub rules |
| **Plausible** | Cookieless site analytics, skipped on Do Not Track | `NEXT_PUBLIC_PLAUSIBLE_DOMAIN` | Whether set |

## C. Customer-chosen providers (`call_model`)

When a customer configures a connection, the prompt goes to **the provider the customer chose, with the customer's own key**. That provider is the customer's processor, not ours. For platform-paid calls (no customer key), we choose the provider and list it in the order form.

## D. Not in the MCP path

Anthropic, OpenAI and Fireworks appear in `requirements.txt` and `.env.example` for the older debate-panel product and for research runs. They do not receive data from the hosted MCP tools unless a customer configures them under section C. Remove the packages or keep this note.

## What this list does not tell you

- **Contracts.** Whether a signed DPA or standard clauses exist with each provider: undetermined. Do not state it to a customer until confirmed.
- **Retention and training.** Governed by each provider's terms. Check Vertex, Neon, Supabase and R2 first, and record the document and date here.
- **Region.** Several rows are blank because nothing in the repository sets them.
- **Payment, email or CRM vendors.** None found in code. Add any you use.

## Process to keep it current

Change this file in the same pull request that adds a provider. Customers get notice before a provider that receives source code is added (MNDA §5.2 and the MSA).
