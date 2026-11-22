# Reproducibility, Review, Research — the product

**What kell is for. 2026-11-20.**

Three words that are usually three different products: a benchmark suite, a peer-review
journal, an eval harness. They are one problem — *can a claim about an agent be checked by
someone else* — and kell already has most of the machinery. This document says what the
problem is, what exists, what is missing, and the order to close it in.

---

## 0. The problem, precisely

Three failures, one root:

| Failure | What it looks like | Root cause |
|---|---|---|
| **Reproducibility** | Two papers report different numbers for "the same model on the same benchmark" and neither can say why | The **harness is unpinned**. Every result is a function of `(model, scaffold, tool-inventory, stopping rule, environment, budget, seed)` and papers pin one of the seven |
| **Review** | Reviewers check plausibility, not claims; debate measurably *lowers* accuracy | There is no artefact to review against. A claim is prose; prose cannot be run |
| **Research** | Findings don't accumulate — a result from 2024 isn't comparable to one from 2026 | Nothing carries forward. No claim, no replication, no negative results, no expiry |

**The root is the same in all three: an agent result is not a first-class object.** It is a
number in a PDF. Everything that follows — pinning, replication, adjudication — is
bookkeeping over an object that doesn't exist.

So the product is: **make the result an object, then the three problems become one
workflow.**

---

## 1. What already exists

This is not a greenfield. Reading the repo, the hard parts are built:

| Capability | Where | Why it matters here |
|---|---|---|
| **Content fingerprint of a procedure version** — `hash_procedure_version()` | `app/execution/plans.py` | The identity half of reproducibility. A version label can lie; a hash cannot |
| **Versionless references refused** — `validate_procedure_ref`, invariant #2 | same | No "latest" can enter a plan. This is the rule the whole field is missing |
| **Self-describing executions** — `executions` carries `procedure_id` + `procedure_version` denormalised | `db/23_plan_persistence.sql` | A run says what it ran without a join |
| **Durable, resumable execution** — `execution_runs`, per-node state, fenced terminal writes | `db/36–37` | A crash is resumable, so a long run can *complete* — which reproducibility needs |
| **Replay** — `replay_session`, `regenerate_observations`, content fingerprints | `app/execution/replay.py` | The primitive for "run it again and compare" |
| **Wilson-bound ranking** — `BEST_VERIFIED_FLOOR = 0.70` | `app/services/product_model.py` | A score that cannot be gamed by a lucky streak |
| **Proposed vs active** — public association can only create `proposed` | `app/api/goals.py:590` | The existing gate, and the thing this work extends |
| **Bitemporal supersession** — invalidate-and-append | `db/18` | History is never lost, so a claim's evidence survives its procedure's edits |

**What is missing is the object itself**: there is no `claim`, no `replication`, no
`harness_digest`. Those three nouns are the product.

---

## 2. The three objects

### 2.1 The pinned context — `harness_digest`

Reproducibility is a hash, not a paragraph. One value, computed from the seven fields,
that lets two runs be compared for *comparability* before their results are compared.

```
harness_digest = sha256({
  scaffold:      { name, version, commit },
  system_prompt: sha256(prompt),
  tools:         sorted([tool_id, tool_version]),
  stopping_rule: { max_steps, max_tokens, max_wall_clock },
  environment:   { image_digest },        # content-addressed, never a tag
  budget:        { cpu, memory, time },   # §9.5: budgets are part of the spec
  sampling:      { model_alias, provider, temperature, top_p, seed, run_date },
})
```

Two rules, both earned from the literature:

- **`run_date` is mandatory.** Closed-model aliases are silently re-pointed; "gpt-4o" in
  March and "gpt-4o" in September may be different models.
- **`environment.image_digest`, not a tag.** Tags are mutable. The disclosure audit found
  **none of eight** agent-benchmark papers fully disclose a content-addressed image.

A run without a complete digest is still recorded — as **unpinned**, at a lower tier. It is
never silently promoted.

### 2.2 The claim — the unit of research

```
claim {
  id, statement,
  unit:        topology | protocol | decomposition | harness | model | budget
  varies:      exactly one of the above          # the confound rule
  comparison:  baseline_ref, treatment_ref       # both pinned
  metric:      { name, oracle, aggregation }
  scope:       { task_family, models, budget }
  falsifier:   "success difference < 5pp at n ≥ 30 paired trials"
  evidence:    [run_id …]
  status:      draft | open | supported | refuted | indeterminate | expired
}
```

**Three rules that make this different from a forum:**

1. **`varies` names one axis.** A claim that changes topology *and* model is two claims,
   or it is a `note`.
2. **`falsifier` is mandatory for `open`.** This is STREAM criterion **6(ii)** — *"what
   evidence could have falsified the conclusion, and whether those conditions were
   pre-registered"* — which is already a published standard, so adopting it is not invention.
3. **`indeterminate` is a real outcome.** Neither supported nor refuted: the evidence could
   not separate them. The IETF agent-semantics draft uses exactly this three-valued
   outcome, and it is the honest answer most of the time.

### 2.3 The replication — the unit of review

Not a comment. A **run with a different provenance** against a stated claim.

```
replication {
  claim_id, replicator, harness_digest, runs[],
  independent: true,            # replicator ≠ claimant, enforced
  verdict: reproduced | failed | partial | inconclusive,
  notes,
}
```

Three properties, each lifted from a working system:

