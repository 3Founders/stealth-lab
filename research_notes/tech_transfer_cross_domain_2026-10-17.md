# Deep research — technology transfer across domains (not just repair)

Research date **2026-10-17**. Exa + arXiv + primary sources. Assessed against
`docs/plan_2026-10_tech_transfer_targeted_repair.md` §7, §7.1, §7.2.

**Correction to my previous pass:** I over-applied §7.2's "park biology" ruling and researched APR
instead of technology transfer. The plan's own §7 says the unit is wider — *"a transformation record
is a claim, an intervention (the procedure), a measurement plan, the evidence, and the replications"* —
and §7.2 names biology as domain 2 of 3. This pass covers the actual transfer problem.

**Headline: the cross-domain premise has a serious empirical problem, three of the plan's open design
questions already have institutional or field-standard answers, and the plan's own falsifier has a
working reference implementation in production.**

---

## 1. The premise needs justification: method transfer across domains does not generalize

### Current progress and open challenges for applying deep learning across the biosciences (PMC8976012)

> "In this paper, we aim to address these foundational questions from the lens of computational
> biology. **The answers, however, are highly task specific and can only be addressed in the context
> of the corresponding applications.**"

The paper goes on to catalogue areas with notable success (DeepVariant, DeepArg, metagenomic binning,
lab-of-origin attribution) *and* areas with "varying levels of success to date," with limitations and
failure modes section by section.

**This is the most important finding in this document.** Cross-field method transfer is the entire
premise of the technology-transfer ambition, and the strongest available survey says the answers are
**task-specific and context-bound**. That does not kill the idea — it means the value proposition
cannot be "methods transfer across fields." It has to be something narrower and harder, e.g.:

- transferring a **verified record** (claim + intervention + measurement plan + evidence) rather than
  transferring a **method**; or
- transferring within a domain across **organisations** (which is what E4 actually tests), treating
  cross-*domain* transfer as a later, separate, harder claim.

**[inference]** E4, the two-organisation test in one domain, is the honest first milestone. §7.2's
"gate to move down the list" (≥5 records replayed by a third party, ≥4/5 within tolerance, ≥2
organisations) is correctly scoped and correctly ordered. The risk is the *narrative* implying more.

---

## 2. §7's falsifier already has a working implementation — interoperate, don't rebuild

Plan §7 falsifier (a): *"a third party cannot replay a record on their own machine from the pack alone
(test with 5 records, success is 4 of 5 reproduce the stated number within the stated tolerance)."*

### nf-prov + nf-core RO-Crate + LifeMonitor (production, multi-engine)

> "**nf-prov** is a Nextflow plugin to generate provenance information for pipeline runs. It supports
> serialization in the **BioCompute Object** and **Workflow Run RO-Crate (WRROC)** formats. WRROC
> support is available since version 1.4.0."
> "The **nf-core RO-Crate tool** can generate a **Workflow Testing RO-Crate** for a Nextflow pipeline.
> Workflow Testing RO-Crate is a specialization of Workflow RO-Crate that supports additional metadata
> related to the testing of computational workflows, **allowing workflows to be automatically tested
> and monitored by the LifeMonitor service**."
> "The nf-core RO-Crate tool utilises the repo2rocrate library, which **also supports Galaxy and
> Snakemake**."

**This is automated third-party replay of a workflow pack, already shipped, already spanning three
engines.** The plan's §7.2 domain-2 adapter list ("environment capture (the pack must run on someone
else's machine)") is what WRROC + LifeMonitor already do for Nextflow/Galaxy/Snakemake.

**Recommended:** make StealthLab's record format and replay harness target Workflow Run RO-Crate, and
test against LifeMonitor. That converts the plan's hardest domain-2 requirement into an existing
standard plus an existing verifier. **[inference]** this is a large de-risking of domain 2.

### Code Ocean Compute Capsules — the "portable pack" already exists commercially

> "A **shareable, traceable, reproducible encapsulation** of the code, data, and environments used in
> computational research, version controlled and linked to the results they produce."
> "Compute Capsules work by creating an **immutable record** of the code, environment, and data used in
> computational work, as well as the results produced. They are releasable, versionable, and
> reproducible. **Whatever runs today will always run in the future.**"
> "**Lineage Graph** — Every Result in Code Ocean includes an immutable record of how it was created.
> View Result provenance and see full lineage."

