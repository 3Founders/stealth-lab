# StealthLab — YC-First Deck (v2)

Audience: Y Combinator interview (primary). INV.ENT / India audiences (secondary).
Rule: concrete user + outcome in the first 60 seconds. No jargon, no vision-speak before Slide 9.

---

## Slide 1 — Title

**StealthLab**
Verified procedural memory for teams running AI agents.

Chaitanya Deshkar (IIT Bombay) · Anuj Bhadbhade (IISc Bangalore)

---

## Slide 2 — Problem

Teams are deploying agents that repeat the same mistakes forever.

- 65% of enterprise agent failures trace to context/procedure drift — not model capability (Value Add VC, Jul 2026)
- Uber gave 5,000 engineers Claude Code; the annual AI budget burned through in 4 months ("tokenmaxxing," DevRev)
- Unmanaged context cost grows quadratically with task length (ACM, arXiv:2607.21503)
- Agents don't learn from experience. Every failure is re-paid at full price.

---

## Slide 3 — Why Now

Both AI labs standardized the file format for what agents know how to do:

- Anthropic Agent Skills (Sept 2025) and OpenAI Codex Skills (Nov 2025) converged on `SKILL.md`
- Adopted by 40+ clients via agentskills.io: Cursor, Copilot, VS Code, Gemini CLI, Goose, Letta…
- Registries and marketplaces already ship skills; Chainguard ships *hardened* skills (security review only)

The format won. Formats don't verify themselves.
No one produces outcome evidence, regression signals, or lifecycle management for machine-written procedures.

---

## Slide 4 — What We Built

A substrate that turns raw agent experience into verified, retrievable procedures.

- Extracts procedures from agent traces with provenance edges and success criteria
- Retrieves them under explicit budget control at decision time (`substrate_search` → progressive disclosure)
- Integrated as a first-class memory in tau2-bench's banking_knowledge domain (MCP server + retrieval mixins)
- Deterministic scoring: DB-hash state verification, gold-patch grading

---

## Slide 5 — Honest Proof

Measured on 15 paired instances vs baseline pipeline:

- **74.4% token-cost reduction** (219 vs 330 tool calls per instance)
- Accuracy unchanged (2/15 vs 0/15 on paired hard cases) — efficiency without accuracy gain yet
- Failure classes generalize across independent repos: RIGHT_FILE_wrong_fix (12 instances/8 repos), NO_EDIT_at_all (11), LOCALIZATION_miss (9–10)
- Wall-clock breakdown (dist.md): tests/docker = 65% of instance time — our target surface
- Known gap, owned openly: method store is a store, not yet a learning loop (success scoring is stubbed). That loop is the next milestone, not a hidden weakness.

---

## Slide 6 — Trust Is the Product

Every procedure carries:
provenance (which traces produced it) · success criteria (what counts as working) · evidence record (attempts, outcomes, model mix, last failure) · lifecycle status (active / superseded / retired)

Resolution by execution, not opinion: procedures are promoted only after passing real task suites (our two benchmark harnesses generalize this).
Enterprises will not install machine-written procedures without exactly this.

---

## Slide 7 — The Layer Map

| Layer | Status |
|---|---|
| Skill FORMAT | ✅ Standardized (SKILL.md, both labs) |
| Transport | ✅ MCP everywhere |
| Discovery | ✅ Marketplaces live |
| Security review | 🟡 Chainguard (supply-chain only) |
| Identity/payments/runtime | ✅ WorkOS, Stripe MPP/x402, hyperscalers |
| Observability | 🟡 Crowded, converging |
| **Evidence + Lifecycle** | ❌ **Nobody. This is us.** |

Analogy: WorkOS didn't invent auth; it packaged enterprise-readiness when SaaS went up-market. Skills just became portable; enterprises will demand verification before installing them. Every SaaS eventually bought auth from WorkOS. Every agent platform will buy procedure-verification from us.

---

## Slide 8 — Roadmap & Moat

Now → Next (spec v5 direction):
1. Close the write-side learning loop: success-scored procedure updates (RL credit assignment over the library)
2. Layered lookup: exact match → logged statistics → generative fallback (near-zero-cost planning; GATS-style)
3. Scope-aware sharing across teams/environments: claims classified shared / cluster-specific / private by contradicting evidence (FedWorld validated this direction on τ-bench, Jul 2026)

Moat compounds: every execution adds evidence no competitor has. The registry becomes more correct as it is used.

---

## Slide 9 — Horizon (only if asked / final slide)

- Today: prove what agents know actually works.
- The agentic internet needs a verified-knowledge layer: Cloudflare defined readable/discoverable/callable/payable — we are the *trustworthy* primitive underneath "callable."
- Endgame: a contestation layer where any claim can be attacked and resolved by execution — journals become UI reading from the ledger.
- One line: "Today we prove what agents know works. Eventually, every claim anywhere gets contested here before anyone trusts it."

---

## Slide 10 — Team

- **Chaitanya Deshkar** — Final Year AI/ML, Centre for Multidisciplinary Education, IIT Bombay. Built the extraction pipeline, benchmark harnesses, tau2 integration.
- **Anuj Bhadbhade** — IISc Bangalore. Systems + evaluation.
- Two public proof artifacts in flight: τ³-banking leaderboard attempt (beat top model pass^1 using small open-weight models + our retrieval) and OSS launch of the registry prototype.

---

## Slide 11 — Market & Milestones

Named-source market numbers (memory infrastructure):
- $1.2B (2025) → $18.9B (2034) @ 62% CAGR (Market Intelo); APAC fastest region @ 68.5% CAGR
- Orchestration + memory: $6.27B → $28.45B by 2030 @ 35.3% (Mordor Intelligence)

Milestones = risk reduction:
1. ✅ Working substrate + paired-instance evidence
2. 🔄 Public leaderboard run (τ³) — visibility artifact
3. 🔄 OSS launch + design partners (5 dev-tool teams)
4. ⬜ Learning-loop close → accuracy gains, not just cost gains

Ask: YC batch to turn the wedge into the standard evidence layer for the skill economy.
