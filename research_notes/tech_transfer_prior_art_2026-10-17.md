# Deep research — prior art and empirical results on the tech-transfer plan

Research date **2026-10-17**. Sources: arXiv API, Exa, ACM DL, and first-party ecosystem sources.
Everything below is quoted or paraphrased from an abstract or page I retrieved, except where marked
**[inference]**. Assessed against `docs/plan_2026-10_tech_transfer_targeted_repair.md`.

**Headline: the plan's central mechanism is established prior art and should not be pitched as novel.
One of its concrete proposals is partly contradicted by the strongest available empirical result. The
weakest-evidenced part of the plan — its verifier — turns out to be a surveyed field with published
rates. And the parts the plan treats as novel security/review work were shipped by an ecosystem while
the plan was being written.**

---

## 1. §2's core thesis is the established APR paradigm — validated, but not novel

### Agentless (arXiv 2407.01489)

> "Agentless follows a simple three phase process: localization, repair, and patch validation. In the
> localization process, Agentless employs a hierarchical process to first localize the fault to
> specific files, then to relevant classes or functions, and finally to fine-grained edit locations.
> Agentless's localization process makes use of both LLM-based localization as well classic
> information-retrieval-based localization… At the same time, Agentless generates **reproduction
> tests** that can reproduce the original error and help with candidate patch selection."

This is the plan's §2 thesis — target the edit, verify with a generated test — published, and with
the added design choice that matters: *"unlike prior complex agent-based tools, Agentless does not
allow LLMs to autonomously decide future actions."* StealthLab's dispatch ladder is the *agentic*
counterpart to the same insight.

### Towards reusing hints from past fixes (Wang et al., ICSE 2008, doi 10.1145/3180155.3182550)

> "we systematically designed six overlap metrics, and performed an empirical study on **5,735 bug
> fixes** to investigate the usefulness of past fixes when composing new fixes. For each bug fix, we
> created **delta dependency graphs**… and identified how bug fixes overlapped with each other in
> terms of the content, code structure, and identifier names of fixes."

Direct ancestor of §2.4 ("procedures keyed by target signature") and of E3/E4. Reuse-of-past-fixes has
been measured at scale for nearly two decades.

---

## 2. The strongest empirical result on targeting — and it constrains `targets[]`

### On the Role of Fault Localization Context for LLM-Based Program Repair (arXiv 2604.05481, 2026-04-07)

500 SWE-bench Verified instances, GPT-5-mini, **61 configurations** varying file-, element-, and
line-level context. Verbatim findings:

> "Our results show that **more context does not consistently improve repair performance**.
> **File-level localization is the dominant factor, yielding a 15–17× improvement over a no-file
> baseline.** Expanding file context is often associated with improved performance, with successful
> repairs most commonly observed in configurations with **approximately 6–10 relevant files**.
> **Element-level context expansion provides conditional gains that depend strongly on the file
> context quality, while line-level context expansion frequently degrades performance due to noise
> amplification.** LLM-based retrieval generally outperforms structural heuristics while using fewer
> files and tokens."

### What this means for plan §2 item 1

The plan proposes extending the brief from `files_hinted` to `targets[]` = `{file, symbol?, test?}`,
and item 2 proposes "one part, one target, one check." The evidence ranks that granularity:

| Granularity | Evidence | Recommendation |
|---|---|---|
| **file** | Dominant; 15–17× over no-file; optimal band ~6–10 files | **This is where the win is.** It is already `files_hinted` |
| **element/symbol** | Conditional on file-context quality | Keep as a *secondary* refinement; never at the cost of file quality |
| **line** | **Frequently degrades performance** (noise amplification) | **Do not add line-level targets.** The plan doesn't propose them — keep it that way deliberately |

Two concrete consequences:

1. **`files_hinted` is already the load-bearing field.** The plan treats extending it as step 1; the
   data says the leverage is in *getting the file set right* (6–10 files, LLM-retrieved) rather than in
   adding a finer-grained layer. Worth measuring file-set quality before building `targets[]`.
2. **There is a measurable ceiling at ~10 files**, which pairs with the plan's E1 (spill measurement):
   if a task genuinely needs >10 files, that is a signal it should route straight to the session model
   rather than dispatch to a cheap one.

---

## 3. The plan's weakest-evidenced part — the verifier — is a surveyed field with published rates

### Patch Correctness Assessment: A Survey (ACM TOSEM, doi 10.1145/3702972, Fei, Ge, Li, Wang, Li, Zhang et al.)

> "Most automated program repair methods rely on test cases to determine the correctness of the
> generated patches. However, **due to the incompleteness of available test suites, some patches that
> pass all the test cases may still be incorrect. This issue is known as the patch overfitting
> problem.** Overfitting problem is a longstanding problem in automated program repair… We first
> offer a few examples of overfitting patches… then propose a comprehensive categorization of publicly
> available techniques and datasets, examine the commonly used evaluation metrics, and perform an
> in-depth analysis of the effectiveness of the existing models."

