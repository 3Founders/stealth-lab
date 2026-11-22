# Pricing a Way, and the two structural changes behind it

**2026-11-22.** Research first, argument second. Four questions are on the table: which sandbox each Way uses,
letting anyone suggest a better model or sandbox for a step, opening any step as its own Way, and — the one that
decides the business — **how to price**. Prices and survey figures are cited at the point of use.

---

## 0. The short answers

| Question | Answer |
|---|---|
| **Every Way has a sandbox field?** | Yes — but it declares a **requirement** (`needs`), not a vendor. Cheapest is not the same as best, and the field must not know the vendor. |
| **Cheapest sandbox?** | **Agent37** — roughly 14× cheaper than E2B on a published build benchmark (§4). But see the caveat before committing to it. |
| **Anyone can suggest a better model or sandbox for a step?** | Yes — and a suggestion *is* a change to the router's input, tested by the next run's recorded cost and outcome, not a vote. |
| **How is a step's model chosen?** | **The router, not a form** (§6.0). `routing.recommend` already routes per step; the Way executor just never calls it. |
| **Any step openable as its own Way?** | Yes — and it is the *same* operation as atomic reuse. If a step is not addressable as its own Way, it is not an atom. |
| **How to price?** | **Run it once, measure what it cost, price from that.** Not a published band (§5.0). Predictability beats cheapness (68% vs 19%), and the delivery cost is shown beside the price. |

---

## 1. The mechanism this all sits on

Before pricing, the thing being priced has to be clear. It is:

```
pay ₹150  →  procedure_licences row (pinned to ONE procedure version)
          →  way_runs.start() checks the licence
          →  steps flatten into a plan
          →  each step is done by the buyer's agent (session) or by kell's models (headless)
```

Four kinds of step, and only one of them can execute code:

| Step | Who does it | Needs a sandbox? |
|---|---|---|
| `prompt` | a model | **no** |
| `fetch` | HTTP to a URL, then a model if a prompt is attached | **no** |
| `tool` | kell's gateway (gated, consented, audited) | **no** |
| `human` | a person, who marks it done | **no** |
| `script` | **Modal Sandbox, gVisor, `block_network=True`** | **yes** |

**That asymmetry is the whole cost story.** Of the Ways in the library today, exactly **three** have a script
step, and none of them is approved. The sandbox is a liability for a category that barely exists yet.

---

## 2. Every Way declares a sandbox — as a requirement, not a vendor

```jsonc
// add to the step, beside `effect` (docs/backend_composability.md)
"sandbox": {
  "needs": "none" | "process" | "network" | "gpu",
  "max_seconds": 120,
  "network": "deny" | "allowlist",
  "writes": "none" | "workspace"
}
```

**Why never a vendor name.** A Way that stores `provider: "e2b"`:

- makes switching vendors a migration across every stored Way;
- breaks silently the day that vendor closes its source — **Daytona raised $24M in February 2026 and closed its
  source in June**, four months later;
- pins the price you quoted to one rate card.

**Why `needs` is the right denominator.** `needs: "none"` means the step is a model or tool call. It costs
**₹0 in sandbox** and should never be metered as if it did. This is what keeps the suggested-sandbox field from
turning into overhead on every procedure: the field is `none` for most, and the cost line stays honest.

---

## 3. What "open any step as its own Way" actually means

This is not a new feature. It is the definition of atomicity you already wrote down
(`docs/KELL.md` §1.1, property 2: *"never dissolved into its consumer"*).

If step 3 of a Way cannot be addressed on its own, then:

- you cannot **price** it; only the whole is priced;
- you cannot **suggest a better model** for it, because there is no object holding the model;
- you cannot **replace** it without editing the parent;
- you cannot **attribute** a failure to it.

So the mechanism is: **every step is either a `call(id)` to a stored Way, or a leaf that can be extracted into
one in a single action.** The extract produces a Way with:

```
steps[i].subprocedure_ref = newly_created_way_id   (pinned to its version)
```

