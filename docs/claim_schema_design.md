# Structuring claims across domains — literature review and proposed format

Research date **2026-10-17**. Sources retrieved this pass: Hanson, Lakatos, Toulmin, Cochrane/GRADE,
Nanopublication Guidelines (working draft, 30 Sep 2026), RO-Crate / Workflow Run RO-Crate, Simonsohn
et al. specification-curve analysis, Petersen & van der Laan on causal models, Zaldivar et al. model
cards, Staley on robustness vs security, Kamath/Jensen/Bennett on empirical appraisal of economic
theory, MINSEQE.

**The short answer.** The literature says you cannot state a bare claim, and it converges on *why* in a
way that determines the whole schema: a hypothesis is never tested alone. You always test
hypothesis + auxiliary hypotheses together (Duhem–Quine), and the observation is theory-laden (Hanson).
So the record must carry its own auxiliaries — the verifier, the conditions, the qualifier, the
population — or it is unfalsifiable and, worse, unfalsifiable *and* false.

The three-level structure you want — goal | procedure | claims — is not an invention. It is
**Lakatos's research programme**: hard core / protective belt / conjectures, with the progressive vs
degenerating problemshift as the test of whether the theory is doing any work.

---

## 1. What the literature rules out

### 1.1 Bare claims are unfalsifiable, and knowably so

**Duhem–Quine.** Testing a hypothesis tests it *plus* auxiliary assumptions about instrumentation,
auxiliary hypotheses, and the boundary of the relevant system. A "black swan" refutes "all swans are
white" only under auxiliary assumptions you hold but did not test.

**Hanson, *Patterns of Discovery* (1958).** The retrieved text: the same physical facts look different
given different theoretical commitments — microphysics is misrepresented by philosophers who treat
finished systems as the paradigm. **Observation is theory-laden.**

**Consequence for your schema, stated bluntly.** `goal | procedure | claims` where a claim is a
sentence is unusable. "This procedure fixes the bug" has no determinate truth value: whether it does
depends on the hidden test suite, the task's environment, the model, and the failure class — none of
which is in the claim. **Every field the claim omits is an auxiliary hypothesis silently assumed true.**
That is the same defect as the LDP provenance paradox: routing on self-asserted quality performs worse
than random because the quality was never independently established.

### 1.2 Lakatos gives the three levels, and the test

Retrieved from *The Methodology of Scientific Research Programmes*:

> "the typical descriptive unit of great scientific achievements **is not an isolated hypothesis but
> rather a research programme**. Science is not simply trial and error, a series of conjectures and
> refutations. 'All swans are white' may be falsified by the discovery of one black swan. But such
> trivial trial and error **does not rank as science**. Newtonian science, for instance, is not simply
> a set of four conjectures — the three laws of mechanics and the law of gravitation. These four laws
> constitute only the **'hard core'** of the Newtonian programme. But this hard core is tenaciously
> protected from refutation by a vast **'protective belt'**…"

The chapter structure of the retrieved text is itself the specification: *"Negative heuristic: the
'hard core'"*, *"Positive heuristic: the construction of the 'protective belt' and the relative autonomy
of theoretical science"*, and **"Progressive and degenerating problemshifts."**

**The mapping is not loose — it is the same structure:**

| Lakatos | Your record | Why |
|---|---|---|
| **Hard core** | **Goal** | A theoretical commitment that is never abandoned by a single counterexample, only shifted |
| **Protective belt** | **Procedure** | The modifiable conjectures that absorb refutation |
| **Conjecture + its evidence** | **Claim** | One procedure instantiation, with what would count against it |
| **Progressive vs degenerating problemshift** | **Goal-level metric** | *Does the machinery predict new phenomena, or only accommodate old ones?* |

**That last row is the answer to your question.** "Do the claims work as the theory of the field?"
is not a matter of taste — it is a computable operationalisation. A goal whose procedures only ever
resolve goals already known is degenerating. A goal whose procedures resolve goals that were *not*
predicted from the belt is progressive. This is one metric, and it is computable in all three domains.

### 1.3 Toulmin gives the claim its internal grammar

Retrieved: a claim is not a bare sentence but **claim + grounds + warrant + backing + qualifier +
exception**. The operative distinctions:

> "**Warrant**: How can we tell if reasons are strong? … Is the reason **relevant** to the claim it
> supports? And is the reason **effective**?"
> "**Qualifiers** are words like some, most, many, in general, usually, typically … little words whose
> value to an argument is immeasurable."
> "**Qualifiers and exceptions are similar in that they both put limits on how far a claim may be
> carried.**"

**So a claim that does not carry an explicit qualifier and explicit exceptions is not a claim — it is
an assertion.** This is the structural cure for the problem in `VERIFICATION_DISCLAIMER.md`: "Verified"
is currently a status label with no qualifier attached. Making the qualifier a *required field* makes
"usually", "in this environment", "for this failure class" impossible to omit.

