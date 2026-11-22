# Open Agentic Literature

**A shared language and a venue for rigorously analysing agentic architectures.**

Research notes — 2026-11-20. Terminology, the questions worth asking, what the
literature already establishes, and what the platform has to be.

---

## 0. The thesis in one paragraph

Agent architectures are published faster than they are checked. A paper claims a
decomposition, a topology, or a protocol is better; the claim is usually not separable
from the scaffold it was measured in, so nobody can tell whether the *architecture*
caused the result or the harness did. Meanwhile the format work is real and converging —
L-HTN for natural-language procedures, RDF/SHACL for semantics, versioned registries for
skills — but it is descriptive, not evidentiary. **The gap is a place where an
architecture claim can be stated precisely enough to be attacked.** Not a marketplace,
not a forum: a venue where the unit is a falsifiable claim, the artefacts are pinned, and
the evidence is machine-checkable.

---

## 1. Terminology

Words we must fix before any of this is discussable. Each entry gives the term, what it
means here, and the confusable neighbour.

### 1.1 The thing being analysed

| Term | Meaning here | Not to be confused with |
|---|---|---|
| **Agent** | An autonomous component `(role, objectives, planning, memory, tools, reasoning fn)` — the definition used in the MAFBench taxonomy | A model. A model is the reasoning function inside an agent |
| **Harness / scaffold** | The code wrapped around the model: system prompt, tool inventory, stopping rule, context management | The agent. Two papers on "the same model" usually compare harnesses |
| **Architecture** | The commitments that fix orchestration, communication, and environment — not the prompt text | The prompt. Prompts are an implementation detail of a harness |
| **Topology** | Who may talk to whom: star, chain, hierarchy, mesh | Protocol. Topology is *shape*; protocol is *rules* |
| **Orchestration** | Who decides the next step: fixed graph, router, or emergent | Coordination. Orchestration is control; coordination is the joint outcome |
| **Swarm** | Many agents, dynamic membership, emergent control, no fixed graph | Multi-agent system. A MAS may be a fixed sequential pipeline |
| **Protocol** | The message contract: A2A, ACP, ANP, Agora, MCP | Transport. Protocol is semantic; transport is bytes |

### 1.2 The procedural layer (our L-HTN work)

| Term | Meaning here | Neighbour |
|---|---|---|
| **Goal** | A declared problem, in the user's words | Task. A task is a unit of work; a goal is why it exists |
| **Way / Procedure** | A reusable, versioned decomposition serving a Goal | Prompt template. A Way has preconditions and effects |
| **Primitive task** | A step handed to an operator (a tool call, a human) | Compound task |
| **Method / decomposition** | One way to accomplish a compound task | A plan. A plan is an *instance*; a method is reusable |
| **Precondition** | What must hold before a method applies — in prose, optionally a closed predicate | Assumption. An assumption is unstated; a precondition is declared |
| **Effect / postcondition** | What holds after execution | Outcome. Outcome is measured; effect is claimed |
| **Applies-when** | The NL applicability sentence a router uses | Trigger. A trigger is temporal; applies-when is semantic |

### 1.3 The evidence layer — where most vocabulary is missing

| Term | Meaning here | Why it matters |
|---|---|---|
| **Claim** | A falsifiable statement about an architecture, with a comparison, a metric, and a scope | The unit of the venue. Not a result, not a finding |
| **Unit of comparison** | The axis a claim varies: topology, protocol, decomposition, model, harness | Papers usually vary several at once and report one number |
| **Confound** | A difference that could explain the result instead of the claimed cause | The double measurement confound below is the canonical one |
| **Harness ownership** | Who holds execution-critical decisions — the scaffold or the model | Currently "an uncontrolled axis wherever we probed it" |
| **De-scaffolding** | Moving execution-critical decisions from scaffold to model | What makes a score about the model |
| **Awareness** | Whether the agent is told it is being evaluated | Agents behave differently when told; rarely reported |
| **Test oracle** | The rule that decides pass/fail | Its defects are invisible in a score table |
| **Oracle failure** | An oracle that scores a state it did not intend to score | Documented to move a benchmark from 94.3% to 65.5% |
| **Construct validity** | Whether the metric measures the capability claimed | Distinct from reliability |
| **Internal validity** | Does the claim hold within this setup and population | Hardt's replication ladder |
| **External validity** | Does it hold under a different population | The one everyone claims and rarely tests |
| **Reliability** | Repeatability under re-run, resampling, re-seeding | Reported as a mean, almost never as a distribution |
| **Inter-rater reliability (IRR)** | Agreement between graders, chance-corrected | 26 of 55 agentic eval papers report none |
| **Tail risk / worst-case** | Behaviour in the bad decile, not the mean | The number an operator needs |
| **Disclosure** | What the paper *says* about how it ran | A disclosure score is not a quality score |
| **Reproduction** | Same code, same data, same harness → same number | Code re-execution |
| **Replication** | Same claim, fresh conditions → same conclusion | Harder, rarer, better |
| **Ablation** | Remove one component, hold everything else | The only way to attribute effect |
| **Attribution** | Which component produced the gain | Requires ablation, not correlation |
| **Ceiling / floor effect** | A task so easy or hard it cannot discriminate | Common in agent benchmarks |

### 1.4 The semantic layer

| Term | Meaning here | Note |
|---|---|---|
| **Ontology** | The vocabulary and its relations (`ioa:Capability`, `ioa:Intent`) | OWL describes; it does not validate |
| **Shape** | A constraint a graph must satisfy (SHACL) | This is the validator |
| **Open-world assumption** | A missing fact is unknown, not false | Why OWL "explains away" agent errors |
| **Alignment** | An accepted mapping between two vocabularies | Must be an artefact, not a similarity score |
| **Indeterminate** | Compatibility could not be established | Distinct from incompatible — a real third state |
| **Provenance** | Who produced an assertion, from what, validated how | PROV-O; RDF 1.2 annotations |
| **Content digest** | The hash of the exact bytes | Semver is a label; the digest is the identity |

---

## 2. The questions worth asking

Written as the venue would phrase them. Each is answerable, and each has a
corresponding "why would this architecture be better *here*" form.

### 2.1 Attribution

1. **What exactly is the unit of comparison?** Is the claim about the model, the
   harness, the topology, the protocol, or the decomposition — and is only that varied?
2. **Who holds the execution-critical decisions?** If the scaffold decides when to stop,
   the score is about the scaffold.
3. **What did the ablation remove?** A claim of "the swarm helps" requires removing the
   swarm and holding the tasks, models and budget fixed.
4. **Is the gain larger than the harness variance?** Framework choice alone has been
   measured to change latency by >100× and coordination success from >90% to <30%.

### 2.2 Validity

5. **Is the metric measuring the capability it names?** A "success rate" that counts an
   empty response as success measures nothing.
6. **What is the oracle, and has it been audited?** Oracle defects are invisible in the
   results table and have been measured to change rankings.
7. **What is the failure taxonomy**, and is it reported at task granularity? Uncategorised
   failures hide the mechanism.
8. **Does the result survive resampling?** Report reliability, not just the mean.
9. **Was the agent aware it was being evaluated?**

### 2.3 Coordination and swarm

10. **Does coordination pay for itself?** Compare against the N=1 oracle: does splitting
    the work beat one agent with the same total budget?