and the parent keeps a `call` node, never an inlined copy. This is exactly the `op_compute` / `call(id)`
distinction in FAOP (`docs/backend_composability.md` §0): a shared sub-workflow has **one canonical definition**
that others call, so a change propagates instead of drifting.

**The consequence for pricing is direct:** if a step is its own Way, it has its own price. A parent Way's price
then has two honest components:

```
parent price  =  its own work  +  Σ (price of each sub-Way it calls)
```

**But do not charge twice.** A buyer who paid for the parent should not be billed again for a sub-Way *inside*
that run. Sub-Ways are licensed transitively by the parent's licence — otherwise a Way that reuses three atoms
costs four times as much, which punishes composition, the exact opposite of the goal.

---

## 4. The cheapest sandbox, with the caveat

From `docs/sandboxing.md` §4 (read there in full):

| Rank | Provider | Cost of 1,000 builds | Isolation |
|---|---|---|---|
| 1 | **Agent37** | **$0.99** | gVisor *or* container (see below) |
| 2 | Blaxel | $9.81 | microVM, scales to zero |
| 3 | E2B | $13.96 | Firecracker |
| 4 | Vercel | $27.64 | Firecracker |
| 5 | Modal | $27.99 | gVisor |
| 6 | Cloudflare | $37.98 | container in a VM |

### 4.0 Correction: the isolation claim was mine to check, and it does not hold as stated

An earlier draft of this document described Agent37 as **"gVisor, SOC 2 Type II"** and recommended starting on it.
That came from Agent37's own homepage. **Their own comparison pages say something different**, and the difference
matters for the decision:

- Their **vs-Modal** page: *"Agent 37 isolates each agent in its own container with its own filesystem"* and
  *"If you execute arbitrary code submitted by strangers, you want a hypervisor boundary, which is why Modal,
  E2B, and Vercel lean on gVisor or Firecracker."*
- Their **gVisor** page: *"Agent37 offers standard gVisor sandboxes and full VMs, so you can choose the runtime
  your agent needs"* — i.e. gVisor is an **option**, not the default.

So the correct statement is: **Agent37 can give you gVisor, but its default instance is a container.** The homepage
line and the comparison page do not agree, and the comparison page is the more specific one.

**Strategic consequence.** A vendor that says, in its own words, that running strangers' code needs a hypervisor
boundary — and then sells containers by default — is telling you exactly what your threat model is. **kell runs
code from strangers who paid money.** That is the case they name.

### 4.1 Where Agent37 genuinely is better

On price and latency, clearly and by a wide margin:

| | Agent37 | Modal |
|---|---|---|
| 730 h, 2 vCPU / 4 GB | **$4.76** | $173.71 |
| Create | 1.29 s | 929 ms |
| Resume | **176 ms** | 2.347 s |
| Run a command | 461 ms | ~96 ms US |

**Where it is better:** ~36× cheaper sustained, **13× faster wake**, and much cheaper for always-on or bursty
work. If a Way holds a sandbox open across a multi-turn conversation, that gap is the whole bill.

**Where Modal is better:**

| | Modal | Agent37 |
|---|---|---|
| Default isolation | **gVisor, always** | container; gVisor optional |
| Network egress | **blocked/allowlist per sandbox** | **open by default**, deny-all available |
| `block_network=True` | verified in our own `sandbox.py` | requires explicit config |
| Track record | large, $4.65B, public benchmarks | launched Sep 2026, **no SLA**, no changelog, no public SDK repo |

**On our specific threat model, isolation and egress matter more than $168/month at our volume.** Our script steps
are stranger-written; Modal blocks the network by default and we rely on that.

### 4.2 The recommendation, corrected

**Keep Modal as the default for stranger-authored scripts. Allow Agent37 as an opt-in
(`SUBMISSION_SANDBOX=agent37`), configured with `deny-all` egress and the gVisor template.**

That is what the code now does: both are wired, Modal stays default, Agent37 is selectable and documented with
the exact settings it needs to be acceptable.