Plan §3 lists "portable pack" as **Partial** (in-repo `library.md` only, no cross-org pack). Code Ocean
is the commercial answer. Also **WorkflowHub** is a registry for computational workflows (Nature
Scientific Data, 2025).

**[inference]** StealthLab's differentiation cannot be "we package and version the run." It must be the
claim/evidence/verification layer and the transfer accounting on top of a pack.

---

## 3. §3's open ownership question has a standard institutional answer

Plan §3: *"Contract terms: ownership of contributed procedures… **The user has an open question: who
owns the procedures.** It must be decided before any second organisation is involved."*

### Georgetown University Office of Technology Commercialization — agreement flowchart

> "**Material Transfer Agreements (MTA):** A material transfer agreement is between two parties for
> the transfer of materials from one party (provider) to another (recipient) **for the recipient's
> independent research**. This agreement is a form of a **non-exclusive license** from provider to
> recipient for **limited internal research use**. **The recipient usually grants provider joint
> publication rights** (e.g., co-authorship), as scientifically applicable. **As between non-profit to
> non-profit, there is usually no intellectual property reach-through** (e.g., the party that develops
> intellectual property owns it). **However, where a Company is the provider** (e.g., of a drug) and
> pr[ovider]…"

That is the institutional vocabulary the plan needs, already resolved for the common cases:

| Question | Standard institutional answer |
|---|---|
| What is the grant? | Non-exclusive licence, limited internal research use |
| What does the recipient owe? | Joint publication rights / co-authorship, "as scientifically applicable" |
| Who owns what IP is later derived? | **No reach-through** between non-profits — the developer owns it |
| Does the shape change with a company provider? | **Yes** — the no-reach-through rule is not symmetric once a for-profit is provider |

**The last row is the live decision for StealthLab.** A public-good procedure contributed by one org
and transferred to another is the non-profit/non-profit case (no reach-through, publication rights
govern). A contributed procedure later used by a company is the asymmetric case. That boundary needs a
ruling, and now there is a template to rule against. **[inference]** the plan should rule on the
asymmetric case explicitly, because that is where StealthLab itself sits.

---

## 4. §7.1's staged rollout is a causal-inference problem with a 2024 methods guide

Plan §7.1: *"New sub-goal procedure → offline replay in each interested tenant's isolated worktree →
canary on a few tenants with a human-visible gate → active. Promotion by a pre-stated sequential rule
with a negative-transfer stop… Results are stratified per environment; a pooled average hides tenants
where it hurts."*

That is **staggered treatment adoption**, and it has known estimator pathologies with a recent guide.

### Wing, Yozwiak, Hollingsworth, Freedman, Simon — "Designing Difference-in-Difference Studies with Staggered Treatment Adoption," Annual Review of Public Health (2024)

> "Difference-in-difference (DID) estimators are a valuable method for identifying causal effects…
> **A growing methods literature points out potential problems with DID estimators when treatment is
> staggered in adoption and varies with time.** Despite this, no practical guide exists for addressing
> these new critiques in public health research. We illustrate these new DID concepts with step-by-step
> examples, **code, and a checklist**… We outline **newly uncovered threats to causal interpretation of
> DID estimates** and the solutions…"

The plan is about to run a multi-tenant staged rollout with a pre-stated promotion rule and a
negative-transfer stop. **That is precisely the design this paper warns about, and it ships a
checklist.** Use it to specify the rollout's evaluation before the rollout runs, not after.

### The non-inferiority error taxonomy is exactly the transfer question

**CONSORT — Reporting of Noninferiority and Equivalence Randomized Trials**

> "In **superiority** trials the null hypothesis is that treatments are equally effective… In
> **noninferiority** trials, the null and alternative hypotheses are **reversed**; a **type I error is
> the erroneous acceptance of an inferior new treatment**, whereas a type II error is the erroneous
> rejection of a truly noninferior treatment. A noninferiority or equivalence trial requires that the
> **reference treatment's efficacy is established** or in widespread use…"

