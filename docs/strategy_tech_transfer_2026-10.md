# Strategy: making technology transfer matter, and how we market it

Written 2026-10-22. A strategy and sequencing document, not a landing: no product code changed. It builds on
`docs/plan_2026-10_tech_transfer_targeted_repair.md` (the plan) and `remaining_tech_transfer.md` (what is not started).
Tags: **[R]** read in the repo, **[J]** my judgment, **[U]** from memory, unverified. Copy rules for anything outward-facing
(from the pilot copy rules): never name the lead, no prices, no savings headline we cannot back, an honest data path, and no
sample data presented as measured.

## 0. The one-paragraph version

Technology transfer is our durable moat and our least proven claim. A router can be copied in weeks; a library of fixes that
were verified in one place and re-verified in another cannot. But the plan itself says procedures have not been shown to
transfer across organisations at all, and none of its work order has started [R]. So the strategy is: **earn importance with
numbers, in stages, using the delegation-and-governance product as the distribution and the data source.** Every public claim is
gated by an experiment that can fail. If the central experiment fails, we keep the product that already works and drop the
claim.

## 1. What we are making important, stated so it can fail

**Claim.** A procedure verified in organisation A can be reused in organisation B at lower cost than a fresh solve, with no loss
of quality, once it has been (a) abstracted from A's names, (b) matched to B's repository by its preconditions, and (c)
re-verified in B before it is trusted [R, plan §1].