**Revisit when**: script steps become a real fraction of runs (cost dominates), **and** Agent37 publishes a
changelog and an uptime commitment. Until then the saving is real but the assurance is not.

---

## 5. How to price — the argument

### 5.0 Correction: the price is *measured*, not chosen

An earlier draft of this document proposed a fixed ₹100–200 band. **That is wrong for this product**, and the
correction is worse for the fixed-band idea than for the rest of it.

**Price is whatever it costs to run the Way once, plus a margin — and you learn that by running it.**

The reason is simple and decisive: two Ways with five `prompt` steps can differ in delivery cost by **20×**
depending on what those prompts ask for. A five-step "find the NeurIPS template" Way and a five-step "exhaustive
literature review" Way are the same shape and completely different bills. A single ₹150 for both means one is
overpriced and one is sold at a loss — and you cannot tell which without running them.

So the mechanism is:

```
submit a Way  →  kell runs it once (a calibration run)
              →  the run records real tokens × real rates + sandbox seconds
              →  that is `run_cost_paise`
              →  price = run_cost × (1 + margin) + platform fee
              →  the buyer sees BOTH numbers
```

This is why `run_cost_paise` is not a nice-to-have display field in `docs/procedure_billing.md` — **it is the input
to the price**. Everything below assumes that ordering.

**What survives from the earlier analysis:**
- Per-**licence** (one pinned version, perpetual) is still the right *unit* — fixed units score +10 and pure usage
  −7 (Pricing I/O, §5.1 below). A measured price is still a fixed price once set.
- The delivery cost is still shown (§5.4).
- Sub-Ways are still licensed transitively and never charged twice (§3).

**What does not survive:** a published band of ₹100–200 as a *policy*. It may well be where most Ways land — but
it is an output of measurement, not an input.

### 5.1 What the evidence says

**Buyers rank predictability far above cheapness.** In Pricing I/O's 2026 survey of 296 software buyers:

> **68% rank predictable total cost among their top three priorities; only 19% rank lowest entry price that
> highly — the lowest-ranked factor of any tested.**

And net preference by model, "most preferred" minus "least preferred":

| Model | Net preference |
|---|---|
| Seat-based | **+17** |
| Subscription + fixed units | **+10** |
| Hybrid (flat fee + usage) | +2 |
| Hybrid (seat + usage) | 0 |
| Hybrid (flat fee + outcomes) | −3 |
| **Pure usage-based** | **−7** |
| **Pure outcome-based** | **−19** |

> "Every model in positive territory keeps a fixed anchor. … Buyers will pay more as they use more; **the
> objection is to losing the anchor that lets them predict the bill**."

**So: a per-licence fixed price is the right shape.** It is a *fixed unit* — the model family scoring +10 — and
it is not pure usage (−7) or pure outcome (−19).

### 5.2 The competing evidence, stated fairly

Per-result pricing is *shipping in production* at real prices:

| Product | One result | Price |
|---|---|---|
| HubSpot Customer Agent | a resolved conversation | $0.50 |
| Fin (Intercom) | an outcome, max one per conversation | $0.99 |
| Gorgias AI Agent | interaction resolved without a person | $0.90–1.00 |
| Zendesk AI agents | a verified automated resolution | $1.50–2.00 |
| Salesforce Agentforce | a question needing no human | $2.00 |

**Your ₹150 ≈ $1.70 sits exactly inside that band** — which is a good sign, not a coincidence: that band is
where "an AI did a real unit of work" clears psychologically.

But note what all five have that a Way does not: **a result definition they control.** HubSpot/Fin/Zendesk define
"resolved". A Way's success *is* observable — you make an evidence row for it — so per-result pricing is
*possible* here in a way it is not for a generic tool.

**Which is why the honest answer is: fixed price first, outcome later.**
Outcome-based pricing needs (a) a definition that survives dispute, and (b) a track record proving the Way
succeeds. You have neither yet. Pricing on an outcome you cannot yet measure is pure outcome −19: exactly the
model buyers reject hardest.