Plan §7 names "a **pre-stated non-inferiority or superiority margin**, as in the pilot design." Two
things fall out of the CONSORT definition that the plan does not currently state:

1. **Noninferiority's type I error is "accepting something worse."** For procedure transfer, "did not
   hurt" is the claim, so the error the plan actually fears is the *primary* error, not a side concern.
   That should be stated explicitly wherever a transfer result is reported.
2. **The reference arm's efficacy must be established first.** That is a precondition the plan does not
   have: the 205-task run establishes that *StealthLab's ladder* is non-inferior to plain Claude Code
   on code tasks, which is a reference for §7.2's domain gate but **not** for any new domain.

### KPI definition as a pre-registered work package — for the "business transformation" class

**GATEKEEPER D7.1 — Pilot Studies Use Case Definition and KPIs (EU project deliverable)**

> "This document will report the detailed plan for the pilot use cases definition and the experimental
> design… focuses on determining and establishing **Key Performance Indicators (KPIs) that will be used
> as measurable values that demonstrate (or refute) how effectively** GATEKEEPER is achieving its key
> (business) objectives. The KPIs will be described, including the **scales and assessment tools**
> that will be used, **that have been established in D6.4** [an earlier deliverable]. Final,
> dissemination/communication plans will be discussed."

This is the template for the plan's "business transformation" evidence class: KPIs pre-registered as a
deliverable, with scales and assessment tools fixed in an *earlier* document, and **refutability built
into the wording**. Plan §7.2 says a business transformation "gives a KPI after weeks, noisy and
confounded" but does not have a counterpart to "established in D6.4."

---

## 5. Domain 3's cost is quantified — which justifies the domain order

### Better reporting for better research: a checklist for reproducibility (Kenall et al., GigaScience, doi 10.1186/s13059-015-0710-5)

> "In 2013 one researcher, **Phil Bourne**, asked just this: *How easy is it to reproduce the results of
> a published paper?* How easy would it be to reproduce the results of **a computational biology
> paper?** The answer: **280 hours**. Such a number is surprising, given the theoretical
> reproducibility of computational research and **given Bourne was attempting to reproduce work done
> in his own lab**."

**280 hours to replay a computational biology paper — from inside the originating lab.** That single
number is the strongest available justification for §7.2's ordering (AI → computational biology →
wet-lab), and it is stronger than the plan's current argument ("the verifier is slow, costly, noisy").
It also reframes the product: the buyer for wet-lab/clinical replication is paying for **human hours**,
not compute. **[inference]** a replication-as-a-service model priced per human-hour, with the machine
handling records and provenance, is a distinct business from the compute-domain one.

---

## 6. §7.2's dual-use blocker has a policy template

### CRS report IF13269 — "Artificial Intelligence and Biosecurity Issues" (Todd Kuiken)

> "AI technologies, methodologies, and applications may benefit biological sciences research and
> development (R&D). For example, AI could be used in conjunction with **biological design tools** to
> enable drug design. These potential capabilities have also raised certain **biosafety and
> biosecurity** concerns. **Some researchers argue that AI could be repurposed and misused to produce
> biological and chemical compounds of concern.** Congress may continue to assess… Regarding potential
> biosecurity concerns, Congress may consider **oversight mechanisms** to evaluate and manage such
> risks, such as **voluntary standards, industry-led frameworks, and regulations by federal
> agencies**."

Plan §7.2 blocker (a): *"dual-use risk, so a release gate with named human review before any procedure
becomes visible to other tenants, and some goal categories never published."* The prior art names the
mechanism classes (design tools → compounds of concern) and the oversight instrument types (voluntary
standards, industry frameworks, regulation). **[inference]** the "goal categories never published"
clause should be a named allowlist/denylist maintained as data, not a policy sentence.

---

## 7. The theoretical reason abstraction is hard

**Chapter 9 — Cognition, Types of "Tacit Knowledge" and Technology Transfer** (ScienceDirect S0573855506800106)

