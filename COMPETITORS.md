# Competitive Landscape — verified via Exa semantic sweep 2026-08-26

## Headline correction

Earlier claim ("verification has no incumbent / nobody builds this") is **now
false in absolute form**. A semantic search surfaced a six-project cluster —
all founded/published Feb–Aug 2026 — attacking slices of exactly this problem.
None of them appeared in keyword websearches; Exa's semantic retrieval found
them. Demand validation is stronger than we thought; the window is shorter.

## The cluster

### Howdex — closest overlap, highest velocity threat
- Solo project, repo created Jun 19, 2026 (2 months old), 1 star — but 738
  tests green and shipping fast: Merkle audit ledger mapped to SOC 2 CC7/CC8,
  EU AI Act Art. 12, CSA ATF; federated multi-tenant procedure library with
  review/promotion workflow, per-tenant scoping, provenance hashes
- Thesis near-identical: traces → receipt-backed verified procedures → public
  registry ("the Codex"); "no proof, no procedure" (candidate until a
  task-relevant verifier passes); staleness metadata; failed-attempt memory;
  deterministic numpy-only core, local-first, MCP server
- Self-reported benchmark: fresh agent 35% → 90% success w/ Howdex, attempts
  13 → 8.75 (own task suite; explicitly labeled internal evidence)
- **Gaps we exploit:** binary receipts ≠ statistical verification (no Welch+BH
  backtest gates); no bi-temporal claim graph or supersession semantics; no
  debate/adjudication for conflicting knowledge; no cross-domain or transfer
  evidence; self-run benchmark only — no third-party gold labels; no published
  learning curves