### 5.3 What it costs you to deliver — the number that decides the floor

The ₹1 Way's five steps are all `prompt` steps on a model. Delivery cost is tokens:

- `deepseek-flash`: **$0.15 / 1M input, $0.60 / 1M output** off-peak
- `deepseek-v4-pro`: **$0.66 / 1M input, $1.98 / 1M output** off-peak

A five-step research Way with a literature review will plausibly move **200k–800k tokens total**, output-heavy.
At Flash rates that is roughly **$0.05–$0.30**; at Pro rates, **$0.3–$1.5**.

So a **₹150 (~$1.70)** licence leaves real margin *if* the steps run on cheap models, and thin-to-negative margin
if every step runs on a frontier model. **This is the argument for `suggested model` per step**: not convenience —
margin.

### 5.4 The recommendation, concretely

**Price per licence. Fixed. Published. With the delivery cost shown beside it.**

| Element | Value | Why |
|---|---|---|
| **Base price** | ₹100–200 (floor / default / ceiling) | Inside the per-result band the market already accepts; microtransaction research says low value keeps perceived risk low |
| **Unit** | one **pinned version**, perpetual | §5.1: fixed unit (+10) beats pure usage (−7) |
| **Sub-Ways** | included, never charged twice | otherwise composition is punished |
| **Delivery cost** | shown, not hidden | 61% of buyers name lack of usage transparency as a top concern; hiding it is the Airbnb-delivery trap — a ₹90 offer that bills ₹110 is judged worse than a ₹100 one |
| **Outcome pricing** | later, only after a measured success rate exists | else you are selling −19 |

**On the ₹1 test price:** it is below Razorpay's per-transaction fee (~2% domestic) — you lose money on every
sale. Fine to prove the rail, cannot be the price. **Do not anchor on it**, either: a ₹1 listing next to a ₹150
one makes the ₹150 look expensive (the anchoring research is explicit that the *order* of visible prices changes
what reads as fair).

---

## 6. Anyone may suggest a better model or sandbox — as a claim

### 6.0 Correction: the router already does this, and nothing asks it

I proposed a suggestion table with a paired-comparison gate. **The router already exists and already routes per
step.** It is not being asked.

`app/routing/service.py::recommend` takes exactly what this needs:

```python
async def _recommend(pool, *, goal_id, candidates, ...,
                     step_order=None, step_role=None,
                     previous_steps=(), remaining_steps=(), ...):
    """Task level (step_order None): a ladder for the whole task.
       Step level: a ladder for ONE step of procedure_id's run.
       ... remaining_steps are the steps still to come, so the target and the value
       are the WHOLE run's (one-step rollout)."""
```

That is **per-step model routing with run-level value** — the thing this section was going to invent — and it is
Bayesian, with model cards, cost model, priors and feedback (`app/routing/` is 29 modules).

**And the Way executor does not call it.** `way_runs.py` line 45:

> `DEFAULT_MODELS = {"deepseek-v4.1-flash"}   # a submitted step's model is kell's default run model, not a choice`

`plan_step()` sets `"model": None` for every step, and `_model()` in `submissions/runs.py` calls `review.complete`
directly. **Every Way step runs on one hardcoded model.** The router, the calibration run and the cost figure all
exist; the wire between them does not.

### 6.1 So the submission flow is

```
author writes a Way (steps, prompts — no model names, no vendor names)
        │
        ▼
kell runs it ONCE, through the router
        │  routing.recommend(step_order=i, remaining_steps=…) picks a model per step
        ▼
the run records  tokens × rate  per step  +  sandbox seconds  +  wall time
        │
        ▼
run_cost_paise is MEASURED  →  price is derived (§5.0)
        │
        ▼
the Way is published with: its cost, its price, and the model the router chose per step
```

**Why the router and not a form.** A form asks the author to guess, and the author is the one person with **no
idea** what a step costs — their machine, their context, their habits. The router predicts from measured
performance across everyone. Submitting a model name is asking a question whose answer is worse than the one the
system already computes.

