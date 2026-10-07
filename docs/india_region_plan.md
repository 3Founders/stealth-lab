# India-region deployment (Option A)

Status 2026-10-07: **plan, nothing built.** Prices marked (V) were read from a vendor page or a price calculator in the search
results; (E) are my estimates; (R) are from memory and must be re-checked. These are planning numbers, not quotes.

## The decision

Run the whole system, including the AI calls, in Google Cloud's Mumbai region (`asia-south1`, Delhi is `asia-south2`), so
customer data does not leave India. Google Cloud is the natural cloud because the Vertex AI project, billing and the Cloud Run
plan already live there (`project-cbf120d5-4a54-4b36-968`).

**Dedicated per customer is not required for residency.** One multi-tenant deployment in Mumbai, using the row-level isolation
we already built, keeps every Indian customer's data in India. Offer a dedicated stack only to a customer who pays for isolation.
Recommended: start with one shared India stack.

Note on the law: India's DPDP Act, as I understand it, allows cross-border transfers except to countries the government
restricts; it is customers' contracts and sector rules (banking, insurance, securities, government) that usually demand India-only
processing. Have counsel confirm which applies to the first customers before promising "India-only" in a contract.

## Where we are today (not India-resident)

| Piece | Today | In India? |
|---|---|---|
| Postgres | Neon, AWS us-east-2 | no |
| Object storage | Cloudflare R2, region not set | unknown |
| Embeddings | `gemini-embedding-2` at Vertex location `global` | no: a global endpoint gives no processing-location guarantee |
| Extraction and chat | `google/gemini-3.8-flash` at `global` | no |
| Semantic judge | General Compute `gemma-4-31B-it` | no |
| Sign-in | Supabase (region not checked) | unknown |
| Error tracking and web analytics | Sentry, Plausible (optional) | no |

## The finding that decides the design

Google documents in-region ML processing for Mumbai (`asia-south1`) for the Gemini 2.5 and 2.0 models and for
**`gemini-embedding-001`**. The excerpt I could read does **not** list `gemini-embedding-2` or any Gemini 3.x model for India, and
in our own tests those two answered only at `global`. **Confirm by calling the API at `asia-south1` once Google credentials work
on a machine** (they are currently rejected with "Account Restricted").

So an India-resident deployment means:
1. Embeddings move to `gemini-embedding-001` at `asia-south1`. That is a **different vector space**, so every vector is
   re-embedded. The earlier quota problem returns: 001 had a published limit of 5 requests per minute and the increase request
   was blocked (`NOT_ENOUGH_USAGE_HISTORY`). **A quota increase in `asia-south1` must be obtained before launch.**
2. Extraction and the judge move to a Gemini 2.5 Flash model at `asia-south1`. Answer quality and cost must be re-measured.
3. General Compute leaves the customer data path (it is outside India).

## Components and monthly cost (shared India stack, then dedicated)

| Piece | Choice | Shared stack, monthly | Dedicated per customer, monthly |
|---|---|---|---|
| Postgres with pgvector (pgvector support on Cloud SQL: (R), confirm) | Cloud SQL, high availability. N4 prices at `asia-south1`: $0.0826 per vCPU-hour and $0.014 per GiB-hour for HA (V). 4 vCPU and 16 GB is about **$405**; 2 vCPU and 8 GB about **$202** | ~$405 | ~$202 to $405 |
| Storage and backups | SSD about $0.17 to $0.22 per GB-month (E); 100 GB plus backups | ~$40 to $60 | ~$25 to $45 |
| App services (MCP server, API, worker) | Cloud Run, minimum instances so there is no cold start | ~$100 to $200 (E) | ~$75 to $150 (E) |
| Network, load balancer, NAT, secrets | | ~$40 to $100 (E) | ~$30 to $80 (E) |
| Logs, 180 days kept in India for CERT-In | Cloud Logging with a retention bucket | ~$20 to $60 (E) | ~$10 to $30 (E) |
| Object storage | GCS | under $10 | under $5 |
| **Fixed total** | | **about $600 to $850** | **about $350 to $750** |