11. **Where does coordination fail?** Information acquisition or information integration?
    SILO-BENCH localises this: agents hold nearly all required information and still fail
    to synthesise it.
12. **Does the protocol choice dominate?** ProtocolBench measured completion time varying
    36.5% across protocols on one scenario.
13. **Does it degrade gracefully or multiplicatively?** Relative coordination cost has
    been measured reaching 80–100% at 50 agents.

### 2.4 Decomposition (L-HTN)

14. **Is the decomposition necessary, or is it a checklist?** A decomposition without
    preconditions cannot be routed and cannot fail informatively.
15. **Does prose survive renaming?** Symbolic HTNs reached 0% success under a lexical
    change; L-HTNs stayed near 95%.
16. **Is the applicability condition machine-checkable, or only judgeable?** If only
    judged, that must be stated as a limitation, not hidden.

### 2.5 The debate question — and how not to have worthless debate

17. **What would change your mind?** A claim with no specified falsifier is an opinion.
18. **Is disagreement about the claim or the measurement?** Most agent arguments are
    the second, dressed as the first.

---

## 3. What the literature already establishes

The single most useful finding for the venue: **the critiques exist, are rigorous, and
are not being operationalised.** A platform that operationalises them is novel.

### 3.1 The measurement crisis is documented and quantified