### 1.4 The cross-domain certainty scale already exists: GRADE

Retrieved, Cochrane Handbook ch.14:

> "The GRADE approach specifies **four levels** of the certainty for a body of evidence for a given
> outcome: **high, moderate, low and very low**. GRADE assessments of certainty are determined through
> consideration of **five domains: risk of bias, inconsistency, indirectness, imprecision and
> publication bias**. For evidence from non-randomized studies… assessments can then be upgraded through
> consideration of three further domains."

**This is the most transferable object I found.** It was built for medicine and is used across
evidence-based practice, yet it is domain-neutral, because all five domains are about *the evidence
pipeline*, not about the subject matter:

| GRADE domain | What it asks | Your instance |
|---|---|---|
| **Risk of bias** | Does the measurement lie? | Verifier false-accept rate α — **your 5/150** |
| **Inconsistency** | Do replications agree? | Independent runs of the same procedure |
| **Indirectness** | Does this evidence speak to *our* conditions? | `where_verified: here / elsewhere` + applicability |
| **Imprecision** | Is the sample too small? | n=15 paired; n=205 A/B |
| **Publication bias** | Are negatives missing? | Do failed runs get stored, or only successes? |

Your `verification_layers 0–5` is an **ordinal status**. GRADE gives you the **dimensions that produce
it** — and the dimensions are the part that generalises across biology and economics without change.
Note also that your current problem is diagnosed in GRADE's own vocabulary: five wrong-passing answers
is a **risk of bias** finding, and the agreement-check remedy is GRADE's standard response.

### 1.5 Packaging: two levels of provenance, and negative results are first-class

**Nanopublication Guidelines** (working draft, 30 Sep 2026 — current):

> "A nanopublication consists of **an assertion, the provenance of the assertion** (simply called
> 'provenance'), and **the provenance of the whole nanopublication** (called 'publication info'). …
> **The different levels of provenance enable users to assess the trustworthiness of data** … and
> provides a mechanism by which authors and institutions may be acknowledged … Nanopublications may be
> used to expose quantitative and qualitative data, as well as **hypotheses, claims, negative results,
> and opinions that usually go unpublished**."

**Two levels of provenance** is the structural point, and it is exactly your problem:
1. **provenance of the assertion** — which verifier, which run, which evidence class, which α
2. **provenance of the assertion's place in the theory** — which goal, which procedure, what would
   falsify it

You currently have level 1 (evidence rows) and are designing level 2 (the review ledger). The format
should make both mandatory. And note that **negative results are named as a first-class publication
type** — you have five, and they are currently the most valuable thing you own.

**Workflow Run RO-Crate** (Sept 2024) is the execution-provenance container: it "bundle[s] together all
their associated objects (inputs, outputs, code, etc.)", aligns to **W3C PROV**, is implemented by
several workflow management systems, and enables "interoperable comparisons between workflow runs from
heterogeneous systems." **That last clause is why it matters for you** — it is the reason Nextflow /
nf-core, Snakemake, CWL, and RO-Crate are the prior art your own plan named in §7.2.

### 1.6 The check you are missing: specification curve, and the identification step

**Simonsohn, Simmons & Nelson**, *Nature Human Behaviour* (2020):

> "(1) identifying the set of **theoretically justified, statistically valid and non-redundant**
> specifications; (2) displaying the results graphically, allowing readers to identify consequential
> specifications decisions; and (3) **conducting joint inference across all specifications** …
> reveals that one finding is robust, one is weak and one is not robust at all."

**Applied to you:** your routing configuration has been hand-tuned across many runs (the run history in
handoff §2.1 is a record of that). So the −49% is potentially robust to model choices and fragile to
*policy* choices. A specification curve over routing configurations — plot every config, report the
joint inference — would tell you which. You do not currently do this, and it is cheap.

**Petersen & van der Laan**, the seven-step discipline for causal questions, and especially step 4:

> "(3) translation of the scientific question into a **counterfactual quantity**; (4) assessment of
> whether, under what assumptions, this quantity is **identified** — whether it can be expressed as a
> parameter of the observed data distribution or estimand…"

**Your headline claim has an identification problem you can name.** The counterfactual quantity is
*"tasks solved had we not routed."* Identification requires the assumptions that license it: same task
set, same grader, and — critically — **arm A unchanged between the comparison runs**. Your own handoff
§2.1 is titled *"the system changed between runs"* and lists six successive system states. And §12
explicitly discounts some page-suite deltas as "run-to-run variation," which is an admission that the
comparison is not fully identified there.

This is not fatal. It is **nameable**, and unnamed identification assumptions are what make a
cross-domain claim unfalsifiable.

