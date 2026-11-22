# Pages to have, and why

**The site map, decided. 2026-11-21.** One page is built (`/way/[id]`); this says what sits around it and
what each one is *for*. The test every row must pass: **if this page were deleted, what would a user be unable
to do?** A page that fails that test is a section of another page.

The rule that shapes the whole map comes from `docs/KELL.md` §4: **a Way is closest to an npm package that
ships with a benchmark result.** npm needed a registry index, a package page, a version page, and a search.
So does this.

---

## 1. The map

| # | Page | Route | Answers | State |
|---|---|---|---|---|
| **1** | **Way** | `/way/[id]` | *Should I trust this, and how do I run it?* | ✅ **built** |
| 2 | Ways index | `/ways` | *What exists? What is verified?* | next |
| 3 | Goal | `/goal/[id]` | *What is the job, and which Ways claim it?* | |
| 4 | Composition | `/way/[id]/graph` | *What is this made of, and what is made of it?* | |
| 5 | Claim | `/claim/[id]` | *What was measured, would it survive being wrong?* | |
| 6 | Run | `/run/[id]` | *What exactly happened, can I re-run it?* | |
| 7 | Harness | `/harness/[digest]` | *Was this result comparable to that one?* | |
| 8 | Corpus | `/research` | *Where are all the claims?* | |
| 9 | Diff | `/way/[id]/compare/[other]` | *Which version should I move to?* | |

Two are deliberately **not** pages: **discussion** (a Way page has three doors — ran it, report, propose —
never a comment box) and **a marketplace listing** (a listing is a card; the page it links to is #1).

---

## 2. What each page must show

### 1. Way — built
Identity → **evidence above the description** → what it does → the decomposition → history → composition →
provenance → sideways. Eight sections in the order a sceptical buyer asks.

The two rules in code, not in a style guide:
- **Never a bare percentage.** `<Rate/>` draws a number only with its `n` and its Wilson interval, and
  "not measured" otherwise — never `0%`, never `100%`.
- **The version never hides.** It sits beside the name, because a label alone can lie (5.5% of skill edits move
  no version).

### 2. Ways index — `/ways`
The registry. One row per Way: name+version, the Goal, status, **verified rate with its interval**, steps,
dependents. Sort default: Wilson lower bound, not raw rate — the same reason `BEST_VERIFIED_FLOOR` exists.

**No infinite scroll and no "trending".** A registry sorted by popularity is the GPT Store: 159K active of 3M,
median creator $47/month. Sort by evidence, filter by Goal, and let the count be honest.

### 3. Goal — `/goal/[id]`
Today a Goal's Ways are reachable only through the router. The page that's missing is the **alternatives**
page: this Goal, every Way that claims it, each with its evidence side by side. That comparison *is* the
product — it is what makes a Way replaceable rather than merely published.

### 4. Composition — `/way/[id]/graph`
The page that makes atomicity visible. Not decoration: **each node carries `name@version`**, and the
operations a reader can take from here are *replace this atom*, *see who depends on it*, *zoom into it*.

This is where §1.3 of `docs/KELL.md` shows up in the UI. Today the backend dissolves a sub-procedure into its
parent at expansion, so this page **cannot** be finished honestly until that projection keeps `origin` on each
flattened node. Build the hierarchy first; the page then reads it.

### 5. Claim — `/claim/[id]`
The unit of research. Must show, in this order: the statement; **`varies` (exactly one axis)**; the falsifier;
the comparison (baseline and treatment, both pinned); the metric and its oracle; evidence count; status
(`draft | open | supported | refuted | indeterminate`); and replications.

`indeterminate` gets equal visual weight. Hiding it is how "no effect" gets read as "no problem".

### 6. Run — `/run/[id]`
One execution. What ran (`procedure@version`), the pinned harness, steps with outcomes, cost and tokens, and
the reproduction command. This is the page every paper is missing: **none of eight** benchmark papers disclose
inference cost, so showing it is a differentiator by construction.

### 7. Harness — `/harness/[digest]`
The page that makes two results comparable *before* their numbers are compared. Lists the seven pinned axes,
and above all **which runs used this digest** — that set is what "comparable" means.

### 8. Corpus — `/research`
The claims index, with negative results visible by default. Not a feed: a corpus. Every row links to a claim,
every claim links to runs, every run links to a harness.

### 9. Diff — `/way/[id]/compare/[other]`
Two versions, step by step: what was added, removed, reordered, and **which steps changed their pinned
sub-Way**. That last one is the atomicity payoff — a diff that cannot show an atom swap is a text diff.

---

## 3. Order to build

1. **Ways index** (#2) — nothing links to a page nobody can reach. One endpoint (`/v1/goals/submissions` list) already exists.
2. **Composition** (#4) — but only after the atom-provenance work (`docs/KELL.md` §1.3), or it lies.
3. **Goal** (#3) — the alternatives view; needs no backend change.
4. **Run** (#6) — completes the evidence loop the Way page already points at.
5. **Claim** (#5) → **Corpus** (#8) → **Harness** (#7) — this block is the research venue, in that order.
6. **Diff** (#9) — last; it is a convenience until atom swapping is real.

---

## 4. What is on screen today, and what is honest about it

The Way page renders **"Verified runs: not measured"** and **"Independent replication: none recorded"**.

That is not a bug and it is not a placeholder. The submissions API the page reads does not carry per-version
run counts, so the two options were to invent a number or say so. Saying so is the only one consistent with
the reason the page exists — and it is the same discipline `procedure_graph_api.py` already documents when it
refuses to fabricate an `environment` field.

When `/v1/goals/submissions/{id}` grows run counts (or the page reads `procedure_graph_api.get_solution_view`,
which already computes the Wilson interval from real evidence rows), those two fields go live with no change
to the layout.
