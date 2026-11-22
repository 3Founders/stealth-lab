# Pilot plan for the lead (draft, 2026-10-14)

Status: draft for the founders. Local, not pushed. The lead is not named here on purpose.
Sources: `docs/HANDOFF_abtest_2026-10-11.md` section 2.2 (phase 1 A/B), section 11 (state on 2026-10-14),
`docs/abtest_followups_2026-10.md`.

## 1. What we have heard (second-hand, unverified)

- The lead is a company of about 2,000 people. That is headcount, not developers.
- They have Cursor subscriptions (so a flat seat, probably) and, as we hear it, use them mostly for
  **presentations and question answering**, not for writing code.
- First job of the pilot is to confirm or kill this on a call, not on our assumption.

## 2. Evidence we already have for this kind of work

Phase 1 A/B, same tasks in both arms. A = Claude Code on Opus alone. C = StealthLab routing to cheap models first,
Opus as the fallback. "Saving" is C's cost against A's. Source: handoff section 2.2.

| Suite | What it is | Verifier | Tasks | A solved | C solved | Saving | Cheap delivered |
|---|---|---|---|---|---|---|---|
| BoolQ | yes/no questions over a passage | label | 10 | 10 | 10 | -89% | 10 of 10 |
| IFEval | writing with format rules | rule checkers | 20 | 20 | 20 | -62% | 15 of 20 |
| BIRD SQL | question to SQL on a database | result match | 48 | 45 | 44 | -50% | 32 of 48 |
| DS-1000 | data-science Python | tests | 30 | 29 | 27 | -43% | 19 of 30 |
| OpenAPI | contract-driven code | property tests | 20 | 16 | 13 | -1% | 3 of 20 |

Reading it honestly:

- The question-answering and writing suites are where the saving is largest, and quality matched A. That supports the
  pilot idea. It is also the only evidence we have for it.
- **Small samples.** 10 of 10 has a 95% lower bound (Wilson) of about 72%; 20 of 20 about 84%. "Matched A" is a point
  estimate, not a proof of non-inferiority. A, on the same tasks, also solved all of them.
- **Automatic graders.** Each suite has a label or a rule checker. A deck, or an open question with no ground
  truth, has none. **We have no result on presentations or free-form question answering.** Nothing in the repo tests them.
- **Silent wrong answers exist.** 4 of C's misses were wrong cheap answers that passed the visible check and the
  test-first tests (bird-1028, bird-1166, ds1000-50, ds1000-831). For an analytics firm a wrong number in a deck is the
  costly error, so verification is part of the offer, not an extra.
- **The saving is against API-priced Claude Code**, not a Cursor seat. On a flat seat inside its included usage the
  dollar saving can be zero even when routing works.
- **Latency.** C's median is 56 s against A's 33 s (all 128 tasks); the Cursor test showed 45-57 s holds per dispatched
  prompt. Users feel this before they see any saving.
- BIRD SQL and DS-1000 are the closest match to analytics work (SQL and data code) and carry real savings (-50%, -43%)
  with a verifier. Ask whether their people do any of it.

## 3. Design, using Zero to One (from memory of the book)

- **The secret we test, and only this:** on routine work with a checkable answer, a cheap model plus a check matches
  the frontier model at a fraction of the price, and the company's own verified procedures make routing better each week.
- **Own a tiny market first:** the 5-8 heaviest Cursor users, chosen by spend and not enthusiasm, on one task class
  (recurring questions and recurring deck-building). The aim is to be their default, not a 50/50 test forever.
- **10x on one slice, said plainly:** BoolQ's -89% is the closest to 10x; the whole-suite figure is -38%.
  The number that matters is dispatchable share times cost ratio. Measure the dispatchable share before claiming more.
- **A definite plan, one pre-committed number:** cost per accepted task falls by at least X% on dispatched tasks,
  blind-rated quality stays within 10 points, and at least 4 of 6 users keep dispatch on in the last two weeks.
  Set X from the spend audit. Retention is the revealed-preference test.
