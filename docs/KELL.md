# kell — the whole picture

**One document, everything we have decided and found. 2026-11-20.**

Read this top to bottom and you have the product, the research, the format, the market, the
design and the launch. It supersedes the separate notes it was composed from (listed in
§12), and where it disagrees with them, this wins.

---

## Contents

0. The thesis in one page
1. **Atomic reusability** — the main thing
2. The format: L-HTN over a semantic core
3. Reproducibility, review and research
4. The procedure page
5. Frontend: what to learn, how not to get stuck
6. Market: what exists, what is unoccupied
7. Pricing and the open-source stance
8. The YouTube channel
9. What is already built
10. What to build, in order
11. Risks, stated plainly
12. Further reading in this repo

---

## 0. The thesis in one page

Agent architectures are published faster than they are checked. A paper claims a
decomposition, a topology or a protocol is better; the claim is not separable from the
scaffold it was measured in, so nobody can tell whether the architecture caused the result or
the harness did. The format work is real and converging — natural-language procedures
(L-HTN), RDF/SHACL semantics, versioned skill registries — but it is **descriptive, not
evidentiary**.

kell's bet is that **the atom is not a prompt, a skill or an agent — it is a versioned,
verified, reusable procedure that can be plugged in anywhere and checked by anyone.**

Three consequences, and everything else follows from them:

1. **Atomic reuse** (§1) — a procedure is a component, not a script. It is pinned by
   content hash, never dissolved into its consumer, and independently versioned.
2. **Reproducibility** (§3) — a result is an object with a pinned harness, not a number in
   a PDF.
3. **Open contribution** — anyone can contribute to a goal; what is earned is *liveness*,
   not visibility.

Measured context for why this is worth doing:

