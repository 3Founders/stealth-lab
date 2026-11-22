# The Procedure Page

**Research findings and a design proposal — 2026-11-20**

The question: should a Way (procedure) get its own public page, separate from the goal
and the flow that lists it — the way a YouTube channel has a page, or an n8n template has
one? And if so, what should that page *be*, given that a Way is versioned, serves a Goal,
and carries **verification evidence**?

Short answer: **yes, but neither YouTube's nor n8n's page is the right model.** A Way is
not content (YouTube) and not a template (n8n). It is closest to an **npm package that
ships with a benchmark result** — and the page has to be designed for that, because the
thing that makes it worth reading is the evidence, not the description.

---

## 1. What the prior art actually does

Five families of page were examined. Each solves one of our three problems and none
solves all three.

### 1.1 Template galleries (n8n, Zapier, Make)

[Web Anatomy analysed 197 template galleries and template pages](https://www.webanatomy.ai/templates-page-examples):

- 80% of gallery cards carry a thumbnail; 63% a one-line description
- 54% of galleries have filters, 45% a search box, 69% categories
- On a **single template page**: 80% show a preview, only 23% a live preview, 30% a
  step-by-step setup guide, 10% a details table
- 87% link back to the gallery; 59% link sideways to other templates

n8n's own template model is *workflow + setup instructions*, with the title and
description generated automatically by the Creator portal
([n8n docs](https://docs.n8n.io/contribute/create-templates)). **There is no versioning
and no evidence.** A template is a snapshot that either works on import or does not.

**What to take:** the 30% that do a setup walk-through are the ones that convert, and the
one-line description on every card. **What not to take:** the snapshot model — it has no
concept of a second version, which is half of what we need.

### 1.2 Channel pages (YouTube)

YouTube's channel page is built for **two visitors**: a newcomer who needs to decide
whether to follow, and a returning subscriber who wants the next thing. YouTube
operationalises this with a *trailer* for non-subscribers and a *featured video* for
subscribers ([YouTube Help](https://support.google.com/youtube/answer/3219384?hl=en)),
up to 12 custom sections, and an about section whose first line is the promise.

**What to take:** the two-audience split is real and applies to us — a visitor arriving
from a search result needs "what does this do and does it work", a returning user needs
"what changed since 2.1". **What not to take:** the creator-economy framing. A Way's
identity is not personality-driven; nobody subscribes to a Way.

### 1.3 Package registries (npm, PyPI, crates.io, deps.dev)

This is the closest structural match, because registries have solved **versioning
presentation** properly.

- **npm:** a meta-rail of single-fact modules (Install / Repository / Homepage / Weekly
  Downloads / Version+License), hairline-separated rather than boxed, with one module
  carrying an inline sparkline. Tabs for Code, Dependencies, Dependents, Versions
  ([design analysis](https://swipefile.design/ref/npm-package/)). npm's own page
  redesign put the README first, the install command above the fold, and download counts
  for day/week/month ([npm blog](https://blog.npmjs.org/post/104856015780/npm-has-a-new-website.html)).
- **npmx.dev** adds the things npm lacks: version diff, changelog view, **timeline**
  (publish history and notable version events such as deprecations), and provenance
  indicators ([npmx features](https://docs.npmx.dev/guide/features)).
- **deps.dev** (Google) shows the **full transitive dependency graph**, decorated with
  versioning, licences, vulnerabilities and OpenSSF Scorecards, and explicitly supports
  *comparing two versions to see how the dependencies changed in a new release*
  ([deps.dev](https://deps.dev/), [API](https://docs.deps.dev/api/v3/)).

**What to take:** the meta-rail, the version timeline, the version comparison, and the
dependency graph as a first-class visual. **What not to take:** the assumption that
"installed" equals "works" — a registry never claims the package does anything.

### 1.4 Model and benchmark hubs (Hugging Face, Papers with Code, LMArena)

This is where **evidence presentation** is solved.

- **Hugging Face model cards**: YAML metadata for search, then description, usage
  examples, technical specs, and **performance metrics** — the release checklist puts
  benchmarks explicitly on the card ([HF docs](https://huggingface.co/docs/hub/main/model-release-checklist)).
- **Papers with Code leaderboards**: the columns are the whole design — Task, Dataset,
  Metric, Model, Score, Paper, Code. Its own maintenance guide insists every row must
  satisfy the same contract: *same metric, same split, same evaluation protocol, and a
  score faithful to the source* ([verify-evals](https://github.com/huggingface/paperswithcode/blob/ca69dd89bcf849ca1f2ab07a388a28cb1da0c0e3/.agents/skills/verify-evals/SKILL.md)).
  **"A leaderboard is not just a ranking. It is a compact visual map of benchmark claims
  and supporting evidence."**
- **LMArena** reports a score **with a ± interval, a raw rank, and a rank spread** — the
  best and worst rank a model could hold given every other model's confidence interval.
  Ties are defined as overlapping spreads ([Arena's ranking method](https://arena.ai/blog/ranking-method)).
  A reader is told: *compare spreads, not raw ranks.*

**What to take:** this is the single most important contribution to our design.
**A number without a sample size and an interval is a marketing claim.** We already
compute a Wilson lower bound; the page must show the interval, `n`, and who ran it.

### 1.5 Evidence grading in medicine (GRADE)

GRADE rates a body of evidence **high / moderate / low / very low** per outcome, and —
critically — **attaches standardised language to each level** ([CDC](https://www.cdc.gov/acip-grade-handbook/hcp/chapter-11-communicating-findings-from-the-grade-certainty-assessment/index.html)):

| Certainty | How the finding is worded |
|---|---|
| High | "X **results in** a large reduction" |
| Moderate | "X **likely** results in…", "X **probably** reduces…" |
| Low | "X **may** reduce…", "**the evidence suggests** X reduces…" |
| Very low | "**the evidence is very uncertain** about the effect of X" |

The reason this matters: it forces the *writer* to be honest in a way readers can audit,
without a statistics background, and the levels are justified in footnotes.

**What to take:** the plain-language certainty ladder, and the footnote discipline. This
is the best available model for turning a Wilson interval into a sentence a buyer can
act on.

**What not to take:** the scoring machinery itself — GRADE's rating is a judgement over
many studies; ours is a single computed interval over our own runs. We should borrow the
*wording and honesty*, not the eight-domain assessment.

---

## 2. What our three requirements demand

The page must represent, simultaneously:

1. **Versioning** — semver, pinned sub-procedure dependencies, supersession, what
   changed, how to go back.
2. **The Goal** — the problem this Way exists to solve, in the user's own words.
3. **Verification** — success rate, interval, sample size, who ran it, on what benchmark,
   and whether anyone independent has reproduced it.

**None of the five prior art families does all three on one page.** Registries do (1)
well and (3) not at all. Benchmark hubs do (3) well and (1) not at all. Templates do
neither. This is genuinely unoccupied ground, and it is the reason a dedicated page is
worth building rather than folding into the goal page.

---

## 3. Proposed page anatomy

Eight sections, in order. The order is deliberate: it is the order a *sceptical buyer*
asks the questions.

```
/procedures/{procedure_id}@{version}
```

### 1. Identity block (above the fold)

```
Refund a captured payment                         v2.3.0   [Verified]
Serves Goal →  "Reverse a customer's charge without touching the ledger by hand"
By @ada · 6 versions · first published 2026-09-14 · updated 2026-11-11
```

- Way name in the display face, version beside it (npm's pattern)
- **State the Goal inline**, linked — this is the differentiator. A procedure is
  meaningless without the problem it solves; most registries make you hunt for it.
- A **verification badge** whose meaning is one click away (see §4)
- Publisher, version count, dates

### 2. Evidence panel (the reason the page exists)

Sits directly under identity, **before** the description. This is the inversion that
matters: npm puts the README first; we put the *evidence* first, because a reader who
does not believe it works should not bother reading how.

```
Verified success        74.2%         95% CI  66.1 – 81.0
                        n = 112 verified runs · 4 benchmarks · last run 2026-11-09
Benchmarks              payment-refund-v2 (61/80), refund-partial (23/32)
Ran by                  kell (94 runs), @partner-org (18 runs, independent)
Reproduced              ✅ independently reproduced by one other party
```

Rules taken from the research:

- **Never a bare percentage.** Interval and `n` sit beside it (LMArena's lesson).
- **Name the benchmark and the split** (Papers with Code's apples-to-apples contract).
- **Separate the runner**, so a self-run score is visibly self-run.
- **Say "independently reproduced" only when it is true**, and link the run.
- Plain-language certainty from the GRADE ladder when the evidence is thin:
  *"The evidence is uncertain: only 6 runs, and none independent."*

### 3. What it does, and when it applies

Two short prose paragraphs — the L-HTN `description` and `applies when`. Not a feature
bulleted list. The applicability sentence is what a router uses, so showing it is honest:
the reader sees the same condition the machine sees.

### 4. The decomposition (the graph)

The steps, rendered as the `ProcedureGraph` we already have, with:

- each step's `kind` (human / automated) visible
- a step that references another Way shown as a **collapsed node with its pinned version**,
  clickable to that Way's page at that version
- a plain "as written" toggle, because a graph is a projection and prose is the source

Draw the graph the way deps.dev draws dependencies: **the version is on the node**, not
in a legend.

### 5. Version history

The npmx **timeline**, not a changelog wall:

```
v2.3.0  2026-11-11  Breaking: step 3 now requires the ledger read      [current]
v2.2.1  2026-10-28  Fixed: partial refunds below ₹1
v2.2.0  2026-10-19  Added: retry note on the gateway step
v1.4.7  2026-09-30  superseded by 2.x
```

Each row: version, date, the *effect* in one line, and a **Breaking:** marker where
applicable — Keep a Changelog's discipline, which is explicit that the marker belongs on
the entry and must say **what breaks**:

> "Say what breaks. The word means little until readers know which interface you keep
> stable."

Plus a **compare** affordance (deps.dev / npmx): `v2.2.1 → v2.3.0` showing which steps,
preconditions and dependencies changed.

### 6. Composition and dependents

Two directions, both from deps.dev:

- **Composed of** — the pinned sub-procedures, with versions and their own verification
  status. If a dependency is unverified, say so here: verification does not inherit
  silently.
- **Used by** — other Ways and Goals that reference this one. npm's "dependents" (which
  npmx still lists as *coming soon*, an honest gap we can beat).

### 7. Provenance and how to verify it yourself

The npm provenance lesson: a badge is only worth something if a reader can check it.

```
Published from      trace t_01J8… (redacted, 41 steps)
Extracted by        kell extractor v7 · 2 model calls
Evidence records    112 rows · open in the evidence browser
Re-run this Way     kell way run <id>@2.3.0 --benchmark payment-refund-v2
```

The last line is the one nobody else offers: **the reader can reproduce the claim.**

### 8. Sideways navigation

87% of template pages link back to the gallery and 59% link to sibling templates. Ours:
the parent Goal, sibling Ways that serve the same Goal, and the benchmarks used.

---

## 4. How to present verification honestly

This is where a page can do real damage, so it gets its own rules.

**Do**

- Show **integer counts**, not just rates: `61/80` is auditable; `76.25%` is not.
- Show the **interval** and name it: "95% CI (Wilson)". We already use Wilson.
- Show **`n`** and the **date of the last run**.
- Show **who ran it**, and separate self-runs from others.
- Use the **GRADE wording ladder** for plain language, and only claim "results in" at
  high certainty — otherwise "likely", "may", or "the evidence is uncertain".
- Mark **superseded versions** plainly, with the same evidence they had at the time.
  A v1 score is not evidence about v2.

**Don't**

- Don't show a bare percentage or a star rating. Stars are a review economy; we are
  claiming measured success.
- Don't aggregate across benchmarks into one number unless the benchmarks share a
  contract — Papers with Code's guide calls mixing splits and metrics "the most common
  failure mode" on leaderboards.
- Don't let verification inherit through composition. A Way built from an unverified
  dependency is unverified, and the badge should say so.
- Don't rank Ways *against each other* on this page. LMArena's rank-spread work shows
  that ordering by point estimate favours high-uncertainty entries; ranking belongs on
  an explicit comparison view, not on an identity page.

---

## 5. Should this be a separate page at all?

**Yes, and the reasons are structural rather than aesthetic.**

1. **A Goal is a question; a Way is an answer.** Many Ways answer one Goal, and one Way
   can answer several. Folding the Way into the goal page forces a many-to-many into one
   page.
2. **Version identity is URL-shaped.** `/{procedure_id}@{version}` is linkable, citable
   and pinnable — which is exactly what the composition edge needs (`subprocedure_ref`
   already stores `{procedure_id, version}`). A page per version is the natural surface
   for that pointer.
3. **Evidence is per-version, not per-Goal.** If two Ways serve one Goal, their
   verification is separate and must not share a page.
4. **The buyer's question is per-Way**: "does *this* work, at *this* version, and can I
   check it?"

What should **not** be a separate page: the *run*. Runs belong in a list under the Way,
not their own top-level surface, until there are enough of them to browse.

---

## 6. Discussion without worthless debate

The page should encourage improvement and engagement, not argument. Those pull in
opposite directions, and most products resolve the tension badly — a comment box at the
bottom of the page, then moderation to clean up the mess.

**The way out: do not build a comment box. Build an improvement track.**

### 6.1 The claim: conversation is the wrong primitive

A comment thread asks people to *say things*. An improvement track asks them to *propose
things*. The difference is not tone, it is **schema**:

| | Comment thread | Improvement track |
|---|---|---|
| Unit | an opinion | a proposed change, to a named part |
| Outcome | a thread that ends when interest dies | an accepted version, or a closed refusal |
| Effort | seconds | minutes |
| Survives | scrolls away | becomes v2.4.0 with attribution |
| Can be verified | no | yes — it is a Way like any other |

The repo already has the right shape for this and it is not being used to its potential:
`submission_type in ("new", "improvement")`, with `parent_procedure_row_id` required for
an improvement. **An improvement is a first-class submission that produces a new version
of the Way.** That is engagement with a destination.

So: the page's discussion surface **is** the improvement form, and everything else
(discussion proper) is deliberately small.

### 6.2 Three gates before an opinion becomes a proposal

Borrowed from how issue trackers keep quality up without closing themselves:

1. **Anchor it.** A proposal must attach to something concrete: a step (by `order`), a
   precondition, a benchmark, or the whole Way. Free-floating commentary has no home and
   gets none.
2. **State the evidence.** The proposer says what they observed: "on the partial-refund
   benchmark this fails when the amount is under ₹1" beats "this is broken". Where the
   system can, it **auto-attaches the run** that motivated it.
3. **Say what would change.** One sentence describing the new behaviour. If someone
   cannot say it, they are reporting a bug, not proposing an improvement — and there is
   a separate, cheaper door for that (the existing `report_way`).

That third gate is the whole design. It is the difference between "worthless debate" and
"a proposal".

### 6.3 The three doors, in increasing cost

Put them side by side, in this order, so the cheap ones absorb most traffic:

| Door | Cost | Needs | Outcome |
|---|---|---|---|
| **Ran it, got a result** | one click | signed in | evidence row → moves the score |
| **Report a problem** | a category + a sentence | signed in | moderation queue (exists already) |
| **Propose an improvement** | anchor + evidence + what changes | signed in | a new version, with attribution |

**"Ran it" is the engagement primitive**, not commenting. It is the single most useful
thing a reader can do, it costs one click, it is unfalsifiable in the useful direction
(it is a run, not a claim), and it *directly improves the thing they came to read* —
the evidence panel in §3.2. A page whose primary action is "run this and tell us what
happened" generates exactly the engagement we want and none of the engagement we don't.

Note what this does to the score: every contribution makes the number *more* accurate
rather than more flattering. That is the opposite of a review economy.

### 6.4 What to do about actual discussion

Some discussion is necessary — "has anyone used this at scale?", "which variant fits a
team of five?". Handle it in the smallest surface that works:

- **A single question thread per Way**, not per step. Questions are answered by the
  author or by anyone with a verified run on that Way.
- **No nested replies.** One level. Nesting is where debate grows.
- **Answers that contain a run are promoted** out of the thread into the evidence panel.
  This is the mechanism that turns talk into data, and it is why the thread can stay small.
- **Threads auto-close on a new major version.** A question about v2.2 is not a question
  about v3.0, and pretending otherwise is how stale disagreement accumulates.

### 6.5 Attribution and incentives, already built

The economy is already wired for exactly this:

- `IMPROVEMENT_REWARD` (20) is **double** `NEW_PROCEDURE_REWARD` (10) — improving pays
  better than adding, which is the incentive we want.
- `PARENT_ATTRIBUTION_SHARE` (0.25) means the original author keeps earning when their
  Way is improved and reused. Nobody loses by being improved.
- The caps (`DAILY_REWARD_CAP_PER_CONTRIBUTOR`, `WEEKLY_…`) already stop farming.

**Credit on the page, not just in the ledger**: an accepted improvement shows the
proposer on the version row ("v2.4.0 — proposed by @sam, verified by 2 independent runs").
Attribution that is visible on the page is worth more than credit in a balance.

### 6.6 What deliberately is not built

Named so nobody adds it later without arguing for it:

- **No upvote/score on proposals.** Votes measure popularity, and the question is whether
  the change is correct. A proposal is judged by whether it verifies, not by how many
  people liked it.
- **No open-ended "general discussion" section.** It is where worthless debate lives.
  Questions are anchored to a Way; everything else goes to the contact form.
- **No reply chains.** One level, always.
- **No leaderboard of commenters.** Standing already exists and is computed from real
  contributions — accepted submissions and verified outcomes — not from volume of talk
  (`app/economy/standing.py`).

### 6.7 The test for whether this works

A good engagement metric here is not "comments per Way". It is:

- **runs per Way** — the primary engagement signal
- **% of Ways with at least one accepted improvement**
- **time from a proposal to a verified version**
- **share of proposals that were accepted** (low is fine if the refusals taught something)

If "comments per Way" is high while "runs per Way" is low, the page is building a forum
instead of an evidence base — which is the failure mode this section exists to prevent.

---

## 7. What to build first

Ordered by value per unit of work:

1. **The evidence panel** (§3.2). Highest value, smallest surface. Everything needed is
   already computed: Wilson lower bound, `procedure_usage_events.outcome_state`,
   `plan_entitlements`, evidence rows.
2. **The version timeline with a Breaking marker** (§3.5). Mostly a query over
   `supersede_procedure` lineage that already exists.
3. **The composition graph with pinned versions on the nodes** (§3.4). Reuses
   `ProcedureGraph.tsx`; the work is showing versions and making nodes link.
4. **Provenance + re-run command** (§3.7). The differentiator, but it needs a stable
   run CLI first.
5. **Dependents** (§3.6). Cheap once composition is queryable, and npmx still hasn't shipped it.
6. **The "Ran it, got a result" action** (§6.3). One button, one evidence row. It is the
   engagement primitive and it improves §3.2 at the same time.
7. **The improvement track** (§6.2). The improvement submission already exists in the
   economy (`submission_type='improvement'`); this is the form on the page that anchors it
   to a step, and the version row that credits the proposer.
8. **The question thread** (§6.4). Last, smallest, one level. Deliberately the least
   important surface on the page.

---

## 8. Open questions

1. **Anonymous runs.** Should a run count toward the headline evidence if the runner is
   not signed in? (Papers with Code's contract suggests yes, but labelled.)
2. **Evidence decay.** Does a 14-month-old 100% score still count? GRADE would downgrade
   for indirectness; we have no rule yet.
3. **One badge or a ladder?** A single "Verified" mark is legible but lossy. A
   three-level ladder (verified / independently reproduced / in progress) is honest but
   invites gaming the definitions.
4. **Who may publish a Way at all** — any signed-in person, or reviewed first? n8n
   reviews every template; we currently auto-screen.
5. **Does an improvement proposal need a run behind it?** §6.2 asks for stated evidence;
   requiring an *attached* run would raise quality and lower participation. Today's
   `NEEDS_REVIEW` path suggests we can start soft and tighten with data.
6. **What happens to open threads on a new major version?** §6.4 says auto-close. The
   alternative — carry them forward — is where stale debate accumulates, but closing
   loses the person who was mid-conversation.

---

## Sources

- [Web Anatomy — 197 template galleries analysed](https://www.webanatomy.ai/templates-page-examples)
- [n8n — create workflow templates](https://docs.n8n.io/contribute/create-templates)
- [YouTube Help — channel layout](https://support.google.com/youtube/answer/3219384?hl=en)
- [Swipefile — npm package page design reference](https://swipefile.design/ref/npm-package/)
- [npm blog — the new package page](https://blog.npmjs.org/post/104856015780/npm-has-a-new-website.html)
- [npmx — features (version diff, changelog, timeline)](https://docs.npmx.dev/guide/features)
- [deps.dev — dependency graphs](https://deps.dev/) · [API v3](https://docs.deps.dev/api/v3/)
- [Hugging Face — model release checklist](https://huggingface.co/docs/hub/main/model-release-checklist)
- [Papers with Code — verify-evals (apples-to-apples contract)](https://github.com/huggingface/paperswithcode/blob/ca69dd89bcf849ca1f2ab07a388a28cb1da0c0e3/.agents/skills/verify-evals/SKILL.md)
- [LMArena — Arena's ranking method (CIs, rank spread)](https://arena.ai/blog/ranking-method)
- [CDC — GRADE certainty, plain-language wording](https://www.cdc.gov/acip-grade-handbook/hcp/chapter-11-communicating-findings-from-the-grade-certainty-assessment/index.html)
- [Cochrane Handbook ch.14 — summary of findings](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-14)
- [npm docs — viewing package provenance](https://docs.npmjs.com/viewing-package-provenance/)
- [OpenSSF Scorecard](https://scorecard.dev/)
- [Keep a Changelog 2.0.0](https://keepachangelog.com/en/2.0.0/)
- [Salesforce AgentExchange — listing anatomy](https://trailhead.salesforce.com/content/learn/modules/appexchange_basics/appexchange_basics_listings)
