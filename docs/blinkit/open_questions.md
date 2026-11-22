# Open questions

**Every decision still unmade, with what it blocks and a proposed default. 2026-11-23.** Companion to
`pricing_discussions.md`. A question here is not a bug — it is a choice that needs a person, and guessing one would
bake it into the schema.

---

## Q1. Does kell charge for the work, or for the tokens?

**Blocks:** what the Way page prints, and therefore the whole pricing frame (`pricing_discussions.md` §3.4).

a16z argues that pricing an application in tokens imports the provider's cost structure and anchors the product to a
unit whose cost keeps falling. Their ladder puts "turn models into useful work" one rung above "sell model access",
and a Way is recognisably *work* ("a NeurIPS-format report"). Their buyer survey: **27 of 50 preferred credits tied to
recognizable work, 14 preferred tokens.**

The opposing case: our buyers are technical, they can price-check against public rate cards, and
`UsageBox` notes that undisclosed spread gets competed away fast.

**Proposed default:** the price is for **the Way** (already true — a fixed licence per output). Measured cost sets
the **floor** and feeds **realized margin**; it is not the printed number. The estimate stays visible as an
assumption, not as the price.

---

## Q2. What is the minimum sample size before a cap is learned?

**Blocks:** `caps.md` §4.2, and therefore whether a Way's second run is truncated.

The literature says *require a minimum sample size* but does not name one, and warns that 40 observations gives a p99
that is "really just the longest request you have seen".

**Proposed default:** `n ≥ 10`, with the seed cap in force below it and the reason shown on the page. A p99 from 10
points is noisy but vastly better than one from 1; from 40 it is meaningful. Revisit once there is data.

---

## Q3. Is the first-run spend ceiling a hard failure or a warning?

**Blocks:** `caps.md` §4.1.

A first run that exceeds the ceiling could: (a) stop and refund, (b) stop and let the author raise it, (c) continue
and record the overrun.

**Proposed default:** **(a) stop and release the licence**, with the measured cost of the partial run kept — it is
still data, and the next attempt benefits from it. A run that quietly costs more than the price it was meant to
justify defeats the purpose of the ceiling.

---

## Q4. Does a later, cheaper run lower the price automatically?

**Blocks:** `measurement.md` §5.

If run 3 costs half of run 1, the honest floor drops. Auto-lowering means the author earns less than they expected,
silently.

**Proposed default:** **no automatic reprice.** The author is shown the measured floor and realized margin and
accepts a new price; an accepted change opens a new version (prices live on the version row, so an existing licence
is never rewritten). A price that only goes down automatically is a price the author did not set.

---

## Q5. Who pays for retries and failed calls?

**Blocks:** the retry term's meaning in `cost_estimate.py`, and whether the margin double-counts
(`pricing_discussions.md` §3.3).

We already paid for them. `UsageBox`: *"you paid for them — decide explicitly whether the customer does."*

**Proposed default:** the customer pays, **inside the fixed price** (not as a surcharge). The retry term
(`p_retry × retry_fraction`) is what makes the price cover them; the margin must therefore be read as covering
**orchestration and metering**, not retries. That reading must be stated wherever the margin is shown, or the number
means two different things to two readers.

---

## Q6. Are the locator rates good enough to publish?

**Blocks:** `cost_estimate.py`'s `LOCATOR_RATE_USD`.

`tool_gateway_calls.cost_usd` exists and is **0/130 populated**. So `tool` $0.005, `url` $0.0005 are vendor
estimates, and the page says so.

**Proposed default:** populate `cost_usd` at the gateway (Exa already returns `costDollars.total`), then replace the
table with measured means per tool. Until then the assumption stays visible — it is the one number on the page we
know is a guess.

---

## Q7. Should a Way be sellable before it has a measured run?

**Blocks:** `pricing_discussions.md` D6, which is designed but not enforced.

**Proposed default:** **publishable, not sellable.** An unpriced Way shows its steps, its output, and *"this Way has
not been measured yet"*; the buy button is absent. That is the cheapest possible gate (the author needs one run) and
it prevents a price with no measurement behind it.

---

## Q8. Do sub-Ways get measured and priced transitively?

**Blocks:** `pricing_discussions.md` D9.

A Way that composes three others: if each charges, the buyer pays three times. If none does, the author pays for all
their compute.

**Proposed default:** **licensed transitively, never charged twice** — one purchase covers the whole run, and the
sub-Way's measured cost rolls into the parent's floor. It is written but not implemented, and the measurement layer
(Q1–Q3) is a prerequisite.

---

## Q9. What does a buyer see of the cost?

**Blocks:** the Way page's price block.

Options: nothing but the price; the estimate range; the estimate range **and** the measured cost of runs so far.

**Proposed default:** **price + the estimate's assumptions**, never another buyer's run data
(`measurement.md` §6). The author sees the measured cost; the buyer sees what they pay and why the estimate said so.
Showing measured cost to buyers would make every purchase a negotiation against kell's cost of goods.

---

## Q10. Which sandbox for a Way that needs `process`?

**Blocks:** `execution.md` §8 — `needs: process` routing for a `prompt` step.

Agent37 is the default (weaker default isolation than gVisor, but our calls explicitly request `deny-all` egress);
Modal is selectable. The richer file API (`/v1/files`) needs an agent template; `scp` works on every template.

**Proposed default:** keep the workspace in object storage and the sandbox per step; do not switch the template until
a Way needs `/v1/files` for something the chunked `exec` transport cannot carry. Say so if you would rather have the
persistent-instance approach instead — its cost is that a Way becomes pinned to one provider.
