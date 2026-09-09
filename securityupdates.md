# Security Updates — Vector-Sensitive Posture, Tiered by Exposure

Companion to `ROADMAP.md` (which owns *what* gets built) and `ARCHITECTURE.md`.
This file owns **what must be true about data protection at each deployment
size**, and reclassifies one asset class whose status changed in 2025.

## Why this file exists now: the vec2vec reclassification

**Finding.** *"Harnessing the Universal Geometry of Embeddings"* (Jha, Zhang,
Shmatikov, Morris — Cornell, NeurIPS 2025, [arXiv:2505.12540](https://arxiv.org/abs/2505.12540))
demonstrates unsupervised translation of embeddings between model spaces with
**no paired data, no encoder access** (adversarial training + cycle
consistency over the conjectured universal latent geometry). Reported cosine
up to 0.96 against ground-truth target vectors; perfect retrieval-matching on
8,000 shuffled embeddings.

**Attack chain this enables against a leaked vector store:**

1. Exfiltrate embedding rows (`pg_dump`, replica, backup, log echo). Attacker
   does not need our encoder, keys, or even knowledge of which model produced
   the vectors.
2. Translate the unknown-space vectors into the space of any strong *public*
   encoder the attacker controls.
3. Apply standard embedding-inversion techniques in that space: topic /
   attribute classification of source documents, corpus-membership testing,
   partial content recovery.

**Consequence:** embedding columns stop being "derived metadata" and are
reclassified as **document-equivalent material (lossy copy)**. Everything
that carries them — `pg_dump`s, replicas, cold backups, status-page payloads,
log lines echoing tool output — inherits the classification of the source
documents. For this repository that means banking-policy procedures,
knowledge-node content, and any future customer corpora.

**What genuinely reduces risk (and what only sounds like it does):**

| Measure | Effect |
|---|---|
| Encrypt vector-bearing stores & dumps at rest | Raises bar; mandatory at every tier below |
| Never return raw embedding values through any API surface | Removes the easiest channel; cheap, do everywhere |
| Aggressive MRL truncation for anything that leaves the trust boundary | Lossier vectors are harder to invert — helps, but is degradation, not safety |
| Non-public / fine-tuned embedding models | Universal-geometry bridges lean on public encoders; a private encoder raises attack cost. Cost-raiser, **not** a guarantee |
| Treat vector-compromise incidents as document breaches | Correct response posture regardless of prevention |

---

## Tier definitions

| Tier | Users | Trigger to enter | Posture name |
|---|---|---|---|
| **T0** | 1 (founder dogfooding) | today | Sealed single-operator |
| **T1** | 2–10 | first external collaborator/dogfooder | Identified collaborators |
| **T2** | 10–100 | first design-partner cohort / pilot revenue | Pilot-production |
| **T3** | 100+ | multi-tenant GA | Governed multi-tenant |

Rules: tiers are cumulative (entering T1 keeps every T0 obligation); a tier's
items must be **complete before the user count that triggers it**, not after;
anything marked ⚠️ blocks the transition.

---

## T0 — Sealed single-operator (1 user)

*Applies:* today. *Goal:* no regret while nothing external exists.

Already shipped in-repo (verify, don't rebuild):
RLS backstop on truth tables (core-a H2), `authn.py` middleware + boot
guards, SELECT-only status surface, `.gitignore` on secrets/logs.

Add now:

1. ⚠️ **Classify embedding columns as sensitive data** in
   `backend/db/*` migration comments + `ARCHITECTURE.md` data map. One-line
   change; prevents every later "it's just vectors" mistake. **[S]**
2. ⚠️ **Encrypt vector-bearing stores at rest** — Postgres volume + any
   `pg_dump`/backup artifacts. Disk-level encryption acceptable at this tier.
   **[S]**
3. **Dump hygiene** — no unencrypted dumps outside the repo machine; backups
   live in the same secret manager as `DATABASE_URL`. **[S]**
4. **API surface audit** — assert (one pytest) that no endpoint serializes an
   `embedding` column; the status page and MCP server get explicit negative
   tests. Extends the private-edge leak-test pattern. **[S]**
5. `.env` / key hygiene stays as-is (rotation pool already in place for
   Gemini keys; GC keys single-purpose). **[existing]**

Exit: classification comment merged; encryption verified by restoring a
backup onto a clean machine; negative serialization tests green.

## T1 — Identified collaborators (2–10 users)

*Trigger:* the first human who isn't the founder can cause a row to exist.

Everything in T0, plus:

1. ⚠️ **Real authN for every non-local principal** — the Band 2.9 identity
   gate (OIDC-only acceptable) becomes a *precondition of T1*, not a Band 2
   aspiration. Local/dev modes refuse network binding. **[M]**
2. **Per-principal scoping enforced end-to-end** — every read/write path goes
   through `access.py` scope/visibility predicates (they exist; the work is
   proving coverage, including vector-similarity paths: a search issued by
   user A must never rank user B's procedures above the visibility filter).
   Contract test: cross-tenant needle stays invisible under direct similarity
   queries. **[M]**
3. **Redaction chokepoint live** — `trace_redaction.py` runs before any `[H]`
   persistence once third-party traces flow (Band 1.11's launch-blocker
   condition triggers here, at T1 not T2). **[S]**
4. **Backup access control** — restore capability limited to named
   operators; every restore logged. **[S]**
5. **Secrets out of dotfiles** — `backend/.env` values move into a managed
   secret store; the file remains as a local dev shim only. **[S]**

Exit: OIDC in front of all non-loopback surfaces; cross-tenant similarity
test red-then-green; redaction contract test proves no raw hook/tool_output
in any `[H]` table.

## T2 — Pilot production (10–100 users)

*Trigger:* external users generating real corpora; first enterprise-shaped
conversation.

Everything in T1, plus:

1. ⚠️ **Vector-breach incident runbook** — a compromise of the vector store
   is handled as a **document breach** (notify affected scope owners,
   rotate/retire exposed corpora, assess reconstruction risk per vec2vec),
   not as a metadata incident. One page, rehearsed once. **[S]**
2. **Exfiltration tripwires** — alert on bulk reads of embedding columns
   (row-count thresholds on `SELECT … embedding`), unusual
   similarity-query patterns, and dump/restore events outside maintenance
   windows. **[M]**
3. **Query-shape guardrails on `substrate_search`** — rate limits +
   result-size caps per principal; similarity endpoints return ranked
   references, never vectors, never bulk text dumps. **[S]**
4. ⚠️ **Erasure mechanics exercisable** — Band 0.9's chosen design
   (crypto-shredding or payload-eviction) must be executable at this tier:
   a scoped erasure request removes reconstructable material from vectors,
   indexes, *and* backups within SLA. This is where GDPR-shaped exposure
   becomes real (§34 vs §19 tension resolves here). **[M after 0.9]**
5. **Per-tenant vector namespaces** — HNSW/partition layout keyed by scope
   (enabled by Band 1.3 scope columns) so tenant isolation holds at the
   index level, not just the predicate level. **[M]**
6. **Truncated-vector egress policy** — any embedding leaving the primary
   trust boundary (analytics, support tooling, third-party processing) is
   MRL-truncated (≤128-dim) and documented as degraded-fidelity material.
   **[S]**
7. **Dependency/supply-chain baseline** — pinned deps, image signing for
   anything serving the API, CI check that new dependencies don't add
   vector-egress paths. **[M]**

Exit: runbook rehearsed; tripwires fire in a game-day drill; erasure drill
completes within SLA; namespace isolation proven by test at the index layer.

## T3 — Governed multi-tenant (100+ users)

*Trigger:* GA. Everything in T2, plus:

1. **Policy engine + authorization service** (Roadmap Band 5.5) — versioned
   policies evaluated at plan-time and execution-time; delegation rule
   enforced ("a procedure never grants more authority than its invoking
   user", spec §34). Identity lineage: T1 OIDC → this. **[L]**
2. **Residency as scope** (Band 5.7) — region-scoped placement of
   vector-bearing tables; cross-region moves carry the data classification
   with them. **[L]**
3. **Dedicated ANN infrastructure** (Band 5.3) — vector search splits from
   OLTP with its own access control, network boundary, and backup chain.
   **[M–L]**
4. **Formal inversion-resistance review before any public similarity API** —
   external-facing semantic search gets a written review covering: encoder
   privacy posture, truncation levels, rate/shape limits, and residual
   vec2vec-style translation risk. Re-reviewed on any embedding-model change
   (pairs naturally with Band 1.6 embedding-provenance stamps). **[M]**
5. **Crypto-shredding exercised at scale** — Band 5.6 mechanics proven
   against partitioned, replicated stores; shredding drill includes the ANN
   index and cold tier. **[M]**
6. **Continuous verification** — quarterly: restore-drill from encrypted
   backups, tripwire coverage review, secret-rotation audit, and a re-run of
   the negative-serialization test suite against every new endpoint. **[S]
   per quarter**

Exit: all T2 exits hold under load; policy-engine decisions are replayable
from logs (consistent with the substrate's own append-only discipline);
residency placement provable per scope.

---

## Mapping to existing Roadmap items (no duplication)

| This file | Roadmap item | Relationship |
|---|---|---|
| T0.1 classification | — (new, feeds ARCHITECTURE.md data map) | prerequisite framing |
| T0.2/T0.3 encryption & dumps | — (new; complements Band 6 hygiene) | new obligation |
| T0.4 serialization negatives | private-edge leak tests (App. C #9) | extends pattern |
| T1.1 authN | Band 2.9 identity gate | **same work; tier-triggered deadline** |
| T1.2 scoping coverage | `access.py` builders; Band 1.3 scope columns | proof-of-coverage work |
| T1.3 redaction | Band 1.11 chokepoint | **same work** |
| T2.4 erasure | Band 0.9 decision → Band 5.6 mechanics | execution deadline pulled to T2 |
| T2.5 namespaces | Band 1.3 + 4.2 partitioning | index-layer expression |
| T3.1 policy engine | Band 5.5 | identical |
| T3.2 residency | Band 5.7 | identical |
| T3.3 dedicated ANN | Band 5.3 | identical |
| T3.4 inversion review | Band 1.6 embedding stamps | review trigger = stamp change |

Maintenance rule (mirrors ROADMAP.md): any PR adding an endpoint that can
carry embeddings, or changing the embedding model, must touch the relevant
tier row here **in the same change** — an unmapped vector path is a bug, not
a footnote.