- **Pilot as step one of a sale:** a conversion clause in the one-pager. If the number is hit, the savings share applies
  at a pre-agreed schedule with a cap and a floor. Billing only if the lower bound of the savings is above zero. A
  flat-seat customer needs a different basis (see section 6).

## 4. What to measure against (existing data, no install)

| Source | What it gives | Note |
|---|---|---|
| Cursor admin dashboard / Admin API | per-user requests, tokens, model, on-demand spend | rolling window, 30-day ranges (third-party docs, unverified); export now. Per-request dollars may be zeroed |
| Anthropic console, Claude Code analytics, or cloud billing | spend and tokens by user | if they use any |
| Plan and invoices | included usage vs actual | decides whether any dollar saving exists |
| Their last weeks of real prompts and the answers they accepted | the replay set | they redact and supply 40-60 |
| Recurring tasks (weekly deck, standing questions) | recurrence share | count-only local script, no content read |
| For data questions: the query and the figure they trusted | an executable check | the closest thing to a test in their world |

Baseline is the team's own 4-8 weeks before install, not one week. Control: comparable users at the lead who install
nothing, pulled from the same export (difference in differences), plus a crossover inside the team.

Sensitivity (assumption, replace with their real variance): if weekly spend per user varies about 50%, a 6-person team
averaged over 4 weeks has about 10% noise, so a saving of roughly 25% or more is visible and 10% is not.

## 5. Phases

0. **Discovery (week -1).** Call with the champion and 3 users: each shows the last 5 chats. Get the Cursor export and
   plan terms. Run the spend audit: model mix, a ceiling on savings, the addressable share.
   Go only if the ceiling is above about 15% and the security reviewer will approve a one-page agreement.
1. **Replay (week 0).** Their 40-60 prompts (and 60-125 if available) through both arms, outputs blind-rated by the
   requester; numbers checked against source where possible. About 100 ratings, 2-3 hours of their time. Carries the
   quality claim, since field tasks are not paired. Needs their approval for the data path.
2. **Live (weeks 1-2).** Randomised crossover by user, dispatch on or `STEALTHLAB_HOOK_MODE=off`. Cost and rework turns.
3. **All on (weeks 3-4).** Everyone on; count who keeps it.
4. **Readout (week 5).** With the AI budget owner. Rules fixed in week 0: continue, stop, or inconclusive (extend two weeks).

## 6. Known gaps before we can run this

- **Presentations and free-form Q&A are untested.** Replay (phase 1) is the first evidence; until then do not claim it.
- **Checkers for non-code work do not exist.** Candidates: figures tie to a source table, template conformance, no
  stale numbers. A research bet; say so.
- **Flat seats:** the savings-share basis fails inside included usage. Options: measure a time-saved study with no fee;
  or run only where they are over their included usage or on metered/API access.
- **Hold time** of 45-57 s per dispatched prompt; get it under about 10 s or make dispatch async/opt-in (`!cheap`).
- **Real Cursor is untested** (free plan hooks, real payloads). Run it ourselves first.
- **Data path:** dispatch sends text to General Compute models. A firm with client work will ask about retention and
  excluded repos or documents; prepare the answer and a per-workspace off switch.
- A Cursor-only user (no Claude login) gets no test-first step; the tests that gate cheap answers are not written.

## 7. Open questions for the champion

1. How many people use Cursor, on which plan, and how close to included usage?
2. What share of chats are decks, questions, data work, code? Any SQL or pandas?
3. Which recurring tasks (weekly deck, standing questions) exist?
4. Who owns the AI budget, and what is the security review path and length?
5. Can we get the usage export and 40-60 redacted prompts with accepted answers?

## 8. Who does what

Chaitanya: the relationship, the agreement, the readout. Anuj: install support, measurement scripts, the report.
The weekly-report command and the count-only recurrence script do not exist yet.