- Framework choice alone moves latency **>100×**, planning accuracy **−30%**, coordination
  success **>90% → <30%** ([MAFBench, arXiv 2602.03128](https://arxiv.org/abs/2602.03128)).
- **26 of 55** agentic evaluation papers report no chance-corrected inter-rater statistic
  ([arXiv 2608.00794](https://arxiv.org/html/2608.00794v5)).
- **None of eight** agent-benchmark papers disclose inference cost
  ([arXiv 2605.21404](https://arxiv.org/abs/2605.21404)).
- Symbolic HTNs hit **0%** under lexical change; L-HTNs stay ~95%
  ([Smith & MacLellan, ACS 2026](https://tail.cc.gatech.edu/files/smith-acs-2026.pdf)).

---

## 1. Atomic reusability — the main thing

This is the requirement everything else serves, so it gets the precise treatment.

### 1.1 What "atomic" has to mean

A procedure is **atomic** when all six of these hold. Each is separately testable, which is
what makes this a spec rather than a slogan.

| # | Property | Test |
|---|---|---|
| 1 | **Pinned by content, not label** | Two uploads labelled `1.4.0` with different bytes are refused or get distinct identities |
| 2 | **Never dissolved into its consumer** | The parent's graph still contains a node that *is* the sub-procedure, at its own version |
| 3 | **Independently replaceable** | Bumping `A@2.3.0 → A@2.3.1` changes only that node; the parent is untouched |
| 4 | **Independently verifiable** | Evidence attaches to `A@2.3.0`, and survives A being superseded |
| 5 | **Composable without ambiguity** | Two sub-procedures claiming the same step resolve deterministically, not by luck |
| 6 | **Carries its own contracts** | `applies when` and `leaves` travel with the atom, so a consumer can check compatibility |

### 1.2 Where we stand against that

| Property | Today | Gap |
|---|---|---|
| 1 Pinned | ✅ `hash_procedure_version()`, exact-version refs, versionless refused | Label and hash are both tracked but not enforced equal on write |
| 2 **Not dissolved** | ❌ **`expand_procedure_steps` splices the referenced sub-procedure's steps into the parent and the reference node "dissolves into its expansion"** | This is the one real conflict with atomicity — see §1.3 |
| 3 Replaceable | ✅ exact pin, `supersede_procedure` appends | — |
| 4 Verifiable | ✅ evidence pins `target_version`; bitemporal lineage | — |
| 5 Unambiguous | ⚠️ cycle detection + depth cap exist | **No deterministic collision policy** when two atoms claim the same step |
| 6 Contracts | ⚠️ `preconditions`/`expect_effects` columns exist | Not NL-first, not surfaced at composition time |

### 1.3 The one thing to change

`procedure_graph.py` currently does:

> *"splices the resulting subgraph into the parent at the referencing node's position — the
> reference node itself dissolves into its expansion; nothing downstream should be able to
> distinguish a composed graph from a hand-authored one of the same shape."*

That was deliberate: it makes execution simple, because a flat graph needs no special
handling. **But it destroys atomicity.** After expansion you cannot tell which steps came
from which atom, so you cannot swap one atom, verify one atom, or attribute a failure to one
atom. Dissolution is convenient for the executor and fatal for the product.

**The fix is not to remove expansion — it is to make it a projection, not a rewrite.** Keep
the composed graph hierarchical at rest and flatten only at execution time, carrying
provenance on each flattened node:

```
Composed graph (stored)              Flattened (execution)
  node A@2.3.0  ────────────────►    step 3  {origin: A@2.3.0, sub_step: 1}
  node B@1.4.7  ────────────────►    step 4  {origin: A@2.3.0, sub_step: 2}
                                     step 5  {origin: B@1.4.7, sub_step: 1}
```

Every flattened step names its atom and its position within it. Then:
swap = re-project from a new pin; verify = filter evidence by `origin`; attribute a failure =
it already identifies the atom.

This is the NOMOS lesson applied here: *a rule is data, not code, and keeping provenance on
each element is what makes verification cheap*. It is also what `deps.dev` does — the version
stays on the node, visibly, rather than being erased by resolution.

### 1.4 Collision policy

The research is explicit that an aggregate must fix this deliberately: *"choosing neither is
not [defensible]"*. Adopt **declaration order wins**, recorded on the composed graph, so the
same inputs always resolve the same way. Last-wins-at-execution is the alternative; either is
fine, silence is not.

### 1.5 Why atomicity is the commercial argument too

A single procedure as text is copyable. **A pinned, verified atom graph is not**, because the
value is in the combination of identity, evidence and replaceability. That is the difference
between selling a document and selling a component, and it is the same reason `npm` lockfiles
have value over reading source.

---

## 2. The format: L-HTN over a semantic core

### 2.1 The evidence for natural language

L-HTN ([Smith & MacLellan, ACS 13 2026](https://tail.cc.gatech.edu/files/smith-acs-2026.pdf))
compares symbolic HTNs, unconstrained LLMs and linguistic HTNs on the same tasks:

| Environment variation | Symbolic HTN | Unconstrained LLM | **L-HTN** |
|---|---|---|---|
| Baseline | 100% | ~50–100% | ~100% |
| Lexical change | **0%** | — | **~95%** |
| Reduced information | **0%** | ~70% | **~99%** |
| Spatial | 100% | ~50% | 47–49% |

**A procedure written in predicates is invalidated by a rename. A procedure written in prose
survives it.** That is why our primitives are prose.

Its stated limitation is our opening:

> *"evaluating whether linguistic preconditions hold remains a language-model judgment and is
> not independently verified by the planner."*

We have evidence. They have none.

### 2.2 The four primitives

```
Way "Refund a captured payment"
  goal:          "The customer's charge is reversed and the ledger agrees"
  arguments:     [ "order id" — "the order the customer paid"
                   "amount"   — "in paise; partial allowed" ]
  applies when:  "The payment is captured and not already refunded"
  ⟶ step 1 [automated]  title: "Find the captured payment"
                         does:  "Look up the order and confirm it is refundable"
                         leaves:"The payment id and refundable amount are known"
                         tool:  locator
  ⟶ step 2 [human]      title: "Confirm the refund is not a duplicate"
  ⟶ step 3 [automated]  title: "Issue the refund"
                         uses:  → Way "Refund via gateway" @1.4.7   ← the atom
  result:        "The charge no longer appears as owed"
  if it fails:   "Already refunded" / "The gateway refused"
```

Three fields are new and are the highest-value additions: **`applies when`** (the NL
precondition — what makes a Way routable and falsifiable), **`leaves`** (the effect, giving
the state chain), and **`uses`** (the atom edge, already stored as `subprocedure_ref`).

Everything that must be machine-checkable stays typed and closed: the procedure identity, its
version, its content hash, and the locator. **Prose is the source; the shape is a guard.**

### 2.3 The semantic core, and why it is not optional

An IRI means one thing for every consumer; a JSON key means whatever the last developer
thought. The [IETF agent-semantics draft](https://www.ietf.org/archive/id/draft-zhang-dmsc-ioa-semantic-interaction-03.html)
supplies the vocabulary (`ioa:Capability`, `ioa:Intent`, `ioa:Task`) and two rules we adopt
verbatim:

- **Three-valued compatibility**: Compatible / Incompatible / **Indeterminate**. "Could not
  establish" is an answer, not a failure.
- *"Descriptive similarity does not establish equivalence."* — which is exactly the mistake a
  similarity threshold makes at 0.97 today.

OWL describes; **SHACL validates**. The two are not interchangeable: OWL under the open-world
assumption will happily "explain away" an agent error with an `owl:sameAs` link rather than
reject it.

### 2.4 Identity, versions and the skill registries

Adopt the model already proven by Google Agent Registry and MLflow:

```
Publisher → Skill → SkillRevision   (revisions immutable, digest-addressed)
```

with lifecycle `draft ⇄ active ⇄ deprecated → deleted`. One finding justifies the digest
outright: [Skill Provenance](https://skillprovenance.dev/) measured **66 of 1,210 (5.5%)**
skill instruction changes with **no version movement**. A version label alone does not tell
you whether the bytes changed.

---

## 3. Reproducibility, review and research

Full treatment in `docs/reproducibility.md`. The argument in brief.

### 3.1 One root cause

**An agent result is not a first-class object.** It is a number in a PDF. Pinning,
replication and adjudication are all bookkeeping over an object that does not exist.

### 3.2 Three objects

**`harness_digest`** — one hash over the seven unpinned axes: scaffold, system prompt, tools,
stopping rule, environment, budget, sampling. Two rules earned from the literature:
`run_date` is mandatory (closed aliases are silently re-pointed) and the environment is a
**content-addressed image digest, never a tag**.

**`claim`** — with three rules that separate it from a forum:
- `varies` names **one** axis (topology, protocol, decomposition, harness, model or budget)
- `falsifier` is **mandatory** — this is STREAM criterion 6(ii), already a published standard
- **`indeterminate` is a real outcome**

**`replication`** — not a comment. Independence **enforced, not asked** (ReScience: *"you
cannot submit the replication of your own research"*); **failure is publishable**; the
reviewer must **run it**, not read it.

### 3.3 What the field already documents, and we operationalise

| Finding | Source |
|---|---|
| **26 of 55** agentic papers report no chance-corrected inter-rater statistic | arXiv 2608.00794 |
| A **double measurement confound**: scaffold owns execution-critical decisions, and scoring may not reflect correctness | arXiv 2609.09218 |
| **Scaffold ownership is "an uncontrolled axis wherever we probed it"** | same |
| **τ-bench counts empty responses as successes**; errors up to **100% relative** | ABC, NeurIPS |
| An unrepaired oracle made **94.3% read as 65.5%**, and changed which agent was strongest | arXiv 2610.16067 |
| **None of eight** papers disclose cost; mean disclosure **0.38** | arXiv 2605.21404 |
| Terminal-Bench defects in **~30%** of an already-red-teamed benchmark; **+10pp** after repair | arXiv 2611.08417 |

### 3.4 The literature that validates "no worthless debate"

Multi-agent debate measurably **lowers** accuracy, and this is the empirical basis for the
platform not hosting argument:

| Task | Group | Before | After | Δ |
|---|---|---|---|---|
| MMLU | 1× Llama + 2× Mistral | 40.0 ± 2.1 | 28.0 ± 1.2 | **−12.0** |
| MMLU | 3× Mistral-7B | 33.6 ± 1.8 | 24.4 ± 2.9 | **−9.2** |
| CommonSenseQA | 2× Llama + 1× Mistral | 58.2 ± 3.8 | 50.2 ± 3.9 | **−8.0** |

Correct→incorrect flips exceed the reverse; social pressure dominates by round 2; adding a
correctness payoff did not help ([arXiv 2509.05396](https://arxiv.org/html/2509.05396)).
Competitive debate collapses to *cheap talk*; consensus-seeking filters out informative
disagreement ([arXiv 2510.20963](https://arxiv.org/html/2510.20963)). One adversarial agent
lowers accuracy **10–40%** and raises wrong-answer consensus **>30%**
([Sci Rep 2026](https://doi.org/10.1038/s41598-026-42705-7)).

### 3.5 Open contribution, and what "instantly applied" must mean

| Event | Latency | Gate |
|---|---|---|
| Anyone contributes a Way to a Goal | **immediate**, as `proposed` | signed-in identity |
| Runnable in **your own** deployment | immediate, if you opted into unverified | your flag |
| Runnable by an **autonomous runner** | only after `BEST_VERIFIED` | hard rule |
| Replaces the incumbent at a plug-in point | after a **paired comparison** wins | McNemar |
| Propagates to existing deployments | **next version resolution, never mid-run** | invariant #2 |

Contribution being open and promotion being earned are not in tension — that is what makes
open contribution safe enough to allow.

### 3.6 The failure record that says why

**Polsia's own founder**, on his autonomous company: a broken support route left **20 Stripe
disputes unanswered** — *"nobody found it because nobody was looking"*; and *"exception
handling must have a human backstop."*

**One company, 43 documented incidents**: **21 of 43 were not the task done wrong** but shared
state the next session could not see; **18 repeated**; of the 10 that recurred after a rule was
written, **8 had only a written control, no code**. Two incidents are the whole thesis: *"the
control was written, and even existed as code. **Nothing ran it.**"*

Every one of those maps to a mechanism in this design. That is not a coincidence — it is the
same problem domain.

---

## 4. The procedure page

Full treatment in `docs/procedurepage.md`. The essentials.

**Yes, a separate page — but neither YouTube's model nor n8n's.** A Way is closest to **an npm
package that ships with a benchmark result**. The decisive finding: *no* existing page family
carries versioning, the Goal and verification together.

| | Versioning | Goal | Verification |
|---|---|---|---|
| n8n / Zapier templates | ❌ | partial | ❌ |
| YouTube channels | ❌ | ❌ | ❌ |
| npm / deps.dev | ✅ | ❌ | ❌ |
| Papers with Code / LMArena | ❌ | partial | ✅ |

**The sections, in the order a sceptical buyer asks:**

1. Identity — name, version
2. **Output** — when a sample exists (a reader who doesn't believe it works shouldn't read how)
3. **Price — one output** (present only when the Way is not free)
4. What it does
5. The decomposition — graph with **versions on the nodes**
6. Version history — with a `**Breaking:**` marker that says what breaks
7. Provenance — the **stored record**, on kell's domain, with a fingerprint of the page's copy
8. Reviews — one person's verdict, kept **apart from the evidence** (a rating never moves a success rate)
9. Sideways navigation, and a right-hand rail of related Ways

Four rules the research insists on:

- **Never a bare percentage.** LMArena reports score **+ ± interval + rank spread** and tells
  readers to compare spreads.
- **Name the benchmark and the split.** Papers with Code calls mixing splits *"the most
  common failure mode"*.
- **Borrow GRADE's wording ladder** — "results in" only at high certainty; otherwise "likely",
  "may", "the evidence is uncertain".
- **A badge is worthless unless checkable.** npm provenance is the model: the record is served on
  kell's own domain and the page shows a fingerprint of the bytes it rendered. The fingerprint is a
  consistency check, not a signature, and the copy says so.

**Discussion that is opinion, not evidence**: reviews are a separate table and a separate section.
A 1-5 rating never feeds a capability number, an applicability decision or a ranking — those come
from recorded runs. One review per person per Way; posting again edits it. The three doors remain —
*ran it* (one click → evidence row), *report* (exists), *propose an improvement* (anchor + evidence
+ what would change → a new version). The improvement path already exists in the economy
(`submission_type='improvement'`) and `IMPROVEMENT_REWARD` (20) is already **double**
`NEW_PROCEDURE_REWARD` (10).

**The metric that says it is failing:** not comments per Way. Watch **runs per Way** against
**comments per Way**. If comments rise while runs stay flat, the page has become a forum.

---

## 5. Frontend: what to learn, how not to get stuck

Full treatment in `docs/frontend.md`.

**Award-winning sites are a bad model for a product site.** Across 23 recent Awwwards
winners, **usability was the lowest score on all 23**, and accessibility lowest or joint-lowest
on **21 of 23**. Four of twelve send **under 30 words of HTML** before JavaScript runs.

**Worth reading:** `raphaelbleier/design-inspiration-skills` (50+ winners measured as tokens
and motion specs), `juliangarnier/anime`, Bruno Simon's folios, `shadcn-ui/ui` (for the
distribution model), `vercel/commerce`.

**The traps, with evidence:**

1. **shadcn's unsolved problem** — copied source means fixes arrive as *conflicts, not
   merges*, with no per-component semver. Copied components get no business logic.
2. **Shared packages ≠ shared apps** — a 3-year Next.js monorepo postmortem found the problems
   were organisational, not technical. **Duplicate first, extract on the third.**
3. **Tokens are a gate, not a migration** — *"if a global rebrand takes a find-and-replace on
   six files, your tokens are working."*
4. **FT's four causes of decay** — scope unclear, architecture unreadable, releases scary,
   history insufficient. Their fix: *"small packages with a one-or-two-sentence README."*
5. **Framework coupling** — keep domain logic out of React so an upgrade stays a chore.

**Where our site actually stands** (measured):

| Check | Result |
|---|---|
| Token block | ✅ full vocabulary in `:root` |
| `prefers-reduced-motion` | ✅ **9** blocks — better than most award winners |
| z-index values | ⚠️ **8 distinct**, no scale |
| Hard-coded hex outside tokens | ⚠️ **38 occurrences** |

The 38 hex values are the one number that will hurt; worth fixing while the site is small.

---

## 6. Market: what exists, what is unoccupied

### 6.1 Agent memory and procedures

| Company | What | Pricing | Stage |
|---|---|---|---|
| **Memorable** (YC S27) | procedural memory, workflow graph | per session | 2 people |
| **Skillsync** (YC W26) | portable sessions + skill sharing | unlaunched | $500K |
| **Mem0** | memory layer | Free → $19 → $249 | **$24M** |
| **Letta** | agent harness, MemFS | $20/mo + $0.10/agent | **$10M** |
| **Zep / Graphiti** | bi-temporal graph — *our model* | $125 / $375 | YC W24 |
| **Cognee** | graph memory | $1/1M tokens | $7.5M |
| **Supermemory** | context engine | $19 / $100 / $399 | ~$3M |

**Memorable is 12 months ahead on capture and 2 people deep.** Its four layers mirror ours
(traces → synthesis → **graph assembly** → retrieval) but it has **no verification and no
marketplace**. That is the opening.

### 6.2 Skills, registries and marketplaces

| | Model | Numbers |
|---|---|---|
| **Orthogonal** (YC W26) | pay-per-call, curated skills | $0.001–$1.00/call |
| **AgentMuxer** (YC) | "OpenRouter for tools" | **zero markup** + 5% |
| **Smithery** | MCP registry | $300/mo → $10 → **acquired by Arcade** |
| **Arcade** | enterprise tool runtime | **$60M Series A** |
| **SquidBay / AgentMarketplace / Capafy** | skill sales | **90/10** and **80/20** |
| **Veri** | **verified agents** | 85% to builder |

**The GPT Store warning**: 3M created, **159K active (5.3%)**, ~**$0.03/conversation**,
**median creator $47/month**, top 1% take 78%; revenue program **dropped May 2024**; custom
GPTs **retire 11 Dec 2026**.

### 6.3 The open-Replit / open-Polsia question

**There is no open Polsia, and "open Replit" is crowded.** OpenCode ~225k★, OpenHands
~92.6k★, Cline ~70.9k★, Dyad ~22.2k★, bolt.diy ~20k★ — and *"not one of them ships the
backend"* (Hyperlambda, Sep 2026).

Polsia is an **autonomous company runner** (roles, daily cycles, Claude Agent SDK). Replit
Agent is an **in-IDE builder that ships to hosting**. Neither is where we should compete.

**The whitespace is the third leg:** not the builder, not the autonomy, but **trust** —
"verified outcomes" is an empty column for every entry in the table. And it is what makes the
other two defensible, because *"our agents' procedures are verified"* cannot be copied by
copying code.

---

## 7. Pricing and the open-source stance

### 7.1 The meter

**Verified resolution.** One unit = one attested successful use of a pinned Solution against
its Benchmark, with evidence recorded. Not a call; not an attempt. You already produce this
(`procedure_usage_events.outcome_state`, Wilson lower bound).

### 7.2 The structure

| Tier | Price | What |
|---|---|---|
| **Open** | ₹0 forever | All Goals, all Ways, run on **your own models** |
| **Verified** | ₹ per verified resolution | Attested runs, evidence receipts, Wilson score, pinned lockfile |
| **Registry** | platform fee + included resolutions | Private registry, dependency lockfile, audit export, SSO |
| **Marketplace** | **20% take rate** | Third-party packs |

**Give away models and procedures; charge for the attestation.** This is Mem0's playbook —
give away compute, charge for the platform — and it works for us because inference is a
commodity we already route cheaply.

### 7.3 Two hard billing rules, from the failure record

1. **Charge only on verified completion.** Polsia's churn and 2.2/5 Trustpilot trace to
   *"failed tasks consuming credits"*.
2. **Every autonomous action needs an external receipt** — a commit sha, a delivery receipt, a
   Stripe payout, never an internal status label.

---

## 8. The YouTube channel

Net-new; nothing in the repo covers it. The channel is not marketing for the product — it is
**the corpus with a face**, and it should be designed so each video produces an artefact that
outlives it.

### 8.1 Why a channel, given everything above

The research venue needs a public on-ramp, and the strongest existing distribution mechanism
for technical claims is a person explaining a measurement. The channel's job is to convert
**a claim** into **a reproducible demonstration**, and the video is the proof-of-life for the
page.

### 8.2 The format: one claim, one measurement, one artefact

Every episode follows the same shape, so it becomes recognisable:

| Beat | Content | Duration |
|---|---|---|
| 1. The claim | "Framework X says their approval gate blocks actions" | 30s |
| 2. The setup | The pinned harness, on screen — `harness_digest` visible | 60s |
| 3. The measurement | Run it. Show the raw count, the discordant pairs, the interval | 3–5 min |
| 4. What it means | What would have falsified this; what did not happen | 60s |
| 5. The artefact | The Way, the claim, the reproduction page | 30s |

**Rule: never show a percentage without its `n` and interval on screen.** The channel's
credibility *is* the column that every paper leaves blank.

### 8.3 The first eight episodes

Each is a published finding we can re-derive, so the first videos need no original research:

1. **"Stop does not mean stop"** — the sibling leak: 5 of 6 frameworks, 215/1,200 runs. The
   strongest opener because it needs **no LLM** and refutes documented behaviour.
2. **"Debate makes agents worse"** — −12pp on MMLU, with the flip counts.
3. **"Why two papers can't agree"** — harness drift, demonstrated by running the same Way
   under two scaffolds.
4. **"The 94.3% that was really 65.5%"** — an oracle that reset a database.
5. **"No paper tells you what it cost"** — 0 of 8 disclose inference cost; we show ours.
6. **"Rename a function, break the agent"** — symbolic 0% vs L-HTN 95% under a lexical change.
7. **"The control was written. Nothing ran it."** — the 43-incident ledger.
8. **"What a verified procedure actually is"** — the atom, end to end.

### 8.4 Rules that keep the channel honest

- **Negative results get episodes.** Two of the eight are failures. A channel that only
  publishes wins is the GPT Store.
- **Every number is on a page.** Each episode links the claim, the pinned runs, and the
  reproduction command. If a viewer cannot reproduce it, the episode gets a correction
  pinned.
- **No "award-winning" hype.** Show the count, the interval, and what did not happen.
- **Corrections are episodes**, not silent edits.
- **One claim per video.** Two claims is two videos, for the same reason `varies` names one
  axis.

### 8.5 Cadence and the compounding asset

Weekly is right only if the artefact keeps up; fortnightly with a real measurement beats
weekly with a demo. The compounding asset is not the channel — it is the **claim corpus** the
channel feeds. By episode twenty there are twenty claims, each with pinned runs and a page,
and that corpus is the thing nobody else has.

---

## 9. What is already built

Reading the repo rather than the docs, these are real and load-bearing:

| Capability | Where |
|---|---|
| Content fingerprint of a procedure version | `app/execution/plans.py::hash_procedure_version` |
| Versionless references refused (invariant #2) | `validate_procedure_ref` |
| Self-describing executions (`procedure_id` + version denormalised) | `db/23_plan_persistence.sql` |
| Durable, resumable runs with per-node state and fencing | `db/36–37`, `durable_graph.py` |
| Replay | `app/execution/replay.py` |
| Wilson-bound ranking (`BEST_VERIFIED_FLOOR = 0.70`) | `app/services/product_model.py` |
| `proposed` vs `active` gate | `app/api/goals.py:590` |
| Bitemporal supersession | `db/18`, `supersede_procedure` |
| Improvement economy (20 vs 10, parent share 0.25) | `app/economy/constants.py` |
| Bayesian routing with posteriors and priors | `db/120–123`, `app/routing/` |
| Referral + refund mechanisms | `db/212`, `db/215`, `wallet_refunds.py` |

**Not built:** `claim`, `replication`, `harness_digest`, a deterministic collision policy, and
hierarchical graph retention (§1.3).

---

## 10. What to build, in order

1. **Hierarchical retention** (§1.3) — stop dissolving atoms; carry `origin` on flattened
   nodes. This is atomicity, and it blocks nothing else.
2. **`harness_digest`** — the column, the hash, and refusal to promote without it. *Smallest
   change with the largest effect.*
3. **Complete the run record** — cost, tokens, tool calls, wall clock, failure class. None of
   eight papers do this.
4. **Validate the schema against published findings** — express SILO-BENCH's coordination gap,
   L-HTN's lexical robustness and Stop Means Stop's sibling leak as claims. **If the schema
   cannot express a published finding, the schema is wrong.**
5. **`claims` + `replications`** — status machine, mandatory falsifier, enforced independence.
6. **Paired comparison reporting** — reuse the McNemar machinery; always report discordant
   pairs.
7. **The procedure page** — evidence panel first; it needs nothing that is not already
   computed.
8. **Champion/challenger** — the incumbent and a challenger both run; promotion on evidence.
9. **Episode one** — the sibling leak, because it needs no LLM and refutes documentation.

---

## 11. Risks, stated plainly

1. **Dissolution is load-bearing.** Changing §1.3 touches the executor. It is the right change
   and it is not small.
2. **A frontier lab could beat us to it** by extending STREAM from *reporting* to
   *replicating*. Moving early on `harness_digest` matters more than moving broadly.
3. **The claim object could become a second blog.** The guard is the existing
   `proposed → active` shape: no falsifier and no pinned run, no `open`.
4. **Replication costs runs, not reading.** ReScience uses volunteers; we may need funded
   reviewers or credits. `credits.py` is half-built.
5. **Polsia's numbers are contested** — $500k/mo conflicts with the same interview's $4.5M
   ARR, and the company's own API reported **84.6% churn**. Do not anchor on the headline.
6. **Do not compete on the builder.** Six projects with 20k+ stars are there; the trust leg is
   empty.

---

## 12. Further reading in this repo

| Document | What it holds |
|---|---|
| `docs/reproducibility.md` | The three objects and the build order, in full |
| `docs/open_agentic_literature.md` | Terminology, questions, the sweep, the 17 Perplexity prompts |
| `docs/procedurepage.md` | The page anatomy, evidence rules, discussion design |
| `docs/frontend.md` | Repos worth reading, six traps, measured state of our site |
| `docs/referral_legality_2026-11.md` | The India legal briefing behind the referral scheme |
| `MONETIZATION.md` | The original tier architecture |
| `docs/billing.md` | Payments, refunds, the plans surface |
| `ARCHITECTURE.md` | Historical; read for the HTN/graph rationale only |

---

## 13. One sentence

**A Way is an atom — pinned by hash, never dissolved, independently verifiable — and
reproducibility, review and research are the same problem: an agent result is not an object
until it is one.**