**Plan §7 says the verifier's false-accept rate (alpha) is "unmeasured on our checks."** It is a
named, taxonomised, benchmarked quantity. The plan should adopt this vocabulary and report alpha in it.

### Are "Solved Issues" in SWE-bench Really Solved Correctly? (ICSE 2026, Wang, Pradel, Liu)

> "the most popular benchmarks… are SWE-bench and its human-filtered subset SWE-bench Verified… These
> benchmarks leverage testing to validate generated patches. However, because testing is rarely
> exhaustive, **a patch may pass the tests but nevertheless fail to match the developers'
> expectations.** Unfortunately, it is currently unclear to what extent evaluations performed with
> SWE-bench suffer from such plausible but incorrect patches. This paper presents an in-depth empirical
> study of the correctness of plausible patches generated by three state-of-the-art issue-solving
> tools (**CodeStory, LearnByInteract, and OpenHands**) evaluated on SWE-bench Verified."

**This is the direct comparison for the 5 wrong-passing answers (DS-1000 ×3, BIRD ×2).** Without it,
no claim about verifier quality is defensible — the question is whether ~3.3% (5/150) is better or
worse than the state of the art, and this paper answers that. **Read this before anything else in this
document is used externally.**

### The test-first verifier also has a benchmark and a SOTA

- **TDD-Bench-Verified** (arXiv 2412.02883) — 449 issues from SWE-bench, re-filtered using OpenAI's
  annotation campaign plus additional automated filters; evaluates **test generation** rather than
  issue resolution. This is the benchmark for StealthLab's Sonnet-written acceptance tests.
- **Otter** (arXiv 2502.05368, IBM Research) — generating tests from issues *before the patch exists*,
  with rule-based output checking and a self-reflective action planner; reports **enhancing systems
  that generate patches from issues**. The plan's test-first approach is a named prior art with a
  named leaderboard.

---

## 4. Negative transfer — the plan's 6-fixed/5-broken history has a literature

- **A Survey on Negative Transfer** (arXiv 2009.00909) — "Negative transfer (NT), i.e., **leveraging
  source domain data/knowledge undesirably reduces the learning performance in the target domain**,
  has been a long-standing and challenging problem… reviews about **fifty** representative approaches
  according to four categories: **secure transfer, domain similarity estimation, distant transfer, and
  NT mitigation**."
- **Characterizing and Avoiding Negative Transfer** (arXiv 1811.09751).

The plan's probation/shadow-score gate (§3 "negative-transfer guard", §4 item 4) maps onto this
taxonomy directly. **[inference]** "Domain similarity estimation" is the closest existing category to
the plan's "matched by preconditions" step, and it is worth reading that subliterature before
inventing the mechanism — it may already answer the "how do we know it will transfer?" question.

---

## 5. The review/verification-layer idea exists in both a paper and a shipped ecosystem

### A four-level verification lattice on agent-skill manifests (arXiv 2605.23951, 2026-05-09)

> "The companion paper introduced a four-level verification lattice on agent-skill manifests —
> **unverified, declared, tested, formal** — and explicitly left the top level aspirational: 'a formal
> analysis tool has produced a machine-checkable proof that the skill's behavior is a subset of its
> declared capability set under the runtime's threat model'. This paper closes that gap."

StealthLab's migration 102 verification layers 0–5 (where `verified_*` needs layer ≥3) are the same
idea, and appear to **predate or match** this. That is a genuine strength. The novel rung is **formal**
— machine-checkable containment — and it is now being written up by others.

### OpenClaw × NVIDIA — shipped, at ecosystem scale (openclaw.ai, 2026-06-01)

> "Every ClawHub skill now ships with a **Skill Card** showing what it does and where it came from,
> and **SkillSpector** scans it for hidden instructions and agentic risks."
> "When we launched ClawHub alongside OpenClaw, we were immediately targeted by actors who tried to
> publish skills bundling known malware. We partnered with **VirusTotal** to flag those skills and ban
> the publishers automatically."
> "Our first attempt at building trust… was a Codex agent prompted to look for OWASP agentic risks.
> It worked, and it caught real bad actors. But it was [insufficient]…"

This is a **shipped implementation of nearly everything in plan §3/§7's outward-facing requirements**:
signed provenance (Skill Card), automated security scanning (SkillSpector), reputation-based banning
(VirusTotal), and a **public dataset**. The ecosystem moved in June 2026 while this plan was written in
October. **[inference]** StealthLab's differentiation cannot be "we scan skills" — it must be
"we can *replay* a skill's evidence."

---

## 6. The security risk is quantified, and it maps onto the survey workstream

### Malicious Agent Skills in the Wild (arXiv 2602.06547, NTU)

> "We construct the first labeled dataset of malicious agent skills by **behaviorally verifying
> 98,380 skills from two community registries, confirming 157 malicious skills with 632
> vulnerabilities**. These attacks are not incidental. Malicious skills average **4.03 vulnerabilities
> across a median of 3 kill chain phases**, and the ecosystem has split into two archetypes:
> **Data Thieves** that exfiltrate credentials through supply chain techniques, and **Agent Hijackers**
> that subvert agent decision-making through instruction manipulation. A single actor accounts for
> **54.1% of confirmed cases** through templated brand impersonation. **Shadow features, capabilities
> absent from public documentation, appear in 0% of basic attacks but 100% of advanced ones**;
> several skills go further by exploiting the AI platform's own hook system and permission flags.
> Responsible disclosure led to **93.6% removal within 30 days**."