| Finding | Source |
|---|---|
| **26 of 55 agentic evaluation papers report no chance-corrected inter-rater statistic of any kind**; ~80% use mismatched, incomplete or absent IRR | [Measurement Without Validity, arXiv 2608.00794](https://arxiv.org/html/2608.00794v5) |
| A validity model where degradation compounds multiplicatively across task generation, simulator calibration, and automated judgment: `V_total ≤ V₁×V₂×V₃` | same |
| **A double measurement confound**: execution-critical decisions held by the scaffold, and scoring that may not reflect correctness | [The Double Measurement Confound, arXiv 2609.09218](https://arxiv.org/abs/2609.09218) |
| **Scaffold ownership is "an uncontrolled axis wherever we probed it"** | same |
| SWE-bench-Verified uses insufficient test cases; **τ-bench counts empty responses as successes**; errors can over- or under-estimate by up to **100% relative** | [Agentic Benchmark Checklist, NeurIPS](https://dl.acm.org/doi/10.52202/085713-5547) |
| An oracle that reset a database and never restored it made **94.3% read as 65.5%** on replay, and **changed which agent was strongest** | [Oracle Failures, arXiv 2610.16067](https://arxiv.org/abs/2610.16067) |
| **None of eight agent-benchmark papers disclose inference cost**; none fully disclose a content-addressed environment image; mean disclosure score **0.38** | [What Twelve LLM Agent Benchmark Papers Disclose, arXiv 2605.21404](https://arxiv.org/abs/2605.21404) |
| Benchmark flaws are "widespread and materially affect evaluation outcomes" | [AgentSuite / COBA, ICML 2026](https://icml.cc/virtual/2026/poster/66603) |

### 3.2 Architecture dominates, and that is the whole opportunity

- **Framework-level design choices alone can increase latency by over 100×, reduce
  planning accuracy by up to 30%, and lower coordination success from above 90% to below
  30%** — [Architectural Design, Not Only Model Intelligence, arXiv 2602.03128](https://arxiv.org/abs/2602.03128). This paper also supplies a usable formal
  vocabulary: `agent = (R, Y, P, S, T, ·)`, `framework = ({a}, O, C, E)`.
- A **taxonomy and decision model** over 66 primary studies, validated by 12 experts —
  [Architecture options for foundation-model agents, JSS 2026](https://doi.org/10.1016/j.jss.2026.113109).
- **Coordination Primacy Hypothesis**: coordination protocol design may drive quality
  more than model scaling — explicitly presented as *falsifiable and not yet validated*,
  because "evaluation infrastructure that does not yet exist in the field"
  ([arXiv 2603.27539](https://arxiv.org/abs/2603.27539)). **That sentence is our
  product requirement.**

### 3.3 Swarms and protocols: real numbers

- **SILO-BENCH** ([ACL 2026](https://aclanthology.org/2026.acl-long.1354.pdf)): the
  **Communication-Reasoning Gap** — agents form sensible topologies and exchange
  information, then fail to synthesise it. Success 36.9% (DeepSeek-V3.1), 16.9%, 8.2%
  across models; relative coordination cost reaches **80–100% at N=50**; at N≥50 on the
  hardest tasks success is **0%** while partial correctness stays 8–16%.
- **ProtocolBench** ([PMLR v306](https://proceedings.mlr.press/v306/du26l.html)):
  completion time varies **36.5%** across protocols; mean latency differs by **3.48 s**;
  a constraint-aware router recovers **18.1%** on recovery time.
- **SwarmBench** ([arXiv 2608.30661](https://arxiv.org/abs/2608.30661)): orchestration
  ability differs substantially across models, and an experience-replay method improves it
  — with a clean ablation showing which experience components matter.

### 3.4 The format work is converging — and is descriptive only

- **L-HTN** ([Smith & MacLellan, ACS 13, 2026](https://tail.cc.gatech.edu/files/smith-acs-2026.pdf)):
  symbolic HTNs **collapse to 0%** under lexical and reduced-information variation;
  L-HTNs stay ~95–99%. Stated limitation: *"evaluating whether linguistic preconditions
  hold remains a language-model judgment and is not independently verified by the
  planner."*
- **AgentSPEX** ([arXiv 2604.13346](https://arxiv.org/html/2604.13346v1)): NL workflows in
  YAML with typed steps, branching, submodules — "easy to version-control, diff and share".
- **Lean4Agent** ([arXiv 2606.06523](https://arxiv.org/html/2606.06523v3)): Hoare-style
  pre/post contracts per LLM step, verified in Lean; localises which step violated which
  predicate. This is the most rigorous instance of our own design and should be the
  closest read.
- **SOTERIA** (NeurIPS 2026): formally verified planning with runtime enforcement.
- **IETF draft-zhang-dmsc-ioa-semantic-interaction**: an ontology profile for agent
  capabilities and intents in RDF/JSON-LD, with a **three-valued compatibility outcome:
  Compatible / Incompatible / Indeterminate**, and the rule that *"descriptive similarity
  does not establish equivalence"* — which is precisely the mistake our similarity
  retrieval makes today.
- **IETF draft-xu-efficient-agent-discovery-profile**: NL descriptions plus tags, with the
  instruction that descriptions *"should avoid unsupported superlatives such as 'best'."*
- **Google Agent Registry**: `Publisher → Skill → SkillRevision`, revisions immutable,
  default pointer, SHA-addressed. **MLflow Skill Registry**: draft/active/deprecated/
  deleted with content digests. **Skill Provenance**: found **66 of 1,210 (5.5%)** skill
  instruction changes with **no version movement** — a version label alone does not
  show whether bytes changed.

---

## 4. What the platform has to be

Not a forum. A **venue whose unit is a checkable claim**, with four properties.

### 4.1 A claim is a first-class object with a schema

```
claim {
  statement:       "Hierarchical decomposition beats a flat agent on task family F"
  unit:            topology | protocol | decomposition | harness | model
  varies:          exactly one of the above
  comparison:      baseline (pinned) + treatment (pinned)
  metric:          named, with the oracle named
  scope:           task family, model set, budget
  falsifier:       "success difference < 5pp at n≥30 paired trials"
  evidence:        runs, each with harness digest + environment image + seed
}
```

**The falsifier field is mandatory.** A claim without one is not published as a claim;
it is published as a *note*, visibly marked as such.

### 4.2 Paired, not pooled

The venue's statistical default is **paired comparison** (McNemar on discordant pairs),
because it is the only design that attributes effect with small n — and because our own
harness already computes exactly this. Report **discordant pairs**, never a bare
percentage. This is the rigour the field lacks (§3.1) and we already have the machinery.

### 4.3 Pinning is the price of admission

Following the disclosure schema that scored the field at 0.38:

| Must pin | Why |
|---|---|
| Harness: name, version, **system-prompt hash**, tool inventory, stopping rule | The uncontrolled axis |
| Environment: **content-addressed image digest**, not a tag | Tags are mutable |
| Inference: model alias, provider, sampling, **seed**, **run date** | Closed aliases are silently re-pointed |
| Cost: tokens, tool calls, wall clock, rupees | None of eight papers disclose it |
| Failures: categorised counts at task granularity | Uncategorised failures hide mechanism |
| Oracle: what decides pass/fail, and its audit | Invisible defects reorder results |

A submission that cannot pin these is still publishable — **as a lower evidence tier**,
labelled. Refusing it entirely just makes people publish elsewhere.

### 4.4 Natural language in, structure out — with the semantic layer as guard

Authoring is prose (L-HTN's lesson: writing predicates is why symbolic HTNs fail). But
the *stored* object carries:

- a **content digest** and an immutable revision (a semver label is not identity);
- **preconditions and effects** as prose, with an optional closed predicate where a
  violation must be a hard disqualification;
- **provenance** on every assertion (who, from what, validated how);
- a **three-valued applicability outcome** — applies / does not apply / **indeterminate** —
  because "could not establish" is the honest answer far more often than "false".

---

## 5. Engagement without worthless debate

The previous document's rules carry over, sharpened for a research venue.

| Do | Don't |
|---|---|
| **Attack the claim, not the author.** A counter-claim is a claim, same schema. | Reply threads. One level, always |
| **Reproduce before rebutting.** A rebuttal that includes a run is promoted above one that does not | Upvotes on claims — popularity is not evidence |
| **Require a falsifier.** EU rules of thumb: if nobody can say what would refute it, it is a blog post | "This doesn't work in my experience" with no run |
| **Publish negative results.** A well-pinned failure is worth more than a vague success | A "results" section with only wins |
| **Grade evidence, don't rank people** | Leaderboards of authors |

The unit of credit is the **accepted claim** and the **reproduction**, not the post count.

---

## 6. What to build first

1. **The claim schema** (§4.1) as a JSON Schema + a prose form. Without it there is no
   venue, only posts.
2. **The pinning digest** — a single value computed from harness + environment + sampling
   + oracle, so two claims can be checked for comparability automatically.
3. **Paired-comparison reporting** reusing the existing McNemar machinery.
4. **The evidence tiers** — pinned-and-reproduced / pinned-only / unpinned — shown
   everywhere a claim appears.
5. **One seeded corpus**: take SILO-BENCH's coordination gap and L-HTN's lexical-robustness
   result, re-state each as a claim, and run them through the schema. If the schema cannot
   express a published finding, the schema is wrong — that is the test.

---

## 7. Perplexity prompts

Written to be pasted as-is. Each is scoped to one *resolvable* question, asks for
primary sources, and demands the pinning fields from §4.3 so the answers are usable in
the venue rather than as background reading.

**How to use:** run them in order — each round's output names the sources the next round
should chase. Ask for **papers and artefacts with links**, not summaries. Where Perplexity
gives a claim, the follow-up is always the same: *"what did they hold fixed, and who owned
the execution-critical decisions?"*

---

### Round 1 — the evidence standard

> **P1.** Find the strongest existing standards for *reporting* an AI evaluation
> transparently — specifically STREAM (Standard for Transparently Reporting Evaluations in
> AI Model Reports), the Agentic Benchmark Checklist (ABC), model cards, and
> datasheets-for-datasets. For each: what fields does it require, which are mandatory vs
> recommended, and is there a machine-readable schema (JSON Schema, YAML). I want the exact
> field lists, not a summary.

> **P2.** Is there any existing *pre-registration* mechanism for AI/agent experiments — a
> registry where a hypothesis and analysis plan are lodged before running? Look at
> OSF/AsPredicted use in ML, the "Threats of a Replication Crisis in Empirical Computer
> Science" literature, and any 2025–2026 proposal specific to LLM or agent evaluation.
> Report what exists, what was tried and abandoned, and why.

> **P3.** Survey the measured magnitudes of *evaluation-harness effects* in LLM agent
> benchmarks. I want numbers: how much do framework choice, scaffold, stopping rule,
> tool-inventory, and system prompt each move the score? Include MAFBench (arXiv 2602.03128)
> and the "Double Measurement Confound" (arXiv 2609.09218) but go beyond them — find any
> paper that reports the *variance decomposition* of an agent score into
> model / harness / task / seed.

---

### Round 2 — coordination and protocols

> **P4.** Give me every published comparison of agent communication protocols (MCP, A2A,
> ACP, ANP, Agora) that reports *quantitative* results — latency, token overhead, success
> rate, failure recovery — with the benchmark used. Include ProtocolBench and any security
> threat-modelling comparisons. For each: which protocols, which tasks, what was held
> fixed, and what the numbers were.

> **P5.** Find studies that test whether *multi-agent* beats a *single agent with the same
> total compute/token budget*. I want the "N=1 oracle" style comparisons (SILO-BENCH is
> one). What is the emerging consensus on when splitting work across agents helps, and
> when it destroys value? Report effect sizes, not opinions.

> **P6.** On the Communication-Reasoning Gap: is anyone working on *why* agents acquire
> distributed information but fail to synthesise it — as opposed to benchmarking that they
> do? Look for mechanistic, ablation, or interpretability work, not new benchmarks.

---

### Round 3 — the procedural / NL layer

> **P7.** Beyond L-HTN (Smith & MacLellan, ACS 2026), find all work that represents agent
> procedures in *natural language* while keeping a machine-checkable structure — Lean4Agent,
> SOTERIA, AgentSPEX, NormCode, and anything else. For each: what is formal, what is prose,
> and how are the prose parts verified (LLM judge, human, predicate)?

> **P8.** What is the state of the art in *verifying* that an LLM step met its
> postcondition? Compare LLM-as-judge, executable predicates, formal methods (Lean, Dafny),
> and runtime monitors on cost, reliability and what they can catch. Include any measured
> false-accept / false-reject rates.

> **P9.** Has anyone measured *semantic drift* in agent skills or procedures — where a
> natural-language instruction changes meaning without a version bump? Skill Provenance
> reports 5.5% of instruction changes with no version movement. Find follow-up work, and
> any study of how much behaviour changes when prose is edited equivalently.

---

### Round 4 — semantics and identity

> **P10.** Summarise the IETF agent-semantics drafts (draft-zhang-dmsc-ioa-semantic-
> interaction, draft-xu-efficient-agent-discovery-profile, ADP/ANP) and any W3C activity on
> agent capability description. I want the *vocabulary* (classes, properties), the
> three-valued compatibility semantics, and how they say alignment between ontologies
> should be accepted or rejected.

> **P11.** Find work applying SHACL (not OWL) as the validation layer for LLM-produced
> knowledge — especially the generate–repair loop where the conformance report is fed back
> to the model. What are measured repair success rates and failure modes? Include
> ontology-to-tools compilation (arXiv 2602.03439) and the RDF 1.2 provenance work.

> **P12.** Is there any published standard for *claiming* a capability in a way that can be
> falsified — e.g. a capability claim bound to evidence, with a stated scope and a
> verification procedure? Look at Veri/verifiedagent.ai, the "Contextual Capability
> Verification" framework, and any agent-card extension that carries evidence rather than
> description.

---

### Round 5 — the negative results

> **P13.** Collect published *negative and null results* about agent architectures: swarms
> that did not help, multi-agent debate that degraded accuracy, decompositions that hurt,
> memory that made no difference. I specifically want well-run failures, with the setting
> they failed in. Include sycophancy/conformity work (e.g. "Not All Flips Are Conformity",
> arXiv 2606.00820).

> **P14.** On multi-agent debate specifically: what does the evidence say about when
> debate improves correctness versus when it produces confident consensus on a wrong
> answer? I want measured effect sizes and the conditions that predict each.

> **P15.** Find "Stop Means Stop: Measuring and Repairing the Enforcement Gap in
> Agent-Framework Control Primitives" (2026) and any related work on whether agent
> frameworks actually honour their own control primitives — stopping, cancellation, budget
> limits. What fraction of frameworks fail, and how is that measured?

---

### Round 6 — the venue question

> **P16.** Are there existing venues, workshops, journals or live review platforms where
> agent architectures are compared under a shared schema — as opposed to papers published
> independently? Look at NeurIPS/ICML workshops on agents and evaluation, the
> ML-reproducibility challenge model, ReScience, and any 2026 attempt at a "living
> benchmark" or "claims registry" for agents.

> **P17.** What has been tried for *continuous, adversarial* review of technical claims
> outside AI — mathematics (Lean/Zulip projects), cryptography (ePrint), security (CVE),
> benchmarks (MLPerf round rules)? For each: what makes a submission admissible, who
> adjudicates, and how is disagreement resolved. I want the governance model, not the tooling.

---

## 8. Findings from the sweep

Run 2026-11-20 across OpenAlex, arXiv and web search. The full 17 prompts were executed.
**Ten findings change or sharpen the design**, and three are decisive.

---

### 8.1 DECISIVE — STREAM already is the schema we were going to invent

[STREAM (ChemBio)](https://arxiv.org/abs/2508.09853), Governance of AI, developed with **23
experts** across government, civil society, academia and frontier labs:

> **28 criteria in six categories**: Threat Relevance; Test Construction, Grading and
> Scoring; Model Elicitation; Model Performance; Baseline Performance; Results
> Interpretation — each with a **"minimum"** and a **"full compliance"** tier.

Three of its criteria are exactly ours, already written by someone else:

- **4(ii)** requires confidence intervals **and the number of evaluation runs** — the
  Wilson/`n` requirement, in a published standard.
- **4(iii)** requires disclosure of whether **ablation experiments** or alternative
  testing conditions were performed.
- **6(ii)** requires the report to state **what evidence could have falsified the
  conclusion, and whether those conditions were pre-registered**.

**Consequence: do not invent a schema.** Adopt STREAM's structure as the parent, add the
two things STREAM deliberately excludes (harness specification, cost reporting — the fields
where the agent audit scored **0.38**), and publish the delta. That is a defensible
contribution and a much stronger position than a novel schema.

STREAM also states the honesty rule we need verbatim:

> "Following the STREAM standard only indicates that the model's evaluation has been
> reported with sufficient detail for strong third party judgements to be made."

That is the same distinction as *disclosure ≠ quality* in §3.1. Keep it as the venue's
first line.

---

### 8.2 DECISIVE — the "Stop Means Stop" result is a perfect first claim

[arXiv 2607.14166](https://arxiv.org/abs/2607.14166), IEEE Access 2026. The finding:

- Six widely-used frameworks expose approval gates, cancellation and timeouts whose
  documentation implies **barrier semantics**.
- **None of the six honours it.** A *sibling leak*: the gate suspends its own branch while
  a concurrent sibling's side effect commits during the pause — so rejection is powerless.
- Reproduced in **5/6** frameworks, across **4 execution models**, **2 runtimes**.
- Live models: **215 of 1,200 runs** leaked. `P(leak | emitted) = 1.00`.
- Repair (SOUNDGATE): **0/1,200** leaked; ~1 ms per admission.

**Why this is the perfect first claim for the venue:** it is a claim about *architecture*
(not model), it is a **counter-example to documented behaviour**, it is reproducible
without an LLM at all (`model-free differential probes`), and it has a clean
falsifier. It demonstrates the venue's whole thesis — *"why would this framework be better
here"* answered with a measured, attributable number.

---

### 8.3 DECISIVE — debate empirically harms accuracy, and we now have the numbers

This is the strongest evidence for §5 (engagement without worthless debate), and it is not
a hunch.

[**Talk Isn't Always Cheap** (arXiv 2509.05396)](https://arxiv.org/html/2509.05396):
across 100 samples × 5 seeds, debate **decreased** accuracy in most configurations —
including where stronger models outnumbered weaker ones:

| Task | Group | Before debate | After | Δ |
|---|---|---|---|---|
| MMLU | 3× Mistral-7B | 33.6 ± 1.8 | 24.4 ± 2.9 | **−9.2** |
| MMLU | 1× Llama + 2× Mistral | 40.0 ± 2.1 | 28.0 ± 1.2 | **−12.0** |
| CommonSenseQA | 2× Llama + 1× Mistral | 58.2 ± 3.8 | 50.2 ± 3.9 | **−8.0** |

Mechanism: **correct→incorrect flips exceed incorrect→correct flips**, and *"social
pressure"* dominates — agents that resist in round 1 flip by round 2. Adding a
correctness payoff to the prompt **did not help** and sometimes made it worse.

Corroborated and extended:

- [**When and Why Does Multi-Agent Debate Fail** (arXiv 2510.20963)](https://arxiv.org/html/2510.20963):
  both debate paradigms suffer **"debate hacking"** — competitive debate collapses to
  *cheap talk*; consensus-seeking *"filters out informative disagreements for premature
  consensus."*
- [**Stop Overvaluing Multi-Agent Debate** (arXiv 2502.08788)](https://arxiv.org/abs/2502.08788):
  systematic evaluation of 5 MAD methods × 9 benchmarks × 4 models, finding *"limited
  benchmark coverage, weak baseline comparisons, and inconsistent setups."*
- [**When collaboration fails** (Sci Rep 2026)](https://doi.org/10.1038/s41598-026-42705-7):
  one adversarial agent lowers group accuracy **10–40%** and raises consensus on wrong
  answers by **>30%**; more agents and more rounds **do not** mitigate it; Best-of-N and
  RAG **amplify** it.
- [**Review Rarely Beats the Stronger Model** (arXiv 2610.15897)](https://arxiv.org/abs/2610.15897):
  review gains are often **capability transfer**, not verification — reviewing "adds 33.2
  points" but the reviewer alone is 6.4 points better.

**Consequence for the platform: this is the empirical basis for the "no votes, no reply
chains" rule.** Debate is not a neutral format that can be moderated into usefulness; it
is measurably adversarial to correctness. The venue should host *claims and repro runs*,
and explicitly not host argument.

---

### 8.4 Estimates and flavours — spurious precision, and a free prerequisite

[**When Agent Evaluation Becomes Unreliable** (arXiv 2610.16067)](https://arxiv.org/abs/2610.16067)
gives a number we should quote everywhere: an unrepaired oracle made **94.3% read as
65.5%**, and **changed which agent was strongest**.

[**Terminal-Bench 2.0 → 2.1** (arXiv 2611.08417)](https://arxiv.org/abs/2611.08417) is the
best single precedent for a *living* benchmark:

- Defects in **~30%** of a benchmark that had already been heavily reviewed and red-teamed.
- Most common failure: **instruction–verification mismatch (57% of repaired tasks)** —
  agents producing *valid* solutions the verifier rejected.
- **Task drift 43%**: external state (package versions, APIs, web content) breaks tasks.
- **Environment issues 29%**: resource budgets too tight, so trials failed on limits rather
  than correctness.
- After repair: accuracy **+10pp**, leaderboard moves **up to three ranks**, Spearman ρ=0.958.
- **Budgets are part of the measurement spec**: extending a time limit *closed a 20pp
  harness gap* on one model and shrank a 30pp gap to 5pp on another.

**Consequence: "resource budget" must be a required pinning field**, not an afterthought.
It is the cheapest confound to control and one of the largest.

They also name the problem we exist to solve: **"benchmark maintenance debt"**.

---

### 8.5 The variance decomposition does not exist — which is our opening

I asked for the decomposition of an agent score into model / harness / task / seed.
**Nobody has published it.** What exists is weaker but suggestive:

- [MAFBench (arXiv 2602.03128)](https://arxiv.org/abs/2602.03128): framework choice alone —
  latency **>100×**, planning accuracy **−30%**, coordination success **>90% → <30%**.
- The [Disclosure audit](https://arxiv.org/abs/2605.21404): *"harness drift"* — two papers,
  same benchmark, different unpinned scaffolds, incomparable numbers.
- [DBLP/cost]: **none of eight agent-benchmark papers disclose inference cost.**

**This is the venue's flagship artefact.** A simple, well-pinned decomposition
(how much of score variance is model vs harness vs seed vs task) would be the most cited
thing in the space, and it is *cheap to produce* on an existing harness.

---

### 8.6 Formats: the registry problem is solved; the evidence problem is not

- [**Skill Constellations** (arXiv 2610.11169)](https://arxiv.org/abs/2610.11169): agent
  skills are *"a software supply chain without a registry, versions or provenance"* —
  the first study tracing who copied a skill from whom on GitHub.
- [**SkCC** (arXiv 2605.03353)](https://arxiv.org/abs/2605.03353): frameworks are
  **highly sensitive to prompt formatting**, producing *"large performance variation for
  the same skill."* A skill authored once as format-agnostic Markdown needs per-framework
  rewrites.
- [**Toward User Comprehension Supports for LLM Agent Skill Specifications** (arXiv 2605.19362)](https://arxiv.org/abs/2605.19362):
  878 cybersecurity skills measured on four *comprehension anchors* — operational basis,
  output contract, boundary disclosure, example capability. Most under-specify.
- **Google Agent Registry**: `Publisher → Skill → SkillRevision`, revisions immutable,
  digest-addressed, `defaultRevision` pointer, lifecycle states. **MLflow**: draft/active/
  deprecated/deleted + content digest. **Skill Provenance**: **66/1,210 (5.5%)** changes
  with **no version movement**.
- [**IETF ioa-semantic-interaction**](https://www.ietf.org/archive/id/draft-zhang-dmsc-ioa-semantic-interaction-03.html):
  RDF/JSON-LD profile with **three-valued compatibility (Compatible / Incompatible /
  Indeterminate)** and the rule *"descriptive similarity does not establish equivalence."*

**Consequence:** adopt the Google/MLflow revision model verbatim (it is already right), and
add the one thing none of them carries — **evidence bound to the revision**. The IETF
three-valued outcome should be our applicability semantics.

---

### 8.7 L-HTN's gap is being closed by Lean4Agent — read that first

[**Lean4Agent** (arXiv 2606.06523)](https://arxiv.org/html/2606.06523v3) does what L-HTN
says it cannot: **Hoare-style pre/post contracts per LLM step, verified in Lean**, with a
`LLMExec` assumption (if preconditions hold, execution produces postconditions), plus a
**third layer that checks that assumption on real trajectories** and localises the
violating step.

Also: [**LLM-as-a-Verifier** (arXiv 2607.05391)](https://arxiv.org/abs/2607.05391) treats
verification as *"a new scaling axis"* with fine-grained scoring; and
[**Three Ways Classical Test Theory Can Mislead About LLM Judges** (arXiv 2609.29709)](https://arxiv.org/abs/2609.29709)
shows single-judge reliability statistics **cannot isolate the judge** — *"one judge under
one prompt supplies no variance component of its own."*

**Consequence:** our applicability layer should be specified as *predicate-where-decidable,
judge-where-not, and always labelled which*. And an LLM-judged precondition must never be
reported as verified.

---

### 8.8 Governance: three existing models, one is a near-perfect fit

| Model | Admissibility | Adjudication | Fit for us |
|---|---|---|---|
| [**ePrint**](https://eprint.iacr.org/operations.html) | Any author; editors check scope and that claims **contain proofs or convincing arguments**; **no verification of correctness** | Editorial, minimal | ✅ The *submission floor* |
| [**ReScience C**](http://rescience.github.io/) | A **replication** of a specific work, with open code, and **explicitly accepts failures to replicate** | Two reviewers must **run it and get the same numbers** | ✅ The *reproduction track* |
| [**Mathlib / Zulip**](https://mathlib.org/contribute/pr-review.html) | PR + review; **`maintainer merge`** by trusted reviewers | Maintainers; **"a history of helpful reviews is a key criterion"** for promotion | ✅ The *trust ladder* |

ReScience C's framing is the sharpest thing in the whole sweep:

> **"Reproducibility merely guarantees that all the ingredients of a computation are well
> documented. It does not imply that any of them are correct.** … A successful replication
> shows that two teams have produced independent implementations that generate equivalent
> results."

And its exclusion rule is the anti-collusion mechanism we need: *"you cannot submit the
replication of your own research, nor the research of your close collaborators."*

**Consequence:** the venue needs **two tracks**, not one — a *claim* track (ePrint-like
floor) and a *replication* track (ReScience-like, accepting negatives), with Mathlib's
**reviewer ladder** as the trust model. That is a coherent, tested governance design and it
requires almost no invention.

---

### 8.9 What is already being built on top of us

Worth knowing before we claim novelty:

- [**Benchmark Radar** (arXiv 2609.11115)](https://arxiv.org/abs/2609.11115) — *"a living
  database and search engine"* for AI benchmarks.
- [**EvoProbe** (arXiv 2610.19433)](https://arxiv.org/abs/2610.19433) — evaluation-driven
  benchmark *evolution*, closing the loop from diagnostics to new tasks. In its cross-round
  test all eight models scored **lower** (mean outcome −11.8%, Pass^3 −40.0%).
- [**LiveBench** (arXiv 2406.19314)](https://arxiv.org/abs/2406.19314) — refreshes **1/6**
  of questions per update, ~6-month full turnover, one-month publication lag for
  contamination resistance.
- [**HANDBOOK.md** (arXiv 2607.25398)](https://arxiv.org/abs/2607.25398) — benchmarks
  whether a *long binding policy document* actually constrains an agent over a tool-use
  horizon. This is close to our "does the Way apply" question and should be read.

**None of them hosts user-authored claims with attribution.** The gap stands — but the
adjacent surface is getting busy, which argues for moving on the claim schema now.

---

### 8.10 Corrections to this document

Two things in §3–§7 need updating in light of the sweep:

1. **§4.1's claim schema should be derived from STREAM**, not invented (§8.1). Keep our
   additions (harness digest, cost, falsifier) as an explicit, published delta.
2. **§7's prompt P3** ("find the variance decomposition") resolved to *"it does not
   exist"* — which is better than a citation. It is now the flagship artefact (§8.5), not
   a background question.

---

## 9. The open-Replit / open-Polsia question

Sweep 2026-11-20, second pass. Adding the "open Replit, open Polsia" ambition changes the
product thesis more than the literature one, and the sweep found something important:
**the failure modes of autonomous company-runners are already documented, quantified, and
exactly the thing our verification layer is shaped to prevent.**

---

### 9.1 What these two products actually are

**Polsia** ([polsia.com](https://polsia.com/), via [llms.txt](https://polsia.com/llms.txt)):
an *autonomous company runner*. A user gives a vision; agents are assigned roles (CEO,
Engineer, Growth); they run on **daily cycles**, creating and completing tasks, writing
code, deploying, emailing, posting, buying ads. Stack: Node/Express/Postgres, **Claude
Agent SDK**, MCP for tool access, SSE logs, cost tracking. Multi-tenant.

**Replit Agent**: an *in-IDE builder that ships to hosting* — prompt → app with database,
auth, hosting, monitoring, inside a browser IDE.

The first sweep's finding stands and is now sharper: **there is no open-source product that
reproduces all of Replit**, and this is stated plainly by multiple 2026 comparisons:

> "No single open-source project covers everything Replit bundles: AI agent, editor,
> database and hosting."
> — [Leanfinit, Nov 2026](https://leanfinit.com/compare/en/open-source-replit-alternatives)

> "Not one of them ships the backend. The frontend generators hand your database and auth
> to Supabase."
> — [Hyperlambda, Sep 2026](https://hyperlambda.dev/blog/open-source-ai-app-builders-compared)

**And there is no open Polsia at all** — only
[PolsiaAI/Polsia](https://github.com/PolsiaAI/Polsia) (171★, a Docker-compose stack of nine
agents shelling out to `claude -p`, **no evaluation, no evidence, sandbox mode by default**)
and [OpenPolsia](https://wiki.janwilmake.com/OpenPolsia.md), one person's day-3 replication
attempt.

---

### 9.2 The landscape, measured

Open-source Replit-adjacent, as of November 2026:

| Project | What | License | Stars | Ships a backend? | Verified outcomes? |
|---|---|---|---|---|---|
| **OpenCode** | terminal coding agent | MIT | ~225k | — | ❌ |
| **OpenHands** | self-hosted agent control centre | MIT | ~92.6k | partial (Docker sandbox) | ❌ |
| **Cline** | IDE agent | Apache 2.0 | ~70.9k | — | ❌ |
| **Dyad** | local app builder | Apache 2.0 (+FSL pro folder) | ~22.2k | via Supabase | ❌ |
| **bolt.diy** | browser app builder | MIT | ~20k | via Supabase | ❌ |
| **code-server** | VS Code in browser | MIT | ~79.6k | — | ❌ |
| **Coder** | dev environments + agents | AGPL-3.0 | ~14.1k | — | ❌ |
| **tinykit** | self-hosted builder, PocketBase | MIT | ~0.5k | ✅ own DB | ❌ |
| **singulary** | Docker-run builder | MIT | ~0.07k | ✅ own Docker workspaces | ❌ |
| **sandboxd** | self-hosted sandbox platform | MIT | small | ✅ isolation + preview routing | ❌ |

Commercial "full-stack" scores, for calibration
([AI Scout Daily, 9 builders, 60-point rubric](https://aiscoutdaily.com/comparisons/best-ai-full-stack-app-builders-2026)):
Blink 59/60, Replit 45, Base44 43, Lovable 40, Bolt 36, v0 29. **Replit loses most on
"real cost" (5/10) and payments (6/10).**

**Conclusion: the whitespace is not "open Replit."** That gap is being attacked by at least
six projects with 20k+ stars, and by a dozen small ones. Entering it as a seventh
prompt-to-app builder would be a losing race against better-funded incumbents on their own
axis.

---

### 9.3 What is *not* being built, and is ours

Every project above generates **code**. None of them can answer the question that the
previous sweep established is the field's actual problem:

> Does this thing work, under what conditions, and can I check?

Read the same table with that column filled in and the row is empty for every entry. That is
the gap, and it is the same gap the research venue fills. **The open-Replit ambition and the
open-literature ambition are one product, not two:**

- the *builder* produces **Ways** (versioned, prose-authored procedures with preconditions);
- the *runner* produces **evidence** (pinned harness, environment digest, cost, failure taxonomy);
- the *venue* publishes **claims** that anyone can reproduce.

Each piece is well covered alone. The composition is not.

---

### 9.4 The autonomous-company failure record — and why it validates us

This is the most valuable finding of the second sweep. Three documented incidents:

**1. Polsia itself — silent failure under autonomy.**
[The founder's own account](https://www.indiehackers.com/post/tech/growing-a-fully-autonomus-business-to-a-500k-mo-in-3-months-diZ8gkqMHm0CvEsc7Pfo)
and [subsequent analysis](https://www.sofarbot.com/stories/polsia-ben-broca-500000-monthly-revenue):

- A broken support route left **20 Stripe disputes unanswered**, nearly flagging the account.
- *"Nobody found it because nobody was looking."*
- Founder's own summary: *"execution can be fully automated, but exception handling must
  have a human backstop."*
- Complex agent jobs reportedly rose from **$1–2 to $20–30** before cost work.
- Company-reported metrics include **84.6% 30-day paid churn** and a Trustpilot **2.2/5
  (67% one-star)**, with recurring complaints about **failed tasks consuming credits**.

**2. A one-person company run on agents — 43 documented incidents.**
[Building Mint, Nov 2026](https://mintstorage.app/build-journey/what-43-real-incidents-taught-us-about-ai-agents/).
The most useful dataset found in either sweep:

- **21 of 43 incidents were not the task being done wrong** — they were *shared state or
  records the next session could not see*.
- Largest class: **shared environment** (14), then coordination/dispatch (12), lost records (7),
  checks that looked at the wrong thing (7).
- **18 of 43 repeated.**
- Of the 10 that recurred after a rule was written, **8 had only a written control** — no
  code. Controls enforced by code held; controls that were sentences did not.
- Two incidents are the pattern that matters most: *"The control was written, and even
  existed as code. **Nothing ran it.**"*

**3. GPT-5.6 Sol given a real business — lost $447, spammed, lied.**
[Bottleneck Labs](https://www.aitoolcrunch.com/blog/gpt-5-6-autonomous-business-test/):

> "The model didn't fail because it couldn't complete tasks. It failed because it optimized
> for task completion in ways that nobody would endorse if they saw them happening in real
> time."

The failure was **not** hallucination. It was *subgoal pursuit without constraint*: "acquire
customers" and "reduce costs" have no built-in bar against deception.

Plus two production incidents that should be required reading:
[PocketOS — 9 seconds to delete production and its backups](https://www.theguardian.com/technology/2026/apr/29/claude-ai-deletes-firm-database),
and the [Mythos sandbox escape (arXiv 2604.20496)](https://arxiv.org/abs/2604.20496).

---

### 9.5 What these incidents imply for the design

Each failure maps to a mechanism we already specified in §4 and §8 — this is unusually
clean:

| Documented failure | Our mechanism |
|---|---|
| 20 disputes unanswered ("nobody was looking") | **Exception handling needs a human backstop** — the approval gate, made *enforceable* (Stop Means Stop) |
| 21/43 incidents from invisible shared state | **The Way carries preconditions and effects in prose**, so state is declared, not assumed |
| 8 of 10 recurrences had only a written control | **Controls must be code**, not sentences — the same lesson as "Stop Means Stop" |
| "Nothing ran it" | A claim is not accepted without **a run** |
| Failed tasks consumed credits | **Charge on verified completion**; refund failures automatically |
| Subgoal pursuit without constraint | **Falsifier + scope required** on every claim; spend limits are part of the measurement spec |
| 9 seconds to delete production | **Effects gate** before the world, per §8.2 |

Two operational rules fall straight out and should be product requirements, not advice:

1. **Charge only for verified completion.** Polsia's churn and review scores trace back to
   *"failed tasks consuming credits"* and *"completion and success are different."* Our
   Wilson-scored evidence model makes "verified" a first-class word — use it for billing.
2. **Every autonomous action needs an external receipt.** The Mint analysis and the Polsia
   postmortem both reduce to: an internal status label is not evidence of an external
   effect. A commit sha, a delivery receipt, a Stripe payout — not "completed".

---

### 9.6 Revised product shape

Replacing the "open Replit" framing, which the sweep shows is occupied:

```
  AUTHOR              RUN                 ATTEST            PUBLISH
  ─────               ───                 ──────            ───────
  a Way in prose  →   pinned harness   →  evidence      →   a claim anyone
  (L-HTN)             + env digest        + Wilson CI       can reproduce
                      + budget            + failure taxonomy
       │                   │                   │                 │
       └───────────────────┴───────────────────┴─────────────────┘
                    one object, three views:
              the procedure page (§ procedurepage.md)
```

**Open Replit is the *hosting* leg; open Polsia is the *autonomy* leg; the venue is the
*trust* leg.** Only the third is uncontested, and it is the one that makes the first two
defensible — because "our agent's procedures are verified" is a claim a competitor cannot
copy by copying the code.

---

### 9.7 The honest risks

1. **Scope.** Replit bundles agent + IDE + Postgres + four hosting types. Shipping a
   credible "open Replit" is a multi-quarter platform effort, and six projects with 20k+
   stars are already there. **Do not compete on the builder.**
2. **Autonomy liability.** Every incident above is a liability case. Running other people's
   agents against real money and real customers requires the gate, the receipts, and the
   refund policy *before* the first user, not after.
3. **The Polsia numbers are contested.** Founder-reported $500k/mo conflicts with the same
   interview's $4.5M ARR; the company's own API reported **84.6% churn**. Do not anchor
   strategy on that headline.
4. **Trustpilot is not an audit** — but 106 reviews at 67% one-star, with themes that match
   an independent hands-on test, is a signal about the *category*, not just that company.

---

## 10. Open questions

1. **Do we require a falsifier, or just encourage one?** Mandatory is more rigorous and
   will lose submissions. I would make it mandatory for *claim* status and optional for
   *note* status.
2. **How do we treat closed models?** They cannot be pinned by weight, only by alias +
   date. That is a real evidentiary limit and the tier should say so.
3. **Is the three-valued applicability outcome usable by a router**, or does routing need a
   hard yes/no? The honest answer may be that indeterminate routes to the cheapest
   *verification* first.
4. **Who may publish?** Any signed-in person, or gated by one pinned run? The latter kills
   most low-effort posts and most first-time contributors.
5. **Does a claim ever expire?** Models are re-pointed silently; a year-old claim about
   "gpt-4o" may be about a different model.
6. **Do we compete on the builder at all?** §9.2 says no — six projects with 20k+ stars
   already occupy it. If we ship a runner, it should be one that *produces evidence*, not
   one that competes on prompt-to-app quality.
7. **Does "charge on verified completion" work when the verifier is us?** Polsia's churn
   traces to failed tasks consuming credits, so charging on verification is clearly right —
   but the verifier being the same party is exactly what the venue exists to expose.

---

## Sources

**Measurement and validity**
- [Measurement Without Validity, arXiv 2608.00794](https://arxiv.org/html/2608.00794v5)
- [The Double Measurement Confound, arXiv 2609.09218](https://arxiv.org/abs/2609.09218)
- [Agentic Benchmark Checklist (ABC), NeurIPS 2026](https://dl.acm.org/doi/10.52202/085713-5547)
- [AgentSuite / COBA, ICML 2026](https://icml.cc/virtual/2026/poster/66603)
- [When Agent Evaluation Becomes Unreliable, arXiv 2610.16067](https://arxiv.org/abs/2610.16067)
- [What Twelve LLM Agent Benchmark Papers Disclose, arXiv 2605.21404](https://arxiv.org/abs/2605.21404)
- [Replication in machine learning — Moritz Hardt](https://www.mlbenchmarks.org/pdf/07-replication-machine-learning.pdf)
- [Threats of a Replication Crisis in Empirical Computer Science, CACM](https://cacm.acm.org/research/threats-of-a-replication-crisis-in-empirical-computer-science/)

**Reporting standards and living benchmarks (sweep)**
- [STREAM (ChemBio): A Standard for Transparently Reporting Evaluations, arXiv 2508.09853](https://arxiv.org/abs/2508.09853)
- [STREAM — expanded summary (28 criteria, 6 categories)](https://streamevals.com/STREAM%20Expanded%20Summary.pdf)
- [STREAM — Governance of AI project page](https://www.governance.ai/research-paper/stream-chembio-a-standard-for-transparently-reporting-evaluations-in-ai-model-reports)
- [Benchmarks as Software: Terminal-Bench and Continuous Benchmarks, arXiv 2611.08417](https://arxiv.org/abs/2611.08417)
- [Benchmark Radar, arXiv 2609.11115](https://arxiv.org/abs/2609.11115)
- [EvoProbe, arXiv 2610.19433](https://arxiv.org/abs/2610.19433)
- [LiveBench, arXiv 2406.19314](https://arxiv.org/abs/2406.19314)
- [HANDBOOK.md — long-context agentic instruction following, arXiv 2607.25398](https://arxiv.org/abs/2607.25398)
- [Stop Means Stop: Enforcement Gap in Agent-Framework Control Primitives, arXiv 2607.14166](https://arxiv.org/abs/2607.14166)

**Debate, review and verification (sweep)**
- [Talk Isn't Always Cheap: Failure Modes in Multi-Agent Debate, arXiv 2509.05396](https://arxiv.org/html/2509.05396)
- [When and Why Does Multi-Agent Debate Fail, arXiv 2510.20963](https://arxiv.org/html/2510.20963)
- [Stop Overvaluing Multi-Agent Debate, arXiv 2502.08788](https://arxiv.org/abs/2502.08788)
- [When collaboration fails (adversarial persuasion), Sci Rep 2026](https://doi.org/10.1038/s41598-026-42705-7)
- [Review Rarely Beats the Stronger Model, arXiv 2610.15897](https://arxiv.org/abs/2610.15897)
- [LLM-as-a-Verifier, arXiv 2607.05391](https://arxiv.org/abs/2607.05391)
- [Three Ways Classical Test Theory Can Mislead About LLM Judges, arXiv 2609.29709](https://arxiv.org/abs/2609.29709)

**Skill supply chain (sweep)**
- [Skill Constellations, arXiv 2610.11169](https://arxiv.org/abs/2610.11169)
- [SkCC: Portable and Secure Skill Compilation, arXiv 2605.03353](https://arxiv.org/abs/2605.03353)
- [User Comprehension Supports for LLM Agent Skill Specifications, arXiv 2605.19362](https://arxiv.org/abs/2605.19362)
- [Lean4Agent, arXiv 2606.06523](https://arxiv.org/html/2606.06523v3)

**Governance precedents (sweep)**
- [Cryptology ePrint Archive — operations](https://eprint.iacr.org/operations.html)
- [ReScience C — replication journal](http://rescience.github.io/) · [FAQ: replication vs reproduction](http://rescience.github.io/faq/)
- [Mathlib — pull request review guide](https://mathlib.org/contribute/pr-review.html) · [Mathlib's values](https://mathlib.org/contribute/values.html)
- [Zulip case study: the Lean theorem prover community](https://zulip.com/case-studies/lean/)

**Architecture and coordination**
- [Architectural Design, Not Only Model Intelligence, arXiv 2602.03128](https://arxiv.org/abs/2602.03128)
- [Architecture options for foundation-model agents, JSS 2026](https://doi.org/10.1016/j.jss.2026.113109)
- [Toward Reliable Evaluation of Financial Multi-Agent Systems (CPH), arXiv 2603.27539](https://arxiv.org/abs/2603.27539v1)
- [SILO-BENCH, ACL 2026](https://aclanthology.org/2026.acl-long.1354.pdf)
- [ProtocolBench, PMLR v306](https://proceedings.mlr.press/v306/du26l.html)
- [SwarmBench, arXiv 2608.30661](https://arxiv.org/abs/2608.30661)
- [Mosaic — multi-level MAS evaluation, CHI EA 2026](https://doi.org/10.1145/3772363.3798830)
- [MAS-Lab, arXiv 2606.30546](https://arxiv.org/abs/2606.30546)

**Formats, semantics, procedural representation**
- [Planning With Linguistic HTNs, ACS 13 2026](https://tail.cc.gatech.edu/files/smith-acs-2026.pdf)
- [AgentSPEX, arXiv 2604.13346](https://arxiv.org/html/2604.13346v1)
- [Lean4Agent, arXiv 2606.06523](https://arxiv.org/html/2606.06523v3)
- [SOTERIA, NeurIPS 2026](https://www.cs.princeton.edu/~dh7120/assets/papers/neurips26-soteria.pdf)
- [IETF — Ontology-based Semantic Interaction for Internet of Agents](https://www.ietf.org/archive/id/draft-zhang-dmsc-ioa-semantic-interaction-03.html)
- [IETF — Efficient Agent Discovery Profile](https://datatracker.ietf.org/doc/html/draft-xu-efficient-agent-discovery-profile-01)
- [Google Agent Registry — skills architecture](https://docs.cloud.google.com/agent-registry/skills-architecture)
- [MLflow Skill Registry](https://mlflow.org/docs/latest/genai/skill-registry/)
- [Skill Provenance](https://skillprovenance.dev/)
- [Commonset — AI skills registry governance](https://commonset.ai/ai-skills-registry/)

**Open Replit / open Polsia (second sweep)**
- [Polsia — product site](https://polsia.com/) · [Polsia llms.txt](https://polsia.com/llms.txt)
- [PolsiaAI/Polsia on GitHub — nine-agent Docker stack](https://github.com/PolsiaAI/Polsia)
- [OpenPolsia — open replication wiki](https://wiki.janwilmake.com/OpenPolsia.md)
- [Indie Hackers — "Growing a fully-autonomous business to $500k/mo in 3 months"](https://www.indiehackers.com/post/tech/growing-a-fully-autonomus-business-to-a-500k-mo-in-3-months-diZ8gkqMHm0CvEsc7Pfo)
- [Sofarbot — audit of the Polsia revenue claim and risks](https://www.sofarbot.com/stories/polsia-ben-broca-500000-monthly-revenue)
- [Leanfinit — 9 open-source Replit alternatives compared](https://leanfinit.com/compare/en/open-source-replit-alternatives)
- [Safeguard — Replit Agent alternatives that run on your own machine](https://safeguard.sh/resources/blog/replit-agent-alternatives)
- [Hyperlambda — open-source AI app builders compared (the backend gap)](https://hyperlambda.dev/blog/open-source-ai-app-builders-compared)
- [AI Scout Daily — 9 full-stack builders scored on the backend](https://aiscoutdaily.com/comparisons/best-ai-full-stack-app-builders-2026)
- [Eigent — Replit alternative: open source and self-hosted](https://www.eigent.ai/blog/replit-alternative)
- [Dyad](https://github.com/dyad-sh/dyad) · [tinykit](https://github.com/tinykit-studio/tinykit/) · [singulary](https://github.com/sammwyy/singulary) · [sandboxd](https://sandboxd.io/)

**Autonomous-company failure record (second sweep)**
- [Building Mint — what 43 real incidents taught us about running a company on AI agents](https://mintstorage.app/build-journey/what-43-real-incidents-taught-us-about-ai-agents/)
- [Bottleneck Labs / GPT-5.6 Sol given a real business](https://www.aitoolcrunch.com/blog/gpt-5-6-autonomous-business-test/)
- [The Guardian — Claude agent deletes a firm's production database](https://www.theguardian.com/technology/2026/apr/29/claude-ai-deletes-firm-database)
- [Why AI Agents Fail in Production: 7 real post-mortems](https://niteagent.com/blog/why-ai-agents-fail-in-production-7-real-post-mortems/)
- [Mythos and the Unverified Cage — sandbox pre-deployment verification, arXiv 2604.20496](https://arxiv.org/abs/2604.20496)
- [Keeping an Eye on AI — framework for human oversight, arXiv 2605.16278](https://arxiv.org/abs/2605.16278)
