# YC Interview Prep — StealthLab

Format: 10 minutes, ~20% of teams get in, partners score: ship-fast / understand-the-problem / real-market / coachable. Phone call = acceptance, email = rejection. Partners demand contracts/metrics mid-interview (W26 reports). Don't rehearse scripts — make progress visible.

---

## The One-Liner

> "We give AI agents verified procedural memory — so when an agent figures out how to do something once, your whole team's agents do it right forever. Think npm plus CI for what agents know how to do."

## The 20-Second Pitch

> "Teams are burning budgets because agents repeat mistakes and re-derive solutions every run — 65% of enterprise agent failures are context drift, not model capability. We extract procedures from agent experience, attach provenance and success criteria, and retrieve them under budget control at decision time. On paired benchmark instances we cut token cost 74% with accuracy held flat. Both AI labs just standardized the file format for agent skills — nobody verifies them. We're building that evidence layer."

---

## Metrics Sheet (know these cold)

| Metric | Number |
|---|---|
| Token reduction (paired) | **74.4%** — 219 vs 330 tool calls/instance |
| Paired instances | 15 |
| Accuracy | Flat: 2/15 vs htn 0/15 (liability, owned openly) |
| Wall clock / instance | 880.9s mean; tests+docker = 65%; agent loop 35% |
| Failure classes | RIGHT_FILE_wrong_fix 12 inst./8 repos · NO_EDIT_at_all 11 · LOCALIZATION_miss 9–10 |
| Market (memory infra) | $1.2B→$18.9B @62% CAGR (Market Intelo); APAC fastest @68.5% |
| Orchestration+memory | $6.27B→$28.45B by 2030 @35.3% (Mordor) |
| Competitor funding | Mem0 $24M A (186M API calls/qtr); Letta $10M seed; Zep YC W24; DevRev Enterprise-Bench: structured memory 94.3% vs fetch-RAG 63.6% @4.4× fewer tokens |
| Academic anchors | Voyager 3.3× items; AWM +24.6%/+51.1% Mind2Web/WebArena; τ-bench gpt-4o <50%, pass^8 <25% |

---

## Cluster A — Guaranteed Openers (≤20s drafts)

**1. What do you build?**
→ One-liner + "we have a working system integrated into a public agent benchmark, with measured results."

**2. Who is it for?**
→ "Engineering teams running coding and ops agents — starting with dev-tool teams where our two harnesses already speak their language. Five design-partner conversations in flight."

**3. Why now?**
→ "Both labs standardized SKILL.md within eight weeks of each other. Portability creates install decisions; enterprises won't install unverified machine-written procedures. The evidence layer doesn't exist yet."

**4. How's it going? What's the traction?**
→ "Working substrate, 74% cost cut on paired benchmarks, tau2 integration shipped, leaderboard attempt running this month, OSS launch next." *(Have one number that didn't exist last month.)*

**5. Tell us about the team.**
→ "IIT Bombay + IISc. I built the pipeline and harnesses end-to-end; Anuj owns systems and evaluation. We've been building this since [date], full-time outside coursework."

**6. Who are competitors? What if Mem0 adds this?**
→ "Mem0/Zep/Letta store facts and conversation context — episodic memory. DevRev showed structured memory beats fetch-RAG 94 vs 64 with 4× fewer tokens — validating our direction from inside a $1B company. Nobody does procedures with provenance and lifecycle. If they add it, they validate the category; our registry evidence compounds daily and can't be back-filled."

**7. What's your moat?**
→ "Evidence accumulates per execution. A copy of our code starts empty; ours knows which procedures work in which environments."

**8. How do you make money?**
→ "Per-seat registry + usage-based verification runs; free read API drives adoption. Comparable rails: WorkOS pricing on top of open transport."

**9. What have you learned / biggest surprise?**
→ "The hard part isn't retrieval, it's the write side — knowing a procedure still works. Our failure classes replicate across independent repos, which means the problem is structural, not ours to wish away."

**10. What's next / why YC?**
→ "Close the learning loop, ship the OSS launch, land five design partners in the batch. YC compresses twelve months of enterprise trust-building into ten weeks."

---

## Cluster B — Hard Questions

**"You're both students. Are you dropping out? Full-time?"**
→ "Chaitanya graduates [month]; Anuj [month]. We're applying through Early Decision precisely because we planned the company around graduation, not instead of it. Between now and then: [visible progress list]."

**"Isn't this just RAG over logs?"**
→ "RAG fetches text; it has no success criteria, no provenance, no lifecycle, no execution-based promotion. DevRev's numbers prove structured memory beats fetch — 94 vs 64. Fetch is the baseline everyone abandons at scale."

**"Your own data shows accuracy flat. Why should anyone believe efficiency alone sells?"**
→ "Correct — and we say it before you do. Efficiency pays today (tokenmaxxing is a board-level pain), accuracy gains come from closing the learning loop, which is milestone three. We'd rather own the honest baseline than inflate it."

**"Open-source commoditizes you."**
→ "The format and transport are already open standards — that's the point. Verification infrastructure is the paid layer on top, like Red Hat on Linux or WorkOS on OAuth."

**"Why Bangalore/Mumbai and not SF?"**
→ "APAC is the fastest-growing memory-infra region at 68.5% CAGR; both campuses give us design partners and talent. YC batch in SF; GTM follows the customers — dev-tool teams are global from day one."

**"This smells like a research project. Where's the business?"**
→ "Every artifact doubles as product: the benchmark harnesses are the verification service; the leaderboard run is demand gen; the registry is the SKU. Research credibility is our sales channel into dev-tool buyers."

---

## Demo Script (90 seconds, rehearsed crashes included)

1. Live terminal: run paired instance → show tool-call counters (219 vs 330).
2. Open substrate: `substrate_search` → progressive disclosure (signature → references) → procedure card with provenance edges.
3. Show DB-hash deterministic verdict on a banking task.
4. **If anything crashes:** narrate while recovering — "what you're seeing fail is exactly the LOCALIZATION_miss class we measure; here's the failure record it just wrote." (W26 precedent: recovering while narrating what ran landed the acceptance.)
5. Close on the layer map slide — point at the ❌ row.

---

## Anti-Pattern Checklist

- ❌ Vision-speak ("agentic internet," "replace journals") in the first 60 seconds — horizon lives on Slide 9, offered only if asked
- ❌ Jargon without a concrete user+outcome first
- ❌ Rehearsed-sounding monologues; answers ≤20s, then stop
- ❌ Hiding the accuracy liability — pre-disclose it
- ❌ Claiming market numbers without named sources
- ❌ Talking over partner interruptions — they're testing coachability

---

## Practice Protocol

- Daily: 2 mock interviews (one hostile), each answer timed ≤20s
- Alternate who fields team questions; never answer for each other
- Record and review: filler words, jargon leaks, vision-speak
- Before every practice: update one metric — make progress visible
- T-minus-72h rule: no new prep material; only reps, metrics sheet, demo

## Action Timeline

| When | Action |
|---|---|
| Now | Residency BLR Founders Track application (starts Sept 7) · EV India submission (draft ready) |
| This week | SINE/IISc campus channels · Antler Before Day Zero · 5 dev-tool user calls |
| Sept–Oct | τ³ leaderboard result public · OSS launch · YC app (ED vs W27 decision by graduation date) |
| Oct 4 | PearX W27 deadline |
| Oct 12–Nov 1 | a16z Speedrun SR008 priority window |
| Late Oct–Nov | Expected YC W27 window |
