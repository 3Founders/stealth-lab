# YC Interview Prep — sourced question bank + drafted answers

Research basis (all read): YC's official interview guide, Dalton Caldwell's
admissions criteria (TechCrunch/YC blog), YC Roaster S26 real-question
architecture, YC Insights 10-question core, Flowjam 60-question scrape of
200 recaps, alumni prep repo, and real W26/S26 acceptance post-mortems
(Sentrial, camelAI, Mark Pothen, Jiangda Wang, Compresr, Reducto).

## Format facts

- 10 minutes, Zoom, 2–3 partners who have read the application. Same-day decision.
- ~20% conversion conditional on interview (vs ~1.5% overall).
- Partners score four things under every question: can you ship fast, do you
  understand the problem better than anyone, is the market real, are you
  coachable when challenged.
- YC's own #1 advice: make visible progress between application and interview.
  Over-prepared founders who answer half-asked questions interview worse.
- Numbers may be written down next to the laptop — explicitly allowed.

## The ten guaranteed openers (drafted ≤20-second answers)

**Q1. What are you building?**
> "Teams running AI coding agents pay twice for every mistake — their agents
> re-solve problems they've solved before. We built a system that captures what
> agents learn, tests whether it actually works, and lets other agents follow
> procedures that come with proof instead of guesswork."
(Pattern: concrete user + concrete outcome, no adjectives — YCRoaster.)

**Q2. Who are your users?**
> "Right now, engineering teams running coding agents on production repos — the
> highest-frequency repeat-work use case. We haven't launched publicly yet; we
> have spoken with [N] developers this week and here is what they told us."
(ACTION BEFORE INTERVIEW: complete 5+ real conversations so this sentence has
a number and a name. Pattern: camelAI ran a 48-hour user-call sprint after the
invite specifically so real anecdotes were ready.)

**Q3. How do you know they want it?**
> "Behavioral evidence, not opinions: agents consume 5–30× the tokens of
> chatbots per task, Uber burned its annual AI budget in four months mostly
> re-deriving known answers, and Gartner forecasts 40% of agentic projects get
> cancelled by 2027 for cost, value, and reliability reasons — the exact things
> verified memory fixes. Our paired benchmark shows 74.4% token reduction."
(Pattern: quantify pain in hours/dollars; never quote encouraging interviews.)

**Q4. Your numbers?**
Use the metrics sheet below. Say absolute numbers first, growth second.
Small-but-real beats big-and-fuzzy. Never compute live.

**Q5. Why you two?**
> "We built the entire substrate before taking any money — 22,500 lines, 500+
> tests, two independent benchmark harnesses. Anuj handles research and
> validation design; I handle systems and product. Letta came out of one
> Berkeley paper; we're showing up with the system already working."
(Dalton Caldwell: technical excellence + founder/market fit are the filters.)

**Q6. How do you make money?**
> "Usage-based API — charged per verification run and per query — mirroring the
> category leader's pricing model, which proves enterprises pay for this layer:
> Mem0's hosted tiers run $19 to $249 a month. Open-source core drives adoption;
> hosted cloud monetizes; enterprise licensing follows."

**Q7. Competitors, and why will you win?**
> "Mem0 raised $24M and is AWS's exclusive memory provider — they proved
> companies buy memory. Zep does temporal knowledge graphs; Letta builds the
> agent-owned runtime. Credit where due: all three store and retrieve facts.
> None verify whether stored procedures work, track outcomes after reuse, or
> retire entries when they go stale. And none can — publishing your memory's
> failure rate undermines a storage business. Independent work shows flat
> fact-memory even loses raw recall to plain long-context. Verification is the
> open problem, and we're structured to own it."
(Pattern: name 2–3, credit each, state your wedge — YCRoaster. Never "no competitors".)

**Q8. Biggest risk?**
> "Two honest ones. Cold start: a verification library needs accumulated
> evidence, which we seed from benchmark corpora and close the loop on through
> extraction. Second: we haven't yet validated willingness to pay. Our way to
> find out fast is launching the MCP server openly and converting five design
> partners within the batch."
(YC guide: candid discussion of obstacles convinces more than glib dismissal.)

**Q9. What have you shipped recently?**
Real list — update before the interview: step-tracker instrumentation across
agent toolkits, exact-enum rendering fix for procedure steps, phaseH benchmark
run, Gemini embedding migration, prompt cross-check protocol. Velocity is the
most legible founder signal.

**Q10. What happens if we don't fund you?**
> "We keep building. This isn't contingent on YC."
One sentence, no hedging.

## Questions THEY specifically will ask us

**"Mem0 raised $24M, won AWS, does 186M API calls a quarter — why does the world need another memory layer?"**
> "Because they won the storage war and left verification unsolved. Their own
> category's independent evaluation showed flat fact-memory losing raw recall to
> plain long-context — storing harder isn't the same as knowing what's true. We
> don't compete for storage; we're the trust layer above every store."