> "The concept of '**tacit knowledge**', introduced in modern epistemological literature thanks to the
> seminal work of the scientist and philosopher of science **Michael Polanyi, 1958**, has experienced
> over the years an ever widening application in a growing number of disparate disciplines that range
> from psychology to mathematics… we find for example 'tacit knowledge' used as an explicatory concept
> in studies on **organisational learning (Nelson and Winter, 1982)**, on **knowledge management
> (Nonaka and Takeuchi, 1995)**… and on the role of technology in **economic development**…"

This is **Polanyi's paradox** — we know more than we can tell — and it is the canonical frame for why
codifying a procedure is hard, which is precisely plan §3's "Abstraction: **Missing**" row and E3. It
also implies the plan's abstraction target should be stated as **what a procedure can say about itself**
rather than "what it can say about its org," because the org-specific part may be partly untellable.
**[inference]** E3's bar ("≥20 of 30 abstract to typed slots with no loss of the check") may be set
optimistically for procedures derived from tacit knowledge; the research literature on codification
suggests partial abstraction with an explicit "unknown/unbound" slot is the realistic target.

---

## 8. What this changes, ranked

| # | Change | Why |
|---|---|---|
| 1 | **Drop "methods transfer across fields" as the value proposition.** Say "verified records transfer within a domain across organisations; cross-domain is a later, separate claim." | The biosciences DL survey says answers are "highly task specific" and context-bound |
| 2 | **Target Workflow Run RO-Crate; test replay against LifeMonitor.** | nf-prov + nf-core RO-Crate + LifeMonitor already do automated third-party replay for Nextflow, **Galaxy and Snakemake**. Converts domain 2's hardest requirement into an existing standard |
| 3 | **Stop treating "portable pack" as Partial.** It exists commercially (Code Ocean capsules, immutable lineage; WorkflowHub registry). Differentiate on the claim/evidence/verification layer, not packaging | Code Ocean: "immutable record… Whatever runs today will always run in the future" |
| 4 | **Rule ownership against the MTA template**, and rule the **asymmetric** case explicitly (company provider) | Georgetown OTC: non-exclusive licence, joint publication rights, **no reach-through** non-profit→non-profit, but **not symmetric** once a company is provider. StealthLab sits in the asymmetric case |
| 5 | **Specify the staged rollout as staggered-DID before running it**, using the 2024 Annual Review checklist | The plan's rollout is staggered adoption with a pre-stated promotion rule — the exact design with known estimator problems |
| 6 | **State that transfer's primary error is accepting something worse** (noninferiority type I), and establish the reference arm's efficacy first | CONSORT noninferiority/equivalence reporting |
| 7 | **Copy the GATEKEEPER shape for business-transformation KPIs**: fixed in an earlier deliverable, scales and assessment tools named, "demonstrate (or refute)" | Plan §7's evidence class 2 has no pre-registration counterpart |
| 8 | **Quote the 280-hour figure to justify domain order, and price wet-lab replication per human-hour** | Bourne's own-lab reproduction of one comp-bio paper took 280 hours — wet-lab replication is a human-hours product, not a compute product |
| 9 | **Make "goal categories never published" a maintained allowlist/denylist** | CRS IF13269 names the mechanism (design tools → compounds of concern) and oversight types |
| 10 | **Lower E3's abstraction bar; add an explicit unknown/unbound slot** | Polanyi's paradox and codification literature say partial abstraction is the realistic target for tacit knowledge |

## 9. Gaps in this sweep

- I did not retrieve primary sources on **Bayh-Dole** specifically, or on FTO/patent landscape for
  method reuse. Both are load-bearing for §3's ownership ruling.
- No search was run on **cross-organisation data-rights law for code/procedure exchange** (which is a
  different question from human-subject data).
- **Interference / spillover** in the rollout evaluation came up via the DID paper's framing but was
  not researched directly — and with tenants acting on each other's repos, interference is a live
  threat to the §7.2 gate's own numbers.
- `benchmark_transfer.py` and the existing goal-abstraction DAG were not checked against the
  **library-migration** literature (e.g. LLM library migration on PyMigBench, arXiv 2504.13272), which
  is the closest existing work on transferring knowledge across API surfaces.