**Variable: AI calls (measured 2026-10-07).** The earlier "$0.004 per `find_ways`" was a guess and is withdrawn. Counted by
running the real `find_ways` code on the 57-goal pilot corpus (40 queries x 0 / 30 / 200 repo facts) with the embedder and
judge replaced by counting stand-ins, so call structure and prompt text are real, answer quality is not:

| Per `find_ways` | Embeddings | Judge calls | Comparisons (candidate x query) | Input tokens | Output tokens |
|---|---|---|---|---|---|
| Resolved, no repo facts | 1 | 2 (1 goal batch of 6, 1 procedure single) | 7 | ~710 | ~270 |
| Resolved, with repo facts | 1 | 2 + 1 applicability (1 procedure x 5 claims) | 7 + 5 | ~870 (+ applicability prompt, not counted) | ~270 |
| Ambiguous (2 goals both match) | 1 | 3 | 8 | ~930 | ~330 |
| No match | 1 | 1 | 6 | ~495 | ~210 |

Configured upper bound per call: 1 embedding, 1 goal batch (<= 8 candidates), 1 hierarchy batch (<= 8; not exercised, the
pilot has no hierarchy), 1 procedure judge per goal in the resolved tree (<= 8 candidates each; tree size is not bounded by
`max_depth=6`), and <= 5 applicability calls (<= 20 claims per procedure). So **2 to 3 judge calls typical, about 8 plus the
tree size at worst**. Number of repo facts (30 vs 200) did not change the call count here. With the 700-character clip a full
goal batch is about 1.9k input tokens.

At Gemini 2.5 Flash list prices of about $0.30 per million input and $2.50 per million output tokens (R: verify), including
the unmeasured applicability prompt and any thinking tokens: **about $0.0012 typical, $0.002 with repo facts, about $0.011 at
the configured worst case**. At 20,000 `find_ways` a day (600,000 a month, an assumption of 10 per person per day for 2,000
people): **about $720 a month typical, $1,200 with repo facts, $6,600 if every call hit the worst case**. This is below the
earlier guess. Still unmeasured: real tree sizes on the 20k-procedure corpus, real judge verdicts (they decide whether the
procedure stage runs at all), Flash thinking tokens, and the embedding cost. Cut further by skipping judge calls where the match
is clear (a decision, see the fallback plan) and by caching.

For comparison, the current Neon setup is $77 to $324 a month for one or two compute units (V, neon.com/pricing).

## One-time work

| Work | Effort (E) |
|---|---|
| Infrastructure as code for Mumbai: network, Cloud SQL with pgvector, Cloud Run, secrets, load balancer, logging sink | 2 to 3 person-weeks |
| Switch embeddings to `gemini-embedding-001` regional, obtain the quota, re-embed, re-check retrieval quality | 1 to 2 person-weeks, plus waiting for Google |
| Move extraction and judge to Gemini 2.5 Flash regional, re-measure quality and cost | 1 to 2 person-weeks |
| Remove non-India services from the data path (Neon, R2, General Compute, Sentry, Plausible); move or confirm the sign-in region | about 1 person-week |
| Migrate data, deploy, test, restore drill, review | 1 to 2 person-weeks |
| **Total** | **about 6 to 10 person-weeks (about 1.5 to 2.5 person-months)** |

At $5k to $10k per engineer-month that is roughly **$8k to $25k**, about a tenth of on-premises.

## Decisions needed

1. Shared India stack first, or dedicated per customer from day one? (Recommended: shared.)
2. Mumbai or Delhi as primary? (Mumbai has the models listed; check Delhi.)
3. Accept the model change (embedding-001, Gemini 2.5 Flash) and the quality re-measurement.
4. Who requests the Vertex quota increase in `asia-south1`, and with which account.
5. Do customers need the sign-in provider moved to India too? (Check Supabase's region for the existing project.)

## Risks

- The quota increase may be refused again for lack of usage history; plan a fallback in schedule, not in code (no silent
  degradation: a limited quota means a slower re-embed, not a different model).
- Quality can change with the model swap; measure before promising any savings figure.
- `gemini-embedding-001` in India may still not meet the throughput the ingestion pipelines assume.
