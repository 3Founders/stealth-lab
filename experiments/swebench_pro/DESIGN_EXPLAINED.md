# The experiment, explained plainly

This is the walkthrough version. `README.md` next to it is the terse
reference; this one explains *why* each piece is shaped the way it is, in
ordinary words.

---

## 1. The question

Your system stores what a codebase has already learned about itself — past
issues, what broke, where the fix landed. The obvious question is: **does
carrying that memory around actually help?**

"Help" splits into two very different things, and they can move in opposite
directions:

- **Accuracy** — does the agent fix more bugs?
- **Cost** — does the agent use fewer tokens getting there?

You can imagine memory helping one and hurting the other. An agent that gets
a useful hint might solve more bugs *and* spend more tokens, because the hint
itself costs tokens to carry. So we measure both, separately, and we do not
let a win on one be reported as a win overall.

---

## 2. What we're testing against

**SWE-bench Pro** is a set of 731 real bugs from real open-source projects.
Each entry gives you:

- the GitHub issue text a human wrote,
- the exact commit the repo was at when the bug existed,
- the real patch a human eventually merged (the "gold patch"),
- the tests that should go from failing to passing (`FAIL_TO_PASS`),
- the tests that were already passing and must stay passing (`PASS_TO_PASS`),
- a prebuilt Docker image with that project's environment already set up.

That last one matters more than it sounds. Getting an old version of a large
Python project to actually run its test suite is normally days of work.
Scale AI published a working container per bug, so we can skip all of it.

**Why not SWE-bench Atlas.** Atlas is the other candidate. It doesn't ship
containers — it ships a list of shell commands you're supposed to use to
*build* the environment yourself (`go mod tidy`, install protoc, and so on),
per repository, with no published proof that the gold patches even pass in
whatever you end up building. On one laptop with 36 GB of free disk, Pro runs
today and Atlas is a project of its own. If you specifically want the Atlas
number later, that's a separate build, not a flag.

---

## 3. Why we only use one project: ansible

All 11 projects in Pro were profiled first (`profile_dataset.py`). The
winner wasn't close:

| | ansible | why it matters |
|---|---|---|
| bugs available | **96** | the most of any repo — so there's a deep history to remember |
| container size | **0.54 GB** | the smallest — disk is the hard limit here |
| test runner | plain pytest | no exotic toolchain |
| network at test time | **never** | see below |

That last row decided it. Some projects (NodeBB) run `npm install` as part
of running their tests, which means the test container needs internet. Your
`repo_execution.py` has an invariant it takes seriously: **nothing that
executes an untrusted patch gets network access.** Rather than quietly
weaken that, we picked a repo where we don't have to. The grading container
runs with `--network none` and the invariant holds.

One practical discovery: the containers for two different ansible bugs share
**zero** disk layers, so each costs a full ~1 GB. So the runner pulls one,
uses it, and deletes it before moving on. Peak disk stays around 3 GB
instead of 20 GB.

---

## 4. The two arms

We run the same agent on the same bug, twice.

**Arm A — "no memory."** The agent gets the issue text and nothing else. It
has to find the relevant code itself, by searching and reading files.

**Arm B — "memory."** Identical in every way, except the very first message
also contains a short block of retrieved context: *here are five earlier
issues in this repo that look like yours, and here are the files their fixes
touched.*