**"Isn't this just RAG / a feature?"**
> "RAG retrieves text; it can't tell you whether retrieved advice worked last
> time or was superseded last week. We tested this directly: on regulatory
> documents, graph-based retrieval beat vector RAG by 70% precisely because
> supersession edges encode what's current. Features get copied; a track-record
> dataset compounds."

**"You're researchers — can you sell?"**
> "We've been selling internally for months: to reviewers, to benchmark
> maintainers, to ourselves — every claim in our repo survives adversarial
> checking. That discipline is the sales pitch to exactly our buyer: teams who
> got burned trusting vendor benchmarks."

**"Accuracy didn't improve in your experiments — why would anyone adopt?"**
> "Because the efficiency result alone pays for the system, and the accuracy
> gap has a diagnosed cause with a shipped fix awaiting measurement — wrong-tool
> selection, which step-tracking now surfaces mechanically. We'd rather show
> you an honest 74% than a dressed-up 5%."

**"What stops OpenAI from making context windows infinite?"**
> "Nothing stops bigger windows, and independent comparison says that's
> partially right — long-context beats flat fact-memory on recall. But cost
> still crosses over around ten turns at 100k context, and neither window size
> nor retrieval verifies anything. Provenance and retirement are orthogonal to
> both — that's our lane."

**Team questions:** biggest disagreement (pick a real resolved technical
debate, e.g., precondition strictness — show resolution process); equity split
(clean equal split with standard vesting — know it cold); who codes/sells
(both code; Chaitanya owns product/GTM, Anuj owns research/validation).

**Simultaneous-separate-questions trick:** answers must match identically —
equity, commitment level, runway, who decided to apply.

## Metrics sheet (know cold; print one page)

- 74.4% token reduction (2,579,395 → 660,938), ~34% fewer tool actions
- n = 15 paired instances (rigorous paired set; wider 28-instance pool unpaired)
- Failure class: RIGHT_FILE_wrong_fix — 12 instances, 8 repos
- Codebase: ~22,500 LOC backend, 500+ passing checks, 601-test green suite
- τ³-bench dev12: 11/12 avg reward 0.27 (procedures arm)
- Market: memory infra $1.2B→$18.9B @62% CAGR (Market Intelo);
  orchestration+memory $6.27B→$28.45B @35.3% (Mordor); APAC fastest @68.5%
- Competitor facts: Mem0 $24M total (Basis Set led Series A Oct 2025), 186M
  API calls/qtr Q3'25, exclusive AWS Agent SDK provider; Zep Graphiti 27k★,
  LongMemEval 63.8 (GPT-4o); Letta $10M seed Felicis, $70M post
- Timing: Gartner <5%→40% enterprise apps with agents in one year;
  >40% project cancellations forecast by end-2027

## Demo script (30 seconds, raw localhost)

1. Retrieve a verified procedure for a task (substrate_search) — show the
   VALID ARGUMENT VALUES block and step list
2. Agent executes; step-tracker prints "N of M steps done"
3. Show a superseded procedure being refused (truth_state=OUT filtered)
Crash-recovery narration (W26 precedent: localhost failed live and recovering
while narrating what was actually running is what convinced partners):
if anything breaks, say what IS working and pivot to the benchmark JSONL tab.
Have ready in tabs: benchmark results JSONL, repo stats page, this metrics sheet.

## Evidence pack (partners may demand proof mid-interview — W26 precedent)

Pre-staged shareable links/files: benchmark run logs, test-suite output
(601 green), repo commit history, spec v4 document. Share in seconds, not minutes.

## Anti-pattern checklist (from YC official + alumni repo)

- No monologues: >20 seconds gets cut; answer in 1–3 sentences, let them dig
- Never say "we have no competitors"; never dress up early metrics
- Partners verify numbers post-interview — every figure must be reproducible
- No live decisions mid-interview ("let me check with my co-founder" = fail signal)
- Each founder answers ≥1 question minimum; agree topic ownership beforehand
  (Chaitanya: product/systems/metrics · Anuj: research/validation/methodology;
  shared: business-model and commitment answers)
- Don't read tea leaves afterward; tone means nothing — keep building either way

## Practice protocol

- Daily 10-minute timed mock with deliberate interruptions until automatic
- Cut every written answer in half, then again
- Rehearse stopping: answer, then silence
- One founder plays hostile partner daily; swap roles
- Rehearse the one question you fear most first

## 72-hour rules (YC Roaster)

No new code the night before. Numbers page printed. Demo tab pre-loaded and
logged in. Ethernet not Wi-Fi. Sleep. The signal partners seek can't improve in
72 hours; panic can obscure it.

## Vision questions (use sparingly, measured voice)

**"Where does this go long-term?"**
> "Both labs standardized how agents package what they learn. Formats don't
> verify themselves. We intend to be the peer-review and registry layer for
> machine-written procedures — the part every platform eventually checks
> against, the way npm became the default index for packages."

**"Is this the Semantic Web again?"**
> "Fair question — the failure modes were documented: humans had to author,
> populate, and maintain knowledge forever, with no reader. Here agents generate
> the content as a byproduct of working, agents are the consumer, and upkeep is
> statistical automation. Same goal, inverted economics."