### 1.7 A subtlety that constrains your remediation: robustness ≠ security

**Staley**, *Robust Evidence and Secure Evidence Claims*, *Philosophy of Science* (2022):

> "evidence for a theory is better when multiple independent tests yield the same result… Little has
> been said about the grounds on which such a claim rests… rests on the **fallibility of assumptions
> about the reliability of testing procedures** and a distinction between **the strength of evidence and
> the security of an evidence claim**. Robustness can enhance the security of an evidence claim either
> by providing what I call **second-order evidence**, or by providing **back-up evidence** for a
> hypothesis."

**This has a direct consequence for the agreement check.** If two cheap models agree because they share
a blind spot — or because the test-first writer wrote a weak test both satisfy — that agreement is
**back-up evidence**, which cannot raise certainty past the shared verifier's own false-accept rate.
**Second-order evidence** — evidence that your testing apparatus itself is trustworthy — requires a
signal that does not route through the same verifier.

So: the agreement check reduces *incidence*. It cannot, by itself, raise *certainty*. Certainty needs an
independent, differently-generated check. That is a hard limit on the fix, and worth knowing before you
sell it.

### 1.8 Two more, for cross-domain legitimacy

- **Model cards** (Zaldivar et al.): disclose **intended use**, **performance across subgroups**, and
  **out-of-scope** use. Your analogue at goal level already has the raw material — your failure-class
  taxonomy (RIGHT_FILE_wrong_fix 12 inst/8 repos · NO_EDIT_at_all 11 · LOCALIZATION_miss 9–10) is a
  subgroup breakdown. The **out-of-scope field is the mechanism that prevents negative transfer.**
- **Kamath, Jensen & Bennett (1990)**, *The Empirical Appraisal of Economic Theories, Econometric
  Testing and the Logic of Refutation* — explicitly cites Lakatos and proposes an alternative test
  procedure to fix the logical problems with testing economic theory. **Cross-domain transfer of
  Lakatos is established**, which is the precedent for using it as your backbone in coding, biology and
  economics simultaneously.
- **MINSEQE** (and MIAME before it): *Minimum Information about a high-throughput nucleotide
  SEQuencing Experiment* — "the minimum information … needed to enable the unambiguous interpretation and
  facilitate reproduction of the results," by analogy to MIAME. **The minimum-information pattern is the
  template for your claim schema**: name the minimum fields that make the claim interpretable.

---

## 2. The proposed format

Three records. Each has required fields; omitted fields are not "unknown", they are **audit
rejections** — which is exactly your existing `claims.md` validator pattern.

```
GOAL        ← hard core (Lakatos)
  id                      stable, never reused
  statement               what must be true of the world for this to be the right goal
  intended_use            where this goal is believed to apply
  out_of_scope            ← the negative-transfer guard; REQUIRED, may not be empty
  falsifier               the observation that would show the GOAL is misconceived
                           (not a single counterexample — Lakatos)
  domains                 which adapters instantiate it
  status                  active | shifted | abandoned
  problemshift            progressive | degenerating | undetermined   ← computed
  shift_history           when it moved, and what replaced the core

PROCEDURE   ← protective belt (Lakatos)
  goal_id
  signature               failure class · language/framework+version · symbol kind ·
                           test runner        ← keyed on TARGET, never on task wording
  preconditions           typed slots (the plan's abstraction work)
  verifier_id             which check judges it
  status                  candidate | structurally_validated | behavioural_verified
  tests_supporting        with counts and NO-PATCH counts
  tests_failing
  disposition             active | on_probation | withdrawn
  authored_by             procedure_id, not a free-text diff

CLAIM       ← one procedure instantiation, one run (Toulmin + GRADE + nanopub)
  goal_id, procedure_id
  claim                   the assertion, written WITH its qualifier inline
  qualifier               REQUIRED: "for class C under precondition P in environment E"
  warrant                 REQUIRED: why this evidence entails the claim
  exceptions              REQUIRED: known cases where it does not hold
  evidence_class          replayable | controlled_field | observational | generated
  provenance_assertion    run_ids · verifier_id · n · alpha · where_verified
  provenance_theory       which goal, which procedure, what would falsify it
  grade                   risk_of_bias | inconsistency | indirectness | imprecision |
                          publication_bias   → certainty: high|moderate|low|very_low
  replications            independent runs, incl. the ones that failed
  problemshift_input      did this resolve a predicted problem or an unforeseen one?
```

### 2.1 The four rules that make it work

1. **Every claim carries a qualifier.** No qualifier, no claim — it is a status label, which is what
   "Verified" currently is.
2. **Certainty is derived from five named domains, never set directly.** Nobody writes "high". They
   write the five assessments and the level follows. This is what makes it domain-neutral.