Same model (`deepseek-v3.1`), same five tools, same 25-step limit, same
temperature (0, so it's as reproducible as the API allows). **The only
difference is that block.** That's the whole point — if anything else
differed, a change in tokens couldn't be attributed to memory.

The agent's tools are deliberately boring: `list_dir`, `search`,
`read_file`, `edit_file`, `finish`.

One design note: the agent edits by exact find-and-replace, and *we* generate
the diff from before/after content. We don't ask the model to write a valid
patch file by hand. That conflates two unrelated skills — understanding the
bug, and counting context lines in a diff — and open-weight models fail the
second one constantly. A patch that fails to apply looks identical to a
wrong answer in the results, which would push both arms toward zero and
leave nothing to compare.

---

## 5. What "memory" actually contains

For each earlier bug in the repo, we store:

- the issue title,
- the issue text,
- **which files the fix changed**,
- **which functions and classes inside them changed**.

And we deliberately store one thing nowhere: **the actual patch text.**

This is the most important line in the design. If memory contained old
diffs, then for a bug similar to an old one, retrieval could hand the agent
a nearly-working fix. The accuracy number would then be measuring "can we
look up a near-duplicate", not "does experience with this codebase
transfer." Storing only *where the work landed* keeps the agent doing the
thinking and keeps memory doing the pointing.

Retrieval combines two signals, the same way your `HybridRetriever` does:

- **meaning** — an embedding of the issue text (voyage-3-large), which
  catches "this is about the same kind of problem" even in different words;
- **exact words** — a keyword match, which catches an issue that names
  `GalaxyCLI` or a specific module directly, where embeddings are weak.

The two lists are merged by **Reciprocal Rank Fusion** with the same
constant (60) your code uses. RRF only looks at *position* in each list, not
raw scores — which matters because a similarity score and a keyword score
are on completely different scales, and adding them together requires
inventing a conversion factor that silently changes behaviour over time.

*(Honest caveat: this is a port of your retriever's logic, not a call into
it. `HybridRetriever` needs a live Postgres with pgvector and the full
schema; this pilot runs standalone. The fusion maths is identical, the
storage isn't. So this is evidence about the idea, not a test of that
deployment.)*

---

## 6. The four ways this could cheat, and what stops each

This is the part that decides whether any number here is worth anything.

**Cheat 1: memory could contain the future.**
If the agent is fixing a bug from March 2024 and its memory contains fixes
from 2025, that's not memory — it's leakage.

*Stopped by:* we dated all 96 bugs by their real commit dates from a clone of
ansible, sorted them, and used the **20 most recent as the test set** and the
**76 older ones as memory**. Then, for each individual bug, memory is rebuilt
with a cutoff at *that bug's own date*. Nothing an agent sees was written
after the bug it's being asked to fix. This split is frozen in `subset.json`
before any model ran.

**Cheat 2: the agent could read the tests.**
The `FAIL_TO_PASS` tests are new tests added alongside the fix. If the agent
could read them, it would be reading the answer key.

*Stopped by:* the copy of the repo the agent explores is checked out at the
buggy commit and **stops there**. The step that adds the new test files
(`before_repo_set_cmd`) is run only inside the grading container, after the
agent is done. The agent never sees those files.

**Cheat 3: the agent could edit the tests to make them pass.**
*Stopped by:* the grading container applies the agent's patch **first**, and
*then* checks out the real test files over the top. Any edit the agent made
to a test file is overwritten. This ordering is copied exactly from Scale's
own harness — it's easy to get backwards and load-bearing.

**Cheat 4: a broken bug could fake a result.**
Some benchmark entries are just broken — the environment drifted, a
dependency vanished. Both arms would fail those, dragging accuracy down for
reasons unrelated to memory.

*Stopped by:* before either arm runs, we apply the **gold patch** and check
it resolves. If it doesn't, the instance is dropped and recorded as dropped.
Costs 14 seconds. We also verified the reverse on a test instance — an
*empty* patch correctly fails — because "gold passes" alone would also be
true of a harness that marks everything as passing.

---

## 7. How grading works

No human judges anything. No LLM judges anything either.

The container runs the project's real test suite, Scale's own per-instance
parser turns the output into a list of `{test name, PASSED/FAILED}`, and the
verdict is one line:

> **resolved** = every `FAIL_TO_PASS` test passes **and** no `PASS_TO_PASS`
> test broke.

A test that doesn't appear in the output at all counts as *not passed*. It's
tempting to ignore missing tests, but absence isn't evidence the fix worked —
treating it as a pass is the one bug that would manufacture fake successes.

We also keep "the patch didn't apply" separate from "the patch was wrong."
They're different facts, and lumping them together would punish whichever arm
happens to produce messier diffs.

We use Scale's parsers rather than writing our own because the parsers
genuinely differ per instance — 8 distinct versions across 40 ansible bugs
alone. Grading a public benchmark with a homegrown scorer and then reporting
the number as that benchmark's score is how results stop being comparable to
anyone else's.

---

## 8. What we measure

**Accuracy:** how many of the 20 bugs each arm resolved.

**Tokens:** every prompt token and completion token, summed across every API
call in an episode, taken from the API's own usage field — not estimated.

**Steps:** how many tool calls the agent made. This is the mechanism. If
memory works by pointing at the right code, it should show up here first, as
fewer searches.

---

## 9. Why the statistics are "paired"

Both arms solve the **same 20 bugs**. So each bug acts as its own control —
how hard it is, how big the files are, how well ansible happens to suit this
model, all of that cancels out within the pair.

- **Tokens and steps** → Wilcoxon signed-rank test. Token counts are wildly
  skewed: an episode where the agent flails costs ten times a clean one. A
  test based on averages would just track whoever drew the worst outlier.
- **Accuracy** → exact McNemar test, which only looks at bugs where the two
  arms *disagreed*. Those are the only ones carrying information about a
  difference.
- All three are then corrected together with Benjamini-Hochberg, reusing
  your `app/eval/statistics.py`.

We deliberately do **not** use `welch_comparison` from that same file, even
though it's sitting right there. Welch assumes two independent groups; these
are the same 20 bugs measured twice. Using it would misstate the evidence in
both directions.

**About power, stated in advance rather than after seeing the answer.** With
20 bugs, the accuracy test needs roughly 6 or more disagreements leaning the
same way to reach significance. It probably won't get them. So an accuracy
result of "not significant" here means **"this run couldn't tell"** — which
is a different statement from "there is no difference," and the output says
which one it is. Tokens are a paired continuous measurement and are far
better powered at this size. Tokens are the thing this pilot can actually
speak to.

---

## 10. One number we already know, before any agent ran

We checked offline whether memory even *could* help — comparing the files
each test bug actually changed against the files named by the top retrieved
prior bugs. No model involved, so it was nearly free:

| | |
|---|---|
| files any earlier bug ever touched ("ceiling") | **53.2%** |
| test bugs where that ceiling is above zero | **15 / 20** |
| what retrieval actually finds at top-5 | **36.8%** (11/20 bugs) |
| at top-20 | 50.2% — essentially the ceiling |

Read that carefully, because it bounds everything else:

- On **5 of 20 bugs, no earlier issue ever touched the relevant files.**
  Memory cannot possibly help there. Not a retrieval failure — the knowledge
  isn't in the corpus.
- Of what *is* reachable, top-5 retrieval finds about **69%** of it. So the
  retriever is doing a reasonable job; the corpus is the limit, not the
  ranking.

---

## 11. What this experiment can and cannot tell you

**Can:** whether, on this repo with this model, carrying repo memory changes
token cost in a measurable and consistent direction — and whether the change
tracks the instances where retrieval actually found the right files.

**Cannot:**
- Generalize across repos. This is one project.
- Give a leaderboard-comparable score. `deepseek-v3.1` on a 25-step
  homemade scaffold is not a frontier agent. Only the **paired difference**
  between the two arms means anything; the absolute resolve rate does not.
- Settle accuracy. See the power note above.

**A known structural tension, flagged before results.** The memory block sits
in the conversation prefix, so it is re-sent on *every* API call in the
episode. To win on tokens, it has to save more exploration than that repeated
cost. Early instances show it saving 1–2 steps but still costing more tokens
overall. If that holds, the honest headline is a token *increase*, and the
analysis reports a decomposition — the fixed prefix tax versus the variable
step saving — so the result is actionable rather than just a minus sign.

That is also why the treatment was **not** tuned after seeing early results.
Adjusting the memory format until the number turns positive is exactly the
failure `KNOWLEDGE_UPDATION_EXPERIMENT.md` warns about. This is scoped as
the pilot that document calls for — *"shakes out harness bugs, rough effect
size. Not a result."* It has already earned that: it found three real bugs
in the harness (an unretried API error truncating one arm's token count, an
agent burning its whole budget without ever editing, and a key collision
silently discarding retrieval metadata).

---

## 12. How to read the output

`python analyze.py --results results.jsonl` prints, in order:

1. **which instances were dropped and why** — read this first; if many were
   dropped the rest is on thin ice;
2. a per-bug table: resolved-or-not and token count for each arm;
3. accuracy with the McNemar p-value;
4. token totals, the per-instance percentage change with a bootstrap
   confidence interval, and the Wilcoxon p-value;
5. the **decomposition** — prefix tax vs. steps saved;
6. the **mechanism check** — token change split by whether retrieval actually
   found a real file. This is the honest test of the story. If memory helps
   by pointing at the right code, the saving should sit with the bugs where
   retrieval hit. If both groups move together, something other than the
   claimed mechanism is doing the work.
7. the power note.