**What would make it false** (the plan's numbers [R]):
- E4: net gain below +3 solved, or losses above half of wins, or a net at or below zero.
- Replay: fewer than 4 of 5 records replayed by a party that did not write them, within stated tolerance, across at least 2
  organisations.
- Rank correlation below 0.3 between a procedure's replay result in one tenant and its result in another (across 20 procedures).

Everything below is conditional on these. Marketing must never run ahead of them.

## 2. Why this can matter (the argument, honestly)

1. **Agents re-solve the same problems in every company, and the learning is lost.** Each organisation's agents rediscover the
   same fixes; nothing carries the proof that a fix worked, where, and under what conditions. [J]
2. **The scarce thing is trust, not generation.** Generation is cheap and floods; what is rare is a change that has a check, a
   record of where it was verified, and a way to be revoked. [J]
3. **It is a package-manager shape.** Goal = interface, procedure = implementation, parent procedures = dependents; a new
   implementation is tested against every dependent before it ships, as compilers and distributions rebuild reverse
   dependencies [R, plan §7.1; analogues named from memory, U].
4. **Window.** A cluster of projects attacks slices of verification and receipts (see `COMPETITORS.md`) [R]. Most certify at
   submission; few measure outcomes statistically; none we found shows cross-organisation transfer evidence. That gap is the
   opening, and it is closing. [R/J]

## 3. The structural strategy: two products, one ladder

| Layer | What it is | Role in the strategy | State |
|---|---|---|---|
| **1. Verified delegation + governance** | Cheaper models take a task only when the project's check passes; the company's key sits behind policy, budget, ledger, kill switch; one-command join | **Distribution and data.** Gets installed, makes the traces, earns trust on a claim we can already test | Built; run end to end locally; no real-Cursor or customer run |
| **2. Verified transfer** | Abstract, pack, match, bind, rehearse, probation, promote, revoke | **The moat and the reason to matter** | Not started; E1-E4 not run |

Why this order [J]:
- Layer 1 is sold on a claim we can measure now (accepted tasks, rework, latency, spend control). It puts the hooks and the
  evidence path inside real repos, which is the only way to get traces from more than one place.
- Layer 2 needs those repos. Selling it before it exists would be a story, not a product.
- Each customer gets a reason to stay: later, "your fix was reused in N other teams, and here is the record."
- If Layer 2 fails its experiment, Layer 1 is still a business. That is the hedge.

## 4. Stages, each gated by a number

| Stage | Work | Gate to the next stage | What we may say publicly |
|---|---|---|---|
| **0. Foundations** (now) | E1 (offline, free) on the 153 recorded tasks; the ownership ruling started in parallel; `targets[]` and recorded `scope_spill`; E3 (read 30 procedures) | E1 shows spill among failures is at least double that among passes (else spill stays cosmetic); E3: at least 20 of 30 abstract to typed slots with no loss of the check | The plan and its falsifiers (a pre-registration). No results. |
| **1. Inside one company** | Probation tag and "verified where" on evidence; E4 between two repos of one organisation (the plan's stand-in) | E4 net at least +3, losses at most half of wins | A measured internal result with its caveats, no name |
| **2. Across companies** | Ownership/licence terms; neutral-operator rules; security prerequisites (signing, sandboxed replay, per-tenant opt-in, revocation push) | At least 5 records replayed by a party that did not write them, at least 4 of 5 within tolerance, across at least 2 organisations | A cross-organisation result and the pack format |
| **3. Public commons** | Registry of verified packs; independent-reuse credit; the part-of edge so importance can be computed | Independent submissions and replications appear without the founders | A public index and an open format |
| **4. Other domains** | Adapters after the gate; biology and others are parked by ruling [R] | The same replay gate in the current domain first | Nothing now |

**Sequencing correction [R]:** the ownership ruling has no engineering dependency; start it now as a parallel human and legal
track. It blocks everything outward-facing but does not block E1-E3.

## 5. How to make it important: six mechanisms

1. **A number the market can adopt.** Define and publish: *transfer yield* (net wins per 100 transferred procedures), *negative
   transfer rate*, *replay rate by an independent party*, and *cost per verified fix*. A metric others quote is authority; a claim
   we make about ourselves is not. [J]
2. **A benchmark we do not own alone.** Build a public task set of repo pairs with the same failure signature, seeded from public
   data already in the import path (SWE-bench-style experiments; the routing import already reads them [R]). Benchmark credibility
   comes from third-party replication, which is also the replay gate. [J]
3. **An open format.** A signed, versioned pack (procedure, diffs, checks, environment capture) plus an interface contract on
   each part-of edge and a five-part adapter spec (vocabulary, verifier, environment capture, data-rights class, release gate)
   [R, plan §7.2]. Ride existing rails: `SKILL.md` over MCP, as `SOLUTION.md` already decides [R]. Standards create importance
   that a product alone does not.
4. **Safety as the headline, not the footnote.** A procedure that auto-runs in many companies is a supply-chain channel [R]. To be
   the first with signed, sandboxed, probationary, revocable transfer is a trust position competitors who certify once do not
   hold. Lead security conversations with this, and with the reverse-dependency idea: nothing ships until every dependent's own
   check still passes.
5. **Research credibility.** Pre-register experiments, list the falsifiers, publish negative results. The repo already has a
   preprint skeleton and preregistration habit [R]. A negative result published early is rare and builds trust that a launch
   post cannot.
6. **A visible map of what matters.** Once the part-of edge exists, show the most depended-on goals and their verified
   implementations, like a dependency graph. Importance computed from independent verified dependents (not reference counts, which
   reward duplicates) is itself a marketing artifact [R, plan §7.1]. Not before the edge exists.

## 6. Audiences and the message to each

| Audience | What they care about | What we say | What we do not say |
|---|---|---|---|
| **Engineering leaders with many repos or teams** (internal transfer first) | Reuse across teams, less rework, an auditable record | "A fix that works in one team's repo becomes a checked, reusable asset for the others." | A savings figure; "works across companies" |
| **Security and platform** | Supply chain, blast radius, stop authority | Signed, sandboxed replay, probation before acting, per-tenant opt-in, revocation, the audit chain | "Safe" without the controls built |
| **Researchers and reproducibility groups** | Replay, falsifiers, pre-registration | The protocol, the numbers, the failures | Novelty claims before reading the prior art (Distill, ReScience, Code Ocean, CodaLab and similar are named from memory, **U**) |
| **Tool and model vendors** | Verified workloads, integration surface | Hooks for their tool, an open pack format | An exclusive or a dependency on one vendor |
| **Funders and grant bodies** | Credibility, a falsifiable plan, public value | The staged gates and the open format | Market-size claims without sources |
| **Contributors** | Credit, ownership clarity | An accounting of independent reuse (the credit ledger exists [R]) | Payouts before the ownership ruling and an anti-farming design |

## 7. Positioning by stage

- **Now (Layer 1):** verified delegation with control and proof. Not a gateway, not memory. See the earlier positioning
  discussion: control, proof, fit, transparency.
- **After Stage 1:** "Your engineers fix it once; the checks decide where it holds." Inside one company.
- **After Stage 2:** "A package manager for verified changes, with a neutral operator." Across companies.
- **Never:** "AGI memory", "self-improving", an unqualified "works everywhere".

Category name candidates to test (do not commit before testing with buyers) [J]: *verified transfer*, *verified reuse*, *change
provenance*. Working tagline for internal use only: "Solve it once. Prove it where it lands." It describes a hypothesis, so keep it
out of public copy until Stage 1 passes.

## 8. Channels, proportionate to a small team

1. **Pre-registered protocol + preprint skeleton** (exists in `PREPRINT_SKELETON.md` [R]): the credibility asset. Publish at
   Stage 0.
2. **Design-partner pilots** (2-3), run as controlled experiments with agreed measures *before* starting. Private case study with
   numbers; no names.
3. **A demo that shows the record, not the magic:** one fix verified in repo A, rebound to repo B, rehearsed, probated, promoted,
   then revoked and shown withdrawn. The founder's recorded video exists locally and is not to be pushed [R, CLAUDE.md].
4. **An open format and adapter spec** published early; invite others to write adapters.
5. **A periodic replay report:** what transferred, what regressed, what we retired. The negative transfers are the most believable
   part.
6. **Talks and write-ups aimed at the people who own repos at scale**, using the numbers, not the narrative.

Skip for now: paid ads, broad launch events, a public registry before there is anything independent to put in it.

## 9. Leading indicators and kill criteria

| Indicator | Why | Stop or pivot if |
|---|---|---|
| E1 spill-vs-error ratio | Decides whether the targeting rule is real | Within noise: keep spill cosmetic, drop the blocking rule |
| E3 abstraction yield (of 30) | Whether procedures can leave their organisation at all | Under 20 of 30: fix abstraction before anything else |
| E4 net and loss ratio | The central claim | Net at or below 0: stop the transfer claim; keep Layer 1 |
| Independent replays | Whether outsiders can reproduce | Under 4 of 5: the format is not portable |
| Design partners who ask to contribute | Real pull | None by the end of Stage 1: reconsider |
| Verifier false-accept rate | Bounds how far any record can be trusted | Unmeasured: measure before Stage 2 |
| Negative-transfer rate | Safety | Rising with scale: tighten probation |

## 10. Risks and what protects us

- **Central claim fails** (E4): Layer 1 remains. This is why we do not stake the company on Layer 2 first.
- **Weak verifiers** yield confident wrong records [R]: measure false-accepts; declare the evidence class of each record
  (replayable check, controlled field measurement, observational) and never let a claim borrow strength from a stronger class.
- **Volume and farming:** free submission plus cheap generation floods; rewards for independent reuse only, with per-tenant caps.
- **Ownership and law:** the one-page ruling, counsel for cross-organisation terms. Not a code item [R].
- **Platform risk:** a tool vendor or lab builds its own sharing. Defence: neutrality across tools, the open format, and the
  evidence.
- **Competitors in the verification cluster** ship receipts first. Defence: be first to publish a cross-organisation number, with
  its failures.
- **Leaking customer code** through a transferred procedure: abstraction must be audited (E3) before anything crosses a tenant
  boundary; redaction is not abstraction.
- **Narrative outrunning evidence:** the single biggest reputational risk. Every public sentence maps to a gate in section 4.

## 11. Next 90 days

| Weeks | Do | Done when |
|---|---|---|
| 1-2 | Run E1 on the 153 recorded attempts; start the ownership ruling; write the pre-registration | E1 ratio recorded; a one-page ruling drafted for the founders |
| 2-4 | `targets[]` and recorded `scope_spill` (no blocking); E3 on 30 procedures | E3 count of 30; scope_spill counted on a replay |
| 4-6 | Probation tag and "verified where"; define the pack format and the adapter interface | Evidence rows carry the tag; a pack round-trips on a second machine |
| 6-10 | E4 between two repos of one organisation | Net and loss ratio against the bar |
| 8-12 | Decide Stage 2 on the numbers; write the preprint draft with whatever the results are | A go/no-go note with the numbers, whichever way it falls |

## 12. Decisions needed from the founders

1. Is intra-company transfer the first product claim, with cross-company later? (Recommended.)
2. Who rules on ownership and licence of contributed procedures, and by when?
3. Do we publish the pre-registration at Stage 0, before any result?
4. Is the open pack format and adapter spec released early, or held until Stage 2?
5. Which two repos are the E4 stand-in?