Three consequences for the plan:

1. **§7.1's "security is the first risk" is correct and now quantified.** The plan's instinct to put
   it first is vindicated by a 98,380-skill empirical study.
2. **"Shadow features in 100% of advanced attacks" is the strongest possible argument for
   StealthLab's survey/evidence layer.** Documenting *actual* versus *declared* capability is not a
   nice-to-have — it is the discriminator between basic and advanced attacks.
3. **"Exploiting the platform's own hook system" is a direct threat to StealthLab's hooks-only
   install.** Three Claude Code hooks plus a worktree is the attack surface. This is not in the plan.

---

## 7. Reproducibility prior art for §7's publication format

- **Registered Reports** (Center for Open Science) — "peer review occurs **prior to observing the
  outcomes**… manuscripts that survive pre-study peer review receive an in-principle acceptance that
  **will not be revoked based on the outcomes**, but only on failings of quality assurance, following
  through with the registered protocol, or unresolvable problems in reporting."
  → Direct precedent for the plan's §7.1 "promotion by a pre-stated sequential rule" and the review
  ledger. Use the actual Registered Report protocol rather than inventing one.
- **LLM agent evaluation and benchmarking: a systematic survey, meta-taxonomy, and critical research
  roadmap** (AI Review, 2026-09-15, Nageshwaran, Ezekiel, Soundararajan, Tran, Lakshmi Narasimhan;
  PRISMA-ScR, 259 primary studies) — "we argue that **the resulting scores are not
  self-interpreting**: what a benchmark number means depends on which capability it measures, how that
  capability is scored, and where the agent is tested."
  → Prior art for the plan's §7 evidence classes (replayable / controlled field / observational) and
  for "claims never borrow strength from a stronger class."

---

## 8. The compositional/interface-contract shape

**agent-chronos-arch** (GitHub, unofficial) — tree decomposition with "**explicit child contracts for
reliable interface propagation**" and "**composition-as-validation**," plus "subtree-level
governance." Small and unrefereed, but it is the same shape as the plan's §7.1 part-of edge with an
input/output contract. **[inference]** Worth citing as prior art rather than treating the interface
contract as an original design.

---

## 9. What this changes, ranked

| # | Change | Why |
|---|---|---|
| 1 | **Read the ICSE 2026 "plausible but incorrect patches" paper before any external claim.** | It sets the bar our 5/150 must clear. Nothing else in this document matters more |
| 2 | **Stop pitching "targeted repair" as novel; pitch replayable evidence.** | Agentless + ICSE 2008 established localize-then-verify two decades ago. The novelty has to be elsewhere |
| 3 | **Weight targeting work toward the file set, not toward `targets[]`.** | File-level is 15–17×; ~6–10 files is the optimal band; line-level degrades. `files_hinted` is already the load-bearing field |
| 4 | **Adopt the patch-overfitting vocabulary and taxonomy** | Alpha is a surveyed, benchmarked quantity. Report it in their terms |
| 5 | **Run TDD-Bench-Verified** | The test-first verifier has a benchmark (449 issues) and a SOTA (Otter) |
| 6 | **Add "hooks are the attack surface" to the security section** | The 98,380-skill study names hook/permission-flag exploitation explicitly |
| 7 | **Differentiate from ClawHub on replay, not on scanning** | OpenClaw+NVIDIA shipped Skill Cards, SkillSpector and VirusTotal in June 2026 |
| 8 | **Use the Registered Report protocol for §7's review ledger** | It is the field-standard pre-registration format; the plan's version is close but not cited |
| 9 | **Read the negative-transfer mitigation literature before designing probation** | ~50 approaches in 4 categories; "domain similarity estimation" may already answer the transfer question |
| 10 | **E4 remains the plausible novel contribution** | I found no prior art for cross-organisation procedure transfer with re-verification and negative-transfer accounting. That is the thing to test |

## 10. Gaps in this sweep

- I did **not** verify the ICSE 2024 "Anti-pattern" paper on mining reusable LLM repair patterns. The
  search returned the ICSE 2008 overlap study instead. **Anti-pattern is likely the closest prior art
  to plan §2.4 and should be located and read directly.**
- No direct prior art found for: cross-organisation procedure transfer with re-verification;
  "importance as in-degree on part-of edges with interface contracts"; credit/stake economics for
  shared verification. E4's novelty claim rests on the absence of search results, which is weak
  evidence — a targeted arXiv listing and a Google Scholar citation search are needed.
- `benchmark_transfer.py` and the existing goal-abstraction DAG were not checked against the library-
  migration literature (e.g. LLM library migration on PyMigBench, arXiv 2504.13272) which is adjacent.