3. **A goal's `problemshift` is computed, not declared.** progressive iff ≥1 procedure resolved a goal
   not predicted from the belt; degenerating iff every procedure resolved an anticipated goal. **One
   metric, three domains, computed from records you already keep.**
4. **Negative results are records, not absence.** Nanopublications name them as a publication type. Your
   five wrong-passing answers are the highest-value rows you own.

### 2.2 The same slots, three domains

| Slot | AI coding | Computational biology | Economics / social science |
|---|---|---|---|
| **Goal (hard core)** | "Verified procedural memory cuts cost without cutting task success" | "This pipeline step is reusable across cohorts, not just the discovery cohort" | "Treatment raises Y by δ, under assumptions A1…An" |
| **Procedure** | the change shape | the step + parameterisation | the estimator + specification |
| **Signature** | failure class · language/framework · symbol kind · test runner | assay · pipeline stage · organism · tool version | treatment · estimand · population · period |
| **Verifier** | hidden tests | ground-truth truth set (e.g. GIAB) + pipeline replay | the identification strategy |
| **Evidence class** | replayable | replayable | observational / controlled_field |
| **α (risk of bias)** | test false-accept rate | false-negative rate on the truth set | confounding, weak instruments |
| **Indirectness** | verified here vs elsewhere; applicability | cohort outside the truth set | population/period outside the design |
| **Out of scope** | languages with no hidden tests | species/cohorts outside the truth set | populations/periods outside the design |
| **Problemshift, progressive** | library predicts a **new** bug class it was not tuned on | step transfers to a **new** cohort | estimate holds in a **new** sample or design |
| **Problemshift, degenerating** | only re-solves bug classes already indexed | only works on the discovery cohort | only holds in the original specification |

**Read the last two rows again.** That is the entire cross-domain argument: *the same computable
question* — did the machinery explain something it did not anticipate? — has a well-defined answer in
coding, in biology, and in economics, using each field's own verifier. **That is the format's load-
bearing property, and it is what no single-domain competitor has.**

---

## 3. What this changes in the repo, concretely

| Existing | Becomes |
|---|---|
| `verification_layers 0–5` (ordinal status) | the five GRADE domains, with the level **derived** |
| `applicability.py` preconditions | `PROCEDURE.preconditions` as typed slots — and the direct test of plan item 3 |
| `claim_evidence.py` | `CLAIM.provenance_assertion` + `provenance_theory`, both mandatory |
| `goal_relations` SPECIALIZES | the belt; **still missing** the part-of edge (from the prior-art audit) |
| `goal_abstraction.py` | unchanged — still the hierarchy |
| `.stealth/claims.md` + validator | the enforcement point: a claim missing `qualifier`, `warrant`, `exceptions`, or a grade domain is **rejected**, not warned |
| The 5 wrong-passing answers | `CLAIM` rows with `replications` populated by failures, `risk_of_bias` downgraded |
| `docs/legal/VERIFICATION_DISCLAIMER.md` | rewritten once certainty is derived, not declared |

## 4. Ranked next steps

1. **Write the minimum-information field list** (MINSEQE pattern) and make it the validator's rejection
   criteria. Hours, and it makes every later change cheap.
2. **Add `qualifier`, `warrant`, `exceptions` as required claim fields.** The single highest-value
   change; it makes "Verified" unfalsifiable-as-written impossible.
3. **Replace `verification_layers` with the five GRADE domains** and derive the level. Mechanical once
   the domains are recorded.
4. **Compute `problemshift`** from existing records. This is the field that answers "does the theory
   work?" and it needs no new data.
5. **Name the identification assumptions for the −49% claim** — same task set, same grader, arm A
   unchanged — and state which comparisons are not fully identified (the page suites).
6. **Add `out_of_scope` to every Goal**, sourced from the existing failure-class taxonomy.
7. **State the back-up/second-order limit on the agreement check** wherever it is offered, because it
   reduces incidence and cannot raise certainty.
8. **Defer the part-of edge** until 1–4 land; the claim schema is worth more first, and the edge
   without honest claims makes importance scores worse, not better.

## 5. What I did not verify

- Whether any existing system implements GRADE domains for AI artifacts. I found none in this sweep;
  the agent-evaluation survey (Nageshwaran et al., *AI Review*, Sept 2026, 259 studies) is the most
  likely place a prior taxonomy lives and I did not read it in full.
- Nanopublication tooling maturity (Trusty URIs, profiles, trusty URIs for retraction). The guidelines
  draft exists and is current; I did not assess implementation.
- Whether GRADE's upgrade domains for non-randomised evidence transfer usefully to generated evidence
  (our procedure-extraction corpus is neither randomised nor observational).
- Whether "problemshift" can be computed reliably from your existing records or needs new bookkeeping
  at capture time. This is the one item on the list that might not be free.