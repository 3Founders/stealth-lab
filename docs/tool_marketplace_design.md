# Tool marketplace: a tool is a goal with a procedure attached (design, 2026-11-06)

Status: design only, nothing built. Owner decisions are marked **[owner]**. The revenue share, fees and prices are not chosen here.

## The idea

Someone puts up a tool for a specific prompt. Kell stores the prompt as a **Goal** and the tool as a **Way** (procedure) attached to it. A person who asks for that goal gets that tool back, as published. Publishing works from two doors that write the same records:

1. the MCP tool `submit_way` (for agents), and
2. a **website page** ("Publish a tool") for people.

There is one store, one review pipeline and one retrieval path. The website is not a second system.

## Records

| Record | What it holds |
|---|---|
| Goal | The prompt as an abstract goal: what is to be done, how success is checked, the context that decides how. Scope and provenance on every row (hard rule 2). |
| Way | The procedure. For a tool, its step is "call tool X with this argument template". |
| Tool binding (new) | A validated field on the Way: a remote MCP server URL, or a package, plus a manifest hash. `submit_way` today allows no links, so this is a separate field with its own checks, not free text. |
| Check | A machine check that runs against the tool's real output. A paid listing must have one. |
| Listing terms (new) | Per-call price, floor and cap, currency, creator, status (draft, in review, live, paused, removed). |
| Evidence | Outcomes of real calls, through `backend/app/execution/evidence.py` only. |

## Retrieval ("retrieved exactly like that")

1. **Exact or near-exact goal match** returns the creator's listing as published.
2. If nothing matches closely, **semantic nearest goals** from the code tree (`semantic_codes.py`).
3. Several tools for one goal are ranked by recorded outcomes. Price and cost break ties.
4. Cluster routing (Avengers-Pro style, see below) chooses the model for the surrounding steps when no tool matches. It does not choose the tool.

## The website flow (routing_kell_frontend, assumed to be the live site)

1. **Sign in**, then "Publish a tool" (new page under the account area).
2. **Describe:** the prompt (goal), what success looks like, example inputs and outputs.
3. **Connect the tool:** a remote MCP server URL or package. Kell introspects it in a sandbox (`tools/list`), shows the discovered tools, and the creator picks the one that serves the goal.
4. **Add a check:** a test case the creator supplies, or a generated one they approve.
5. **Set terms:** per-call price within the floor and cap **[owner]**.
6. **Review:** automated screening (manifest hash, description against behaviour, malicious-pattern checks), then a human review for paid listings **[owner]**. A changed manifest means re-review.
7. **Go live.** Creator dashboard: calls, pass rate, earnings, reviews, pause and remove.

The same steps are an API (`/v1/tools/...`, new), and `submit_way` calls it. The browser holds no keys and no authorisation logic: the backend answers 401/403 (repo rule F-014).

## Money

- Buyers hold one **credit wallet**. Every tool call, kell's own or third party, charges credits through the existing caps.
- An **append-only ledger** records purchases, charges, creator earnings, the platform share, refunds and payouts, each tied to the call record.
- **Razorpay** is the processor. Credits are bought with Razorpay Checkout; webhooks are signed, idempotent and reconciled (`billing_reconcile.py`). Creator payouts use **Razorpay Route** linked accounts (KYC at onboarding). Route moves money per payment, so credits stay in our ledger and Route is used at payout time. To confirm with Razorpay: holding funds, cross-border payouts, GST and tax handling **[owner]**.
- Failed calls refund automatically. A dispute window precedes payout.

## Safety

- Tools that send, post, pay or delete need a per-call confirmation.
- Per-user and per-tool daily spend limits; a balance check before the call.
- Listing review follows OWASP's third-party MCP guidance: trust-minimise, validate manifests, sandbox introspection. Papers on tool poisoning and description-code inconsistency show real attacks, so descriptions are compared with behaviour.
- A removed or malicious tool is paused at once; earnings from it are held.

## Model routing for goals with no tool (Avengers-Pro adapted)

Avengers-Pro (arXiv 2508.12631): embed queries, cluster them, profile each model's accuracy and cost per cluster, score `alpha * perf_norm + (1 - alpha) * (1 - cost_norm)` with min-max normalisation inside each cluster, route to the model with the best score summed over the top-k nearest clusters.

Adaptation for kell:

- Cluster **abstract goals** (project specifics stripped), reusing the code tree.
- Each cluster has a **micro-benchmark** of graded anchor tasks. New models are probed on them (as `probe.py` does for one coding set).
- Micro-benchmarks are tiny, so raw per-cluster rates are noisy and min-max normalisation amplifies the noise. Use shrinkage toward the parent cluster and the global model ability, with credible intervals, and choose the cheapest model whose lower bound clears the pass threshold. The existing hierarchical Bayesian model in `backend/app/routing/` is the starting point; this has not been read in full for this doc.
- Evaluate offline on recorded outcomes against the current router before any switch (Band rule: exit criteria are numbers).

## Open questions

1. Is `routing_kell_frontend` the live website, or `prod_frontend`? (The 2026-10-19 repo map says `prod_frontend`; recent work is in `routing_kell_frontend`.) **[owner]**
2. Revenue share, fees, price floor and cap. **[owner]**
3. Human review for paid listings: who, and how fast. **[owner]**
4. Razorpay Route: cross-border, fund holding, GST. **[owner]**
5. Terms: creator agreement, buyer terms, takedown policy (legal pages live in `prod_frontend/content/legal/`).

## Not in this design

Open peer review of procedures, the HTN semantic-web protocols and E2EE are separate designs; they connect later through the same Goal and Way records.