### TrustMemory — facts/claims layer, strongest distribution
- Peer-reviewed claim pools across 50+ domains; multi-agent validation ("like
  Wikipedia editors but automated"); trust scores per agent; Ed25519 portable
  attestations; Merkle audit chains; governance/appeals; auto-seeded from WHO/
  CDC/FDA/NIST etc.; native MCP + A2A + SKILL.md across 30+ platforms
- **Gap:** validation by agent opinion-consensus, not measured outcomes;
  facts not procedures; no lifecycle/staleness retirement of wrong claims

### CleanSkills — certification pipeline (Procedure-CI adjacent)
- $1 flat SKILL.md certification: behavioral contract extraction, 48-point
  automated test suite, CLR-ID certificates, on-chain mirror (Base L2),
  x402 payment gate, Sovereign air-gapped tier
- **Gap:** one-shot conformance cert at submission time; no ongoing outcome
  track record, no reuse telemetry, no staleness re-testing, no learning loop

### Lorg — agent-built knowledge archive + reputation
- Founded 2026; prompts/workflows/insights contributed by agents, agent-peer
  review, quality gates, cryptographic attribution, trust-score tiers
- **Gap:** prompts/workflows not executable procedures; peer review =
  consensus, not outcome measurement

### Limitless Library — verified reuse protocol
- Receiver-owned checks, exact-byte/digest binding, observed-invocation
  adoption receipts, policy-filtered catalogs, fail-closed abstention;
  Apache-2.0 local alpha; software-work wedge first
- **Gap:** proves authorization/adoption, not effectiveness statistics

### SkillFab — arXiv:2607.03780 (academic prior art)
- Agent-native skill production platform: demand-first issues, Git-backed
  evidence, maintainer review/certification, registry publication, MCP surfaces
- **Status:** research report; trust policy between community/maintainer/
  automated certification listed as open question

## Refined defensible position (replaces "nobody")

> Six teams attack slices — attestation (Howdex), consensus facts
> (TrustMemory), conformance certification (CleanSkills), reputation (Lorg),
> adoption-proofing (Limitless), production workflow (SkillFab). Nobody does
> *empirical outcome verification*: gold-labeled statistical backtests before
> promotion, bi-temporal supersession when the world changes, adjudication
> when knowledge conflicts, and published cross-domain learning curves. That
> combination — measurement-grade truth, not attestations or opinions — is ours.

## Strategic implications

1. **Speed:** Howdex went zero → federation + audit ledger in 8 weeks solo.
   Ship the verify-and-retire loop within weeks, not quarters.
2. **Compliance wedge (adopt):** Howdex's EU AI Act Art. 12 / SOC 2 mapping of
   an append-only operation ledger is the right enterprise wedge — our
   provenance graph maps onto it directly. Fold into roadmap.
3. **Possible complement, not war:** CleanSkills/Howdex attestations could sit
   alongside our outcome layer ("certified AND effective"). Interop posture
   beats turf war pre-product.
4. **Honesty brand sharpened:** name all six by name in investor/partner
   conversations; publish this comparison. Being the team that mapped the
   space accurately is itself differentiation.
5. **YC Q7 and Slide 7 rewritten accordingly** (done — see those files).

## Sources
Exa semantic sweep Aug 26, 2026: github.com/rossbuckley1990-hash/Howdex
(+ PRs #41 #43), trustmemory.ai (+/docs, /security), cleanskills.btnomb.com,
limitlesslibrary.com, linkedin.com/company/lorgai, arXiv:2607.03780.

---

## Traction pass (Aug 26, 2026 — deep dive on the three movers)

### TrustMemory — most commercially mature of the cluster
- **Pricing already live** despite "100% free early access" homepage framing:
  Free / Pro $49/mo / Business $199/mo / Enterprise $499/mo (agent counts,
  pools, claim & query quotas); early adopters promised grandfathering
- Distribution: official Python SDK on PyPI (`trustmemory` v0.2.0); MCP server
  shipping **11 tools**; Google A2A protocol support; one-message install via
  WhatsApp/Telegram/Discord/Claude Code/Cursor
- Scale signals (self-reported): 2,000+ claims, ~20–30 knowledge pools,
  "thousands" of peer-reviewed claims; pools auto-seeded from WHO, CDC, FDA,
  OWASP, MDN, NIST pipelines; claims of 7-layer Sybil defense
- No funding announcement found anywhere — appears bootstrapped/indie, moving
  fast on distribution rather than capital
- Read: executing the *consumer facts-trust* play with real GTM. Still zero
  procedural/outcome verification — validation is agent opinion-consensus over
  seeded factual claims.

### CleanSkills — an autonomous-AI-operated service (notable on two axes)
- Terms of Service disclose: *"BTNOMB is operated by an autonomous AI system.
  There are no human employees."* Certifications processed end-to-end by AI
  agents and pipelines, with an explicit instruction to independently verify.
- Public registry carries recognizable names (@anthropic/computer-use,
  vercel/v0-agent-tools, openai/swarm-skills) — certification-by-association
  marketing; sample cert dated Jul 7, 2026; flat $1/x402 model working.
- **Their own ToS concedes our wedge:** a CLR-ID *"attests to behavioral test
  results at a point in time — it does not guarantee ongoing correctness,
  safety, or fitness."* Point-in-time conformance, explicitly no lifecycle.
  That admission is quotable in our positioning.

### Howdex — solo velocity benchmark unchanged
Repo created Jun 19, 2026; 1 star; single maintainer; 738 tests green;
Merkle ledger + federation shipped inside 8 weeks. No external users claimed
(their README says dogfood metrics are "internal evidence only"). Watch their
release cadence — it sets the pace we must beat.

### Updated strategic read
1. TrustMemory proves buyers will pay subscription pricing for agent trust
   infrastructure *today* — de-risks the revenue slide.
2. CleanSkills' autonomous operation + point-in-time disclaimer is market-
   education fuel: enterprises evaluating these tools will quickly learn the
   difference between conformance certs and outcome evidence. Be the team that
   explains it publicly.
3. Nobody in the cluster has announced institutional money yet (vs Mem0's
   $24M in adjacent storage) — first mover with the loop shipped + published
   curves likely takes the category-defining round.
4. Monitor cadence set: weekly Exa pass on all six names; alert on any funding
   announcement, registry-scale jump, or enterprise logo appearing.