- **Independence is enforced, not asked for.** ReScience C's rule: *"you cannot submit the
  replication of your own research, nor the research of your close collaborators."* That is
  the anti-collusion mechanism, and it costs one check.
- **Failure is publishable.** ReScience explicitly accepts negative results, requiring only
  that the replicator document the claim and *attempt to contact the original authors*.
- **The replication must be run by the reviewer.** Their acceptance criterion is that both
  reviewers *"are able to run the proposed implementation on their computers, obtain the
  same results"*. Not read the code — run it.

---

## 3. How the three collapse into one workflow

```
        AUTHOR                          REVIEW                       RESEARCH
          │                               │                            │
   write a Way (prose) ─────────────►  a claim  ──────────────►  an open question
          │                               │                            │
     run it pinned ────────────────►  replication  ◄──────────  a related claim
          │                               │                            │
          └─────────────► evidence ◄──────┘                            │
                              │                                        │
                        adjudicated ─────────────────────────────────►│
                              │                                        │
                        the live solution                        the corpus
```

**The same object plays three roles.** A *Way* is what an author writes, what a reviewer
replicates, and what a researcher studies. That is why this is one product: the alternative
is three products with three data models that never reconcile — which is the state of the
field today.

---

## 4. What "instantly applied" becomes

The previous turn's question resolves itself once reproducibility is the goal.

**Instant visibility, evidence-gated liveness, per-deployment opt-in.** Concretely:

| Event | Latency | Gate |
|---|---|---|
| Anyone contributes a Way to a Goal | **immediate** — appears as `proposed` | signed-in identity only |
| It becomes runnable **in your own deployment** | immediate, if you opted into unverified | your flag |
| It becomes runnable **by an autonomous runner** | only after `BEST_VERIFIED` | none — hard rule |
| It replaces the incumbent at a plug-in point | after a **paired comparison** beats it | McNemar, discordant pairs |
| It propagates to existing deployments | **next version resolution, never mid-run** | `plans.py` invariant #2 |

**Why not instant global application.** It is the same failure as the Mint ledger's 21
incidents: a change nobody could see, applied where nobody was looking. Contribution being
open and *promotion* being earned are not in tension — they are what makes open
contribution safe enough to allow.

**Propagation is a version event.** A contribution that goes live creates a new pinned
revision at the plug-in point. What is already running is never mutated. This is the same
discipline `plans.py` already enforces for resumes, applied to propagation.

---

## 5. Build order

Each step is independently useful, and each unblocks the next.

1. **`harness_digest`** — the column, the hash function, and refusal to promote without it.
   *Smallest change with the largest effect*: it is the difference between a number and a
   result.
2. **The run record completes** — cost, tokens, tool calls, wall clock, failure class, on
   every execution. **None of eight papers disclose cost**; making it automatic makes us
   better than the norm by construction.
3. **`claims` table + the prose form** — status machine `draft → open → supported |
   refuted | indeterminate`. Falsifier required to leave `draft`.
4. **`replications` table + independence check** — replicator ≠ claimant. This is the
   review system, and it needs no moderation queue.
5. **Paired comparison reporting** — reuse the existing McNemar machinery. **Report
   discordant-pair counts**, never a bare percentage.
6. **Champion/challenger at the plug-in point** — the incumbent and a challenger both run;
   promotion on paired evidence. This is `current_best` generalised from a single winner to
   an ordered pair.
7. **Expiry** — closed models are re-pointed, so a claim about an alias must expire unless
   re-verified. Not urgent; necessary before the corpus is a year old.

---

## 6. What this beats, and what would beat us

**Beats:** every eval harness (they produce numbers, not objects); every benchmark
(they publish tasks, not claims); every review venue (they review prose). Nobody composes
pinning + claims + replication + propagation.

**Would beat us:** a frontier lab publishing a pinned-harness standard with claims attached —
i.e. STREAM extended from *reporting* to *replicating*. Since STREAM already has 6(ii)
(falsifiers, pre-registration), that extension is a short step for them. **Moving early on
`harness_digest` matters more than moving broadly.**

**A real risk:** the claim object could become a second blog. The guard is that a claim
cannot leave `draft` without a falsifier *and* at least one pinned run — the same shape as
the `proposed → active` gate already in `goals.py`, so it is a known pattern here, not a
new one.

---

## 7. Open questions

1. **Does a claim need a replication to be `supported`?** My view: `open` with one pinned
   run, `supported` only after an independent replication. That makes independence
   load-bearing and costs nothing but patience.
2. **How does `indeterminate` behave in a corpus?** It should be visible by default — hiding
   it is how "no effect" gets read as "no problem".
3. **Champion/challenger cost.** Running two Ways on every task doubles spend. Probably
   only for claims under active review, not for every task forever.
4. **Who bears the replication cost?** ReScience has volunteer reviewers. A venue that
   needs *runs* — not reading — needs either funded reviewers or a credit economy. We have
   the latter half-built (`credits.py`).
5. **What is the minimum viable experimental corpus?** Probably: take three published
   findings (SILO-BENCH's coordination gap, L-HTN's lexical robustness, Stop Means Stop's
   sibling leak), express each as a claim, and re-derive it through the pipeline. If the
   schema cannot express a published finding, the schema is wrong.

---

## 8. One sentence

**Reproducibility, review and research are the same problem — an agent result is not an
object — and fixing it once means a pinned run, a falsifiable claim, and a replication by
someone else are three views of one record.**
