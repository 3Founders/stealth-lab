# Testing verified procedural memory on SWE-bench Pro — a plan, not a result yet

`README.md` describes the pilot (flat retrieval context, one repo, "shakes
out harness bugs... not a result"). `GRAPH_EXPERIMENT.md` describes the
three-arm graph-memory experiment that runs against the real backend.
Neither tests the machinery built in the most recent sessions: the
`procedures` table, ticket 13's lifecycle (promotion/circuit-breaker/
quarantine), `applicability.py`'s hard-constraint filter, or
`procedure_extraction`. This plan is for testing *that* — and for being
honest about what this corpus can and can't answer about it before a
single instance runs.

## 0. Read this first: a real, structural finding this plan is built around

`procedure_extraction.extract_procedure()` calls `capture_procedure()`
fresh every time it runs — confirmed by reading the code directly, not
assumed. No `family_id` lookup, no merge into an existing similar
procedure. **Every extracted episode becomes a brand-new `candidate` row
starting at 0 attempts.**

Ticket 13's verification bar (`>=10 successes, 0 failures, >=3 distinct
contexts`) only advances through real execution outcomes recorded via
`record_execution_outcome()` — a procedure has to be *selected* for a
later task, *used*, and *succeed*, repeatedly. `applicability.py` defaults
to `require_verified=True` for automatic selection; a `candidate`
procedure is invisible to it.

**SWE-bench Pro issues don't repeat.** Each of the 96 ansible instances is
a different bug or feature. The odds that any single extracted procedure
gets matched and successfully reused 10 times across 3 distinct contexts,
inside a corpus this size, are close to zero under normal task diversity.
This is not a bug to fix before testing — it's a structural mismatch
between SWE-bench Pro's task shape and what the verified pathway is
designed to reward (repeated, similar company workflows), and it's the
same wall Experiment 4 already hit (which is why that experiment used
deliberately-repeating synthetic tasks instead).

**Consequence for this plan: two separable questions, not one, reported
separately.** Conflating them would make an ambiguous result
uninterpretable.

## 1. The two questions

### Q1 — Does retrieval from prior episodes help, unverified?

Run with `require_verified=False`. Tests `applicability.py`'s hard-filter
(preconditions/scope/temporal validity/staleness/availability) plus
retrieval, without the statistical-verification value-add. This is the
question closest to what Experiment 1 already measured (76.7% vs 31.8%
vector-vs-lexical precision@1) — but through the real `procedures` /
`applicability` path this time, not raw knowledge-graph retrieval.

### Q2 — Does the full ticket-13 pathway help?

Needs genuine task repetition to be answerable at all. Two ways to get
real repetition inside 96 ansible instances:

- **Cluster by the taxonomy already ingested.** `graph_ingest.py` reads
  `issue_categories`/`issue_specificity` from the raw dataset (via
  `load_dataset()`) and stores them in `task_nodes.io_schema` and
  `knowledge_nodes.properties`, but `subset.json` (the frozen split
  manifest) does not carry them directly — pulling the real distribution
  needs a fresh query against the full dataset, not yet done. If enough
  instances cluster into real task families (e.g. several "deprecated
  API removal" or "add a new module option" issues), deliberately
  building the eval set to include multiple same-family instances gives
  a real shot at crossing the verification threshold naturally.
- **Or treat a null/near-zero result as the real finding.** A cold-start
  result on a 96-instance, non-repeating corpus, honestly reported with
  this explanation, is genuine and useful information about where the
  verification threshold's assumptions break down — not a failure to
  hide.

Recommendation: attempt the clustering check first (cheap — one query
against already-ingested data); if it doesn't yield a usable number of
same-family clusters, run Q2 anyway and report the cold-start result
honestly rather than skip the question.

## 2. What "good" looks like — calibrated against real current numbers, not hope

Checked directly (web search, Aug 2026), not assumed from training data:

- SWE-bench Pro vendor-scaffold numbers cluster near saturation for
  frontier models (~77-80%, Claude Mythos 5 / Fable 5).
- Scale's **standardized** harness — the only apples-to-apples comparison
  — tops out at **~59-61.5%** (Muse Spark 1.1, GPT-5.4 xHigh). The
  10-30 point gap between vendor-tuned and standardized scaffolds shows
  scaffold/tool-use quality is a huge, largely independent lever from
  raw model capability.
- **Open-weight models** (what this project actually runs — Groq/General
  Compute serving DeepSeek/Qwen/GLM/Kimi) score meaningfully lower still:
  best open-weights entry on the standardized harness is **38.7%**
  (Qwen3-Coder-480B).
- The original SWE-bench Pro paper found real models score *worse* on
  the private/held-out set than the public set (Opus 4.1: 22.7% → 17.8%;
  GPT-5: 23.1% → 14.9% when moving from public to private). This
  project's corpus is the public ansible set — any number here is an
  optimistic upper bound on true generalization, not a generalization
  claim.

**"Good" is NOT "beat the SWE-bench Pro leaderboard."** Unrealistic given
model/compute choices, and the wrong frame for what this system actually
is — a memory/scaffold layer, not a bigger model.

**"Good" is a real, measured, statistically-distinguishable delta**
(memory-on vs memory-off, same model, same scaffold, same step budget) on
resolve rate *or* cost/steps-to-completion — reported honestly even if the
direction is negative, the same way Experiment 4's "currently costs more"
finding was kept rather than buried.

**A null result, correctly explained by the cold-start finding above, is
also a legitimate outcome of this plan**, not a failed experiment.

## 3. Corpus and split

Reuse `profile.json`/`subset.json` as-is — the repo-selection reasoning in
`README.md` (image size, no network at test time, plain pytest, deepest
instance count) doesn't need redoing. 96 ansible instances, `corpus` = 76
(strictly earlier by `base_commit`), `eval` = 20 (latest).

**Open question, not yet decided: eval-set size.** The pilot's 20 was
explicitly "not a result" — real statistical power on a binary
resolve/fail outcome at n=20 is weak (a handful of flips changes the
headline number by 5+ points). Decide a real target n (or an explicit
power calculation) before running anything, not after seeing early
numbers.

## 4. The real pipeline needed — does not exist yet, end to end

The old `graph_ingest.py` path (static knowledge-graph preload,
`task_nodes`/`knowledge_nodes` only) cannot answer either Q1 or Q2 — it
never creates `procedures` rows, and predates `project_id`/migration 17
entirely. What's actually needed:

1. **Real agent runs** (HTN, both arms) against the memory-corpus
   instances → real `trace_events`/`observations`, via the now-verified
   hook path (Chaitanya's session confirmed 33 real events land and
   ingest cleanly).
2. **`procedure_extraction` run against those episodes** → real
   `procedures` rows, `candidate`/0-attempts to start.
3. **For Q2 specifically**: enough of those procedures actually get
   *reused* (matched against later same-family eval/corpus instances,
   via real `record_execution_outcome()` calls) to reach `verified`
   before the eval-set runs that are supposed to benefit from them.
4. **Eval-set runs**, memory-on vs memory-off, same model, same scaffold,
   same step budget — the actual controlled comparison, for both Q1
   (`require_verified=False`) and Q2 (`require_verified=True`).

None of steps 1-3 have been run against this corpus with the current
`procedures`/`applicability`/`procedure_extraction` stack. Step 1 is also
gap 4/hook-wiring-adjacent — needs a real LLM call, same credential
question as everywhere else this has come up.

## 5. Metrics

- **Primary**: resolve rate (pass@1), using the dataset's own
  `fail_to_pass`/`pass_to_pass` test lists — same check the existing
  harness already runs, no new grading logic needed.
- **Secondary, given Experiment 4's finding that memory currently costs
  more**: real token cost and steps-to-completion, not just accuracy.
  Memory could plausibly help on one axis and hurt on another; that's a
  precise, reportable result, not an ambiguous one, as long as both are
  measured.

## 6. Threats to validity to actively control for

- **Temporal leakage** — already handled by the frozen date-ordered
  split; keep it, don't relax it for convenience.
- **Public-vs-held-out generalization gap** — real, documented above.
  Report public-set numbers as an upper bound, state this explicitly in
  any writeup, don't imply a generalization claim the corpus can't
  support.
- **Q1/Q2 conflation** — report separately, always, even if one of them
  turns out null.
- **Model/provider variance** — pin one model for the whole comparison.
  A silent provider fallback mid-run (rate limits, outages) would
  confound the exact thing being measured; the harness needs to fail
  loudly on a provider swap, not degrade silently.
- **Extraction quality itself untested at scale** — `procedure_extraction`
  has real unit/e2e coverage (per its own commit) but has never run
  against a real, large, diverse episode set like 76 ansible instances.
  Worth treating the FIRST extraction pass as its own checkpoint (spot-
  check a sample of extracted procedures for sanity) before trusting
  anything built on top of it.

## 7. Concrete next actions, in order

1. Query the full SWE-bench Pro dataset (via `graph_ingest.py`'s own
   `load_dataset()`) for the real `issue_categories`/`issue_specificity`
   distribution across the 96 ansible instances — cheap, answers whether
   Q2's clustering approach is even feasible with this corpus, before
   committing to it.
2. Decide the real eval-set size (statistical power target), separate
   from the pilot's placeholder 20.
3. Decide the model to pin for the whole comparison, and confirm real
   credentials/network access exist for it (gap 4's own blocker, still
   open).
4. Build the real pipeline (section 4) as a runnable script, mirroring
   `run_graph_experiment.py`'s existing structure rather than inventing a
   new harness shape.
5. Run Q1 first (cheaper, no reuse-accumulation step needed) as a real
   checkpoint before committing to Q2's larger pipeline.
