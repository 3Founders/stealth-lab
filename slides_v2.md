# slides_v2 — YC-first deck (content base + metrics sheet + leave-behind)

Format note: YC interviews ban decks. This document doubles as (a) our one-page
metrics sheet and leave-behind, and (b) the content base for verbal answers.
Voice rule: measured, full sentences, every number traceable to a file or a
fetched citation. No superlatives.

---

## Slide 1 — Title

**We verify what AI agents learn before anyone reuses it.**
Peer review + a public journal for agent procedures — globally indexed.
Chaitanya Deshkar (IIT Bombay) · Anuj Bhadbhade (IISc)

---

## Slide 2 — The problem

AI agents save what they learn and share it with each other — but nothing
anywhere checks if it actually works. So agents re-solve solved tasks from
scratch, inherit procedures nobody ever validated, and repeat each other's
mistakes at copy speed.

- 65% of enterprise agent failures trace to context drift, not model capability
- Unmanaged agent context grows quadratically; Uber's 5,000-engineer rollout
  burned its entire year's AI budget in four months, much of it re-deriving
  what it already knew

---

## Slide 3 — Why now

Now is the right time for three reasons that all landed within the last year.

1. **Adoption arrived.** Enterprise agents went from under 5% to a projected
   40% of applications in twelve months (Gartner), while Gartner projects over
   40% of agentic projects will be cancelled by 2027 — citing cost, unclear
   value, and unreliable behavior. Those are exactly what verified memory fixes.
2. **The ecosystem standardized.** Both major AI labs adopted one open skill
   format late 2025 (`SKILL.md`, ~40 clients via agentskills.io). Procedures
   became portable — and sharing unverified knowledge became a global problem.
3. **Science converged.** Seven serious procedural-memory papers since Dec 2025
   agree on the mechanisms: test before reuse, record outcomes, retire stale
   procedures. No shipping product implements any of them.

Historical note (if asked "didn't this fail before?"): the Semantic Web,
Cyc, and expert systems all died because humans had to write, populate, and
maintain the knowledge forever, and no reader showed up. All three conditions
reversed in the last 24 months: agents write it themselves, agents are the
reader, and upkeep is now statistical automation.

---

## Slide 4 — What we built

Not a slide-deck idea — a working system.

- ~22,500 lines of working backend code, 500+ automated checks
- Claim graph with sources, validity dates, and truth states (IN / OUT /
  superseded)
- Procedure compiler: verified claims → step-by-step procedures with explicit
  preconditions, typed tool bindings, numeric safety checks
- Multi-agent debate/adjudication layer that reviews untrusted machine-generated
  knowledge before promotion
- MCP server consumable by any major agent client today
- Two independently built evaluation harnesses (see Slide 6)

---

## Slide 5 — Proof (honest)

Same coding tasks, agent twice: without memory, then with ours.

| Metric | Result |
|---|---|
| Tokens on repeat-pattern work | 74.4% fewer (2.58M → 0.66M) |
| Tool actions | ~34% fewer |
| Paired instances measured | 15 |

Honest catch: accuracy did not yet improve — we say so rather than hide it.
What we did find: a repeated failure class across 12 instances and 8 repos
(RIGHT_FILE_wrong_fix) — generalizable, which makes a train/test learning demo
possible rather than memorization.

---

## Slide 6 — Why you can trust these numbers

- Third-party benchmarks we did not write and cannot cheat on
  (τ³-bench scores by deterministic database-state hashing, no judge LLM;
  SWE-bench graded against gold patches)
- We measured our own contamination/memorization risk instead of assuming it away
- Statistics infrastructure already coded: Welch t-tests + Benjamini-Hochberg
  correction for every claim we will make about the learning loop
- We report unflattering results (Slide 5) most teams would omit

---

## Slide 7 — Competition: the stack exists except one layer

| Layer | Status |
|---|---|
| Skill format (`SKILL.md`) | ✅ Standardized by both AI labs, ~40 clients |
| Transport (MCP) | ✅ Everywhere |
| Memory storage / retrieval | ✅ Funded: Mem0 ($24M, AWS exclusive provider), Zep, Letta |
| Security hardening | ✅ Chainguard (supply-chain only) |
| **Evidence + lifecycle (verify → track → retire)** | ❌ Nobody |

Every competitor monetizes storage — publishing their memories' failure rates
is against their incentive. Verification has no incumbent because incumbency
requires admitting the problem.

Recent market validation: DevRev (structured memory beat fetch-RAG 94.3% vs
63.6% at 4.4× fewer tokens) and Rippletide ("freeze validated action sequences",
VentureBeat May 2026) are articulating pieces of this thesis independently.

---

## Slide 8 — Moat & roadmap

Sequence, each stage gated:

1. **Verify-and-retire loop runs end-to-end** — provenance edge per procedure,
   outcome feedback, backtest gate before promotion (the word "verified"
   becomes load-bearing)
2. **Published learning curves** — pass^k and reward-vs-trial from real
   benchmark runs; no vendor reports these
3. **Outcome-provenance API** — any agent queries a procedure's track record
4. **Cross-domain transfer** — coding → banking policy domain

Distribution rides what exists: SKILL.md import/export + MCP transport +
OpenTelemetry outcome telemetry. Nothing proprietary is required to adopt us.

---

## Slide 9 — Team

**Chaitanya Deshkar** — Final Year, AI/ML, Centre for Multidisciplinary
Education, IIT Bombay. Built the substrate end-to-end pre-funding.

**Anuj Bhadbhade** — Indian Institute of Science (IISc). Research and
validation lead.

Two-person team, zero outside capital to date, full system plus two benchmark
harnesses shipped.

---

## Slide 10 — Market

Named third-party estimates, not our projections:

- Agent memory infrastructure: **$1.2B (2025) → $18.9B (2034), 62% CAGR**
  (Market Intelo, Jul 2026)
- Agentic orchestration + memory systems: $6.27B (2025) → $28.45B (2030),
  35.3% CAGR (Mordor Intelligence)
- Asia-Pacific is the fastest-growing region at ~68.5% CAGR — relevant to an
  India-built company

Agent software overall: $86.4B → $206.5B this year (+139%), the fastest-growing
segment inside a booming market (compiled Gartner data).

---

## Slide 11 — Milestones = risk reduction

Every stage has a pass/fail test, including the possibility of a "no."

| Stage | Delivers | Pass condition |
|---|---|---|
| 1. Close the loop | Learning from real outcomes, not just storing | A stored procedure's track record visibly improves/worsens from real results |
| 2. Prove reuse | Transfer to unseen problems | Measured improvement on held-out cases |
| 3. Second domain | Not a coding-specific trick | Same approach on banking/customer-service tasks |
| 4. Public registry | Global indexing of procedures | Any agent can query a procedure's evidence before reuse |

One line: *We don't make the AI smarter. We make it stop forgetting what
already worked — and prove it.*