**And it makes "anyone can suggest" free.** A suggestion *is* the router's input changing. If someone believes
`deepseek-flash` is better for step 3 than the current pick, that is not a vote — it is a claim the next
calibration run can test, because the run records per-step cost and outcome. There is no separate suggestion
queue to moderate (§6.2).

### 6.2 What a suggestion still has to be

A claim, not an edit — the `varies`-names-one-axis rule (`docs/reproducibility.md` §2.2):

1. **`proposed` immediately, visible.** Same as a contributed Way.
2. **Promoted only by paired comparison** — McNemar over discordant pairs, same input, two routings. The router's
   own feedback loop (`app/routing/evidence.py`, `fit.py`) is the machinery.
3. **Cost and quality are separate claims.** A cheaper model is a *cost* claim; a better one is a *quality* claim.
   Never averaged into one number — a Way that gets cheaper and worse must be *refusable*, not smoothed.

**This is the piece that keeps "anyone can suggest" safe**: without the falsifier, it becomes a forum, and the
evidence on debate is unambiguous that debate lowers accuracy.

---

## 7. Build order

1. **`needs` on every step** (`none` default). Nothing runs differently; it makes sandbox cost countable.
2. **Wire the router into the run.** `plan_step()` sets `model: None`; instead call
   `routing.recommend(step_order=i, remaining_steps=…, previous_steps=…)` and record the chosen model on the step
   result. This is the single change that unlocks everything else — it is also how cost becomes measurable.
3. **Record per-step cost during the run**: tokens in/out × rate, sandbox seconds, wall time. `run_cost_paise` is
   the sum. Without this, §5.0's measured price is impossible.
4. **The calibration run on submit**, producing the cost figure the price is derived from. A Way with no
   calibration run is `unpriced` and cannot be listed for sale.
5. **Step → own Way** (`extract`), keeping a `call(id)` in the parent. Atomicity, not a new feature.
6. **Sub-Way licences are transitive.** Test: a parent that calls three sub-Ways charges once.
7. **Suggestions route through the router's feedback loop**, not a separate queue.

---

## 8. One sentence

**Run the Way once through the router, measure what it actually cost, and price from that — because the author
cannot know what a step costs, the router already predicts it, and a price nobody measured is either a subsidy or
a refusal.**

---

## Sources

- Pricing I/O, *What AI Software Buyers Want From Pricing in 2026* — https://www.pricingio.com/insights/what-ai-software-buyers-want-from-pricing-in-2026
- Oliver Wyman, *Managing the AI pricing transition — seats versus usage* — https://www.oliverwyman.com/our-expertise/insights/2026/oct/managing-ai-pricing-transition-beyond-seats.html
- QueryNow, *Results as a Service: published per-result prices* — https://www.querynow.com/resources/whitepapers/results-as-a-service-playbook
- Bain, *AI Pricing: A Reality Check on Effort, Usage, and Outcomes* — https://www.bain.com/insights/ai-pricing-a-reality-check-on-effort-usage-and-outcomes/
- TechTarget, *Zendesk expands outcomes-based pricing* — https://www.techtarget.com/news/zendesk-expands-its-outcomes-based-pricing-and-product-strategy/874881/
- DeepSeek API pricing — https://api-docs.deepseek.com/quick_start/pricing/
- Mercur, *Showing Prices on a Marketplace: How to Keep Offers Comparable* — https://www.mercurjs.com/marketplace-academy/showing-prices-marketplace
- Bright Patterns, *Cost transparency* — https://brightpatterns.org/bright-patterns-collection/cost-transparency
- Chaudhary & DaSouza, *Consumer Readiness for Microtransactions* — https://www.mdpi.com/2673-7116/4/3/29
- Greiff & Egbert, *A Review of the Empirical Evidence on PWYW Pricing* — https://www.ebrjournal.net/cgi/viewcontent.cgi?article=1101&context=home
