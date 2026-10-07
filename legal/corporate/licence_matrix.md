# Licence matrix: code, models and data

**DRAFT. A checklist, not a legal opinion.** Licences below are what I recall of each project and must be **verified against the current licence file before relying on them**. The rows marked "verify" have not been checked. Purpose: the Company must know what it may ship in a hosted service, what it must credit, and what could block a sale or a customer's review.

## How to use

1. For each component, record the exact licence and version, where it is used, and whether the Company distributes it, only runs it as a hosted service, or links to it.
2. Strong copyleft (GPL, AGPL, SSPL) is the main concern for a hosted service: **AGPL** reaches network use. Check these first.
3. Datasets and model weights have separate licences and terms from code, including restrictions on commercial use and on training.
4. Update this file when a dependency is added. Run a licence scanner in CI (for example `pip-licenses`, `licensee`, `license-checker`) and save the output.

## Software dependencies (from `backend/requirements*.txt` and `prod_frontend/package.json`)

| Component | Use | Licence (verify) | Notes |
|---|---|---|---|
| FastAPI / Starlette / uvicorn | API and MCP server | MIT / BSD | Low risk |
| MCP Python SDK | MCP server | MIT | Low risk |
| asyncpg | Postgres driver | Apache-2.0 | Low risk |
| pgvector | Vector search in Postgres | PostgreSQL licence | Low risk |
| NumPyro / JAX | Routing model fit (nightly) | Apache-2.0 | Low risk |
| NumPy / SciPy | Numerics | BSD | Low risk |
| z3-solver | Constraint checks | MIT | Low risk |
| google-genai / Google Auth | Vertex and Gemini calls | Apache-2.0 | Service terms are separate |
| anthropic / openai / voyageai | Provider SDKs | MIT / Apache-2.0 | Provider service terms are separate |
| sentry-sdk | Error reports | MIT | Optional |
| Next.js / React | Website | MIT | Low risk |
| **All remaining transitive dependencies** | | **run the scanner** | The scanner may find GPL or AGPL |

## Datasets and data sources

| Source | Used for | Terms to check |
|---|---|---|
| **SWE-rebench** (the 20k ingested procedures came from this) | Public procedures and routing observations | The dataset licence; the licences of each underlying repository; whether you may redistribute derived procedures commercially; attribution duty; anything about task text taken from GitHub issues |
| GitHub repositories and issues | Source of the tasks | Each repository's licence; GitHub terms on use of content; personal data (usernames, emails) in issues and commits |
| Public skill and prompt files (SKILL.md pilots) | Ingested procedures | Each file's licence |
| Bot dependency PRs | Ingested procedures | Repository licences |

Open questions for counsel: (1) May derived procedures be shown to paying customers? (2) Do attribution or share-alike terms apply to the Global Commons? (3) Is there personal data in the ingested text, and what is the DPDP position?

## Model weights and APIs

| Model or service | Terms to check |
|---|---|
| Google Vertex AI models | Data-use terms, zero-retention, whether outputs may be used to train competing models |
| Models in the customer's `call_model` connections | The customer's own agreements with the provider; the Company is not the licensee |
| Any open-weight model the Company hosts | The model licence: commercial use, user-count limits, acceptable-use restrictions (for example Llama community licence) |
| Embedding models | Whether embeddings of third-party content may be stored and sold |

## Name and marks

Do not use "Claude", "Codex", "GPT", "Cursor" or other vendors' marks in the product name. Describe compatibility factually ("works with Claude Code"). Run a trademark search on the final product name before filing.
