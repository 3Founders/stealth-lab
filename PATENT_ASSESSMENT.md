# Provisional Patent Assessment — Verification Gate & Lifecycle System

Status: internal pre-attorney brief. Prepared Aug 26, 2026 after prior-art
sweep (Exa semantic + websearch + Google Patents surfacing). NOT legal advice;
next step is a consult with a registered Indian patent agent + US counsel.

---

## 1. Candidate invention (what we'd claim around)

A computer-implemented system and method for **governing reusable procedural
knowledge in agent systems**, comprising:

(a) representing agent-derived operational knowledge as discrete items carrying
source provenance, bi-temporal validity metadata (assertion time vs valid time),
and lifecycle states including at least in-force / superseded / retired;

(b) compiling verified knowledge items into executable procedures bound to
typed tool interfaces with machine-checkable preconditions;

(c) **a promotion gate that admits a candidate procedure into a shared
retrieval corpus only when outcome statistics from replay against gold-labeled
task suites satisfy preregistered hypotheses, corrected for multiple
comparisons** (e.g., Welch's t-test with Benjamini–Hochberg correction);

(d) updating said outcome statistics from observed post-promotion reuse, and
automatically transitioning a procedure to superseded or retired state when
freshness/supersession conditions are met, wherein retrieval filters results by
said states; and

(e) an adjudication process resolving conflicts between candidate and incumbent
knowledge items using evidence recency and corroboration before either is
served.

The arguably novel core is the **combination**: statistical-hypothesis
promotion gates tied to gold-labeled suites (not self-reported success),
feeding a bi-temporal lifecycle that retrieval honors, with conflict
adjudication upstream of the gate.

## 2. Prior-art map (all examined Aug 26, 2026)

| Reference | What it covers | Our asserted delta |
|---|---|---|
| **US App 19/647,395** ("LLM & Skill Gating"; qu3ry.net disclosures, Nick Clark) | Capability gates opened by accumulated performance evidence; cryptographic certification tokens w/ evidence hash, expiry, auto-revocation on regression threshold; device binding | Gates *requester capabilities* w/ binary evidence thresholds + tokens; does not teach *knowledge-item* promotion via null-hypothesis statistical testing on gold-labeled backtests, nor bi-temporal supersession semantics, nor conflict adjudication |
| **arXiv:2607.16621 MSCE** ("Memory-to-Skills, Evidence-Grounded Co-Evolution") | Crystallizes evidence-backed policies into callable skills retaining verification rules + reliability estimates; value backfilling | No formal inferential statistics / multiple-comparison correction; no bi-temporal truth-state retrieval filtering described as claimed |
| **arXiv:2607.00345 EDDOps on AgentCore** | Evaluation-driven registration/promotion/**retirement** governing an *agent model* registry; cost-to-performance framework; trace-native observability | Governs foundation-model selection, not knowledge/procedure items; economic decision framework, no null-hypothesis backtests against gold-labeled task suites, no bi-temporal supersession semantics — cite as closest lifecycle-governance prior art and distinguish on claim scope |
| **MemHouse spec** (github memhousehq, fair-code) | Tri-temporal memory (belief/valid/salience); governed promotion Gates A/B; supersession chains; skill-readiness pre-flight checks | Promotion driven by confidence + *human-in-the-loop*, not outcome-statistical backtests; no gold-labeled replay requirement |
| **Howdex** (OSS, Jun '26) | Receipt-gated procedures ("no proof, no procedure"), Merkle ledger, staleness metadata | Receipt = single task-relevant verifier pass; no hypothesis testing, no correction for multiple comparisons, no supersession-driven retrieval filtering |
| **CleanSkills** | One-shot behavioral-conformity certification of SKILL.md packages | Point-in-time conformance only; no outcomes, lifecycle, or retrieval integration |
| **TrustMemory / Lorg** | Consensus peer-review of factual claims; reputation scores | Opinion aggregation ≠ measured effectiveness; facts not procedures |
| **SkillFab (arXiv:2607.03780)** | Registry + review workflow for skill production | Workflow/process focus; certification trust policy explicitly left open |
| **US 12,671,588** (agentic receipt lineage) | Identity-bound delegation, connector mediation, append-only transparency logs | Attestation plumbing; orthogonal to knowledge-quality gating — cite as complementary |
| **US20260017525A1 (Citibank)** | Validating proposed agent actions via generative AI | Per-action validation at runtime; not corpus-level knowledge promotion/lifecycle |

Academic backdrop to cite as background, not blocking: ReMe (utility-based
pruning), MACLA (Bayesian reliability per procedure), Skill-Pro (PPO gate),
WMT (retention scoring) — none combine formal hypothesis testing +
bi-temporal retrieval honoring + adjudication in one governing loop.

## 3. Honest novelty risk assessment

- **Risk: HIGH on obviousness/combination attacks** — the field is crowded
  (six product teams + ≥6 papers within 8 months). An examiner (or ITC
  challenger) may frame our gate as "apply known statistical tests to known
  receipt-gating." Counter: the *specific coupling* — preregistered
  multi-comparison-corrected backtests as the sole promotion criterion,
  feeding supersession-aware retrieval — is what none show.
- **Risk: MEDIUM on §101/§3(k) subject matter** — manageable; see framing below.
- Recommendation: proceed to **provisional filing** for priority date +
  "patent pending" posture, but budget expectations modestly; the durable moat
  remains data (outcome corpora) + speed, with patents as defense-in-depth.

## 4. India framing strategy under CRI Guidelines 2025 (effective Jul 29, 2025)

Key rules confirmed from practitioner sources (Intepat Mar 2026, SpicyIP,
GLE May 2026):

- **Technical effect is the door.** Frame claims as improving *functioning of
  the technical system itself*: measurable reduction in erroneous tool
  invocations, reduced retrieval-of-stale-knowledge rate, bounded propagation
  of invalid records (poisoning containment %), retrieval latency/token-cost
  reductions. These map to Delhi HC's second test (overcoming a limitation in
  the field — cf. Microsoft/Comviva security-improvement precedents).
- **Novel hardware NOT required** (Raytheon embedded in 2025 Guidelines).
- **Enablement bar is high for AI/ML:** specification must include full
  pipeline detail — schema definitions, gate formulas, correction procedure,
  dataset characteristics (harness versions, gold-label construction), and
  validation results demonstrating the claimed technical effect. Our repo +
  PREPRINT_SKELETON.md content becomes the enablement backbone.
- **Business-method bar is absolute in India:** avoid any claim language
  drifting toward "method of managing a marketplace of procedures."
- **AI-assisted drafting fine; AI-conceived not** — humans conceived; document
  inventorship accordingly (both founders).
- **Form 27 (working statement) obligations post-grant** — noted for ops.

## 5. Filing strategy options

| Option | Sequence | Notes |
|---|---|---|
| A. India-first provisional | India provisional (~₹1.6k–8k official fees w/ startup rebate post-DPIIT) → 12-month window → India complete + PCT decision | Cheapest; DPIIT recognition unlocks 80% fee rebate; aligns with incorporation sequence |
| B. US provisional first | US provisional ($75–150 micro/small entity) → 12-month PCT | Stronger "patent pending" signal for US investors; common for SaaS/software |
| C. Dual provisionals same week | India + US provisional on identical disclosure | ~$300–500 total official fees; maximal optionality; recommended if timing allows |

Either way: file **before** public disclosure milestones beyond current repo
visibility (the arXiv preprint is itself prior art — file the provisional
before or simultaneously with preprint submission; grace-period nuances differ
by jurisdiction).

## 6. Attorney brief — questions to bring

1. Is claim direction (c) — statistical-gate promotion — more defensible as
   method, system, or CRM (computer-readable medium) set in light of 2025 CRI?
2. Should Howdex/MemHouse be characterized as §3(k)-adjacent prior art or as
   combination-reference risks for inventive step?
3. India-first + Paris route vs PCT given 2026 PCT reforms (per GLE analysis)?
4. Trade-secret split: keep exact gold-suite composition + thresholds as trade
   secret, claim the surrounding mechanism?

## 7. Immediate actions (non-lawyer)

- [ ] Snapshot repo state (commit hash + archive) as conception evidence dated
      before any filing — both founders sign inventor logbook pages
- [ ] Draft claims sketch v2 with patent agent after their read of this doc
- [ ] File DPIIT first (80% fee rebate applies to the provisional itself)
- [ ] Calendar: provisional target ≤ 2 weeks after Milestone-1 demo exists
      (stronger working-example embodiment)

## Sources (all read Aug 26, 2026)

qu3ry.net LLM-Skill-Gating disclosure series (US 19/647,395) ·
patents.google.com/patent/US20260017525A1 · github memhousehq/memhouse specs ·
arXiv:2607.16621 (MSCE) · exa.ai library entry for US 12,671,588 ·
intepat.com CRI-2025 guide · intepat.com AI-patentability guide ·
spicyip.com 3(k) consistency analysis · globallawexperts.com 2025–26 rules
primer · plus session-held references: Howdex repo/PRs, CleanSkills,
trustmemory.ai, limitlesslibrary.com, arXiv:2607.03780 SkillFab.
