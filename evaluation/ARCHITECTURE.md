# Evaluation suite architecture

## Two harnesses, deliberately not merged into one

**`backend/tests/evaluation/harness/`** (this suite) drives gold-set component evaluation:
"given a known-correct answer and a real production module, how close does the module get?"
It never runs a frontier agent and never spends money. `run_gold_set()` in `gold_runner.py`
takes a gold set and a `run_case(case) -> outcome` callable that invokes the real SUT (e.g.
`app.services.retrieval.HybridRetriever`), and emits one `EvalResult` row per case.

**`experiments/harness/`** (MEASURE lane, pre-existing) drives whole-task, whole-agent
comparison: "given a real task, how does a frontier agent perform with vs. without Stealth?"
It runs real (or scripted-fixture) agents end to end and, for live runs, spends real API budget.
Its `run_task()` in `run_harness.py` is the spec's `run_evaluation(task, baseline, treatment,
fixture, config)` at the whole-agent level.

Both write JSONL with an overlapping-but-not-identical field set (see METRICS.md's "result
schema" section for the reconciled superset). They are kept separate because they answer
different questions at different cost: one is safe to run on every commit, the other is not.
Merging them would either make the fast suite occasionally expensive, or make the expensive
harness pretend to be free — both wrong. `evaluation-results/final-scorecard.md` is where their
outputs are combined into one picture.

`experiments/harness/`'s own rule — "nothing here imports `backend/**`, adapter-string only" —
is preserved. This suite's extensions to it (Phase 6/7: `run_id`/`retries`/`files_touched`
fields, `economics.py`, ablation arm configs D–G) add fields and config, they don't add a
`backend` import.

## Why `backend/tests/evaluation/` and not a top-level `tests/`

The repo's entire test suite runs via `cd backend && python -m pytest tests/ -q`, and
`backend/tests/conftest.py` carries a load-bearing guard (a `pytest_configure` hook that
prevents a real `.env`'s `DATABASE_URL` from leaking into offline test collection — see that
file's docstring for the exact ordering bug it closes). A top-level `tests/` directory would
sit outside that invocation and lose the guard. `backend/tests/evaluation/` is a subpackage of
the existing suite, gets the guard for free, and is picked up by the existing
`python -m pytest tests/ -q` command with no new invocation to document or forget.

Top-level `evaluation/` (this directory: docs) and `evaluation-results/` (data/reports) stay at
the repo root, since neither is pytest-discovered code — they're the human-facing and
machine-readable output layer respectively, and putting them at the root matches spec §39's
required artifact layout.

## Gold-set format

```json
{
  "cases": [
    {
      "id": "unique-case-id",
      ...area-specific fields the matching run_case() function expects...,
      "expected": {...whatever the gold label/answer is for this area...}
    }
  ]
}
```

`gold_runner.load_gold_set()` only enforces `id` uniqueness; each area's `test_gold_<area>_*.py`
owns its own case shape and its own `run_case` that calls the real production module and
compares its output to `case["expected"]` using the matching `metrics.py` functions.

## Test tiers (unchanged convention, just extended)

This repo separates tiers by filename suffix + skip guard, not pytest markers — no
`pytest.ini`/`[tool.pytest.ini_options]` markers config exists, so this suite doesn't introduce
a parallel scheme:

- `test_*_offline.py` — pure/mocked, always runs, no DB/network/LLM. Most of
  `backend/tests/evaluation/{retrieval,applicability,learning,generalization,evidence}/` are
  offline: they call real production modules but against in-memory/fixture data, not a live DB.
- `test_*_e2e.py` — gated on `DATABASE_URL` via the same
  `pytestmark = pytest.mark.skipif(not DATABASE_URL, ...)` idiom used throughout
  `backend/tests/`. `concurrency/` is e2e (needs a real asyncpg pool, following
  `test_load_e2e.py`'s pattern).
- `experiments/harness/` runs (live-model baseline-vs-Stealth, ablation, economics execution)
  are a third, explicitly separate tier: never invoked by `pytest tests/`, always a deliberate
  `python experiments/harness/run_*.py` invocation, per spec §32.

## Chaitanya convergence point

When Chaitanya's external-corpus admission work approves a canonical procedure `P`, the bridge
into this suite is: add one gold case per relevant `gold_<area>/` set (a `gold_retrieval` query
whose relevant procedure is `P`, a `gold_applicability` case using `P`'s real preconditions, an
execution case if `P` is meant to run, and a `backend/tests/evaluation/regression/` case if
admitting `P` surfaces a new failure mode). No discovery/fetch/extraction machinery is
duplicated here — this suite only ever asks "given knowledge already in Stealth, does the
system use it correctly," never "should this external technique enter Stealth."

## Extension points added to `experiments/harness/` (Phase 6/7)

- `run_harness.py`'s per-arm result dict gains `run_id` (present already as `task_id` but not a
  distinct run identifier across resumed sweeps), `retries`, and `files_touched` — additive
  fields, no change to existing consumers (`scoreboard.py`, `mcnemar_power.py`) since they
  index by the fields they already use.
- `economics.py` (new): a cost model over `openrouter_arms.py`'s `SpendLog` rows (LLM cost) plus
  estimated ingestion/embedding/extraction/generalization/storage/execution-overhead cost, and
  an ROI/break-even calculator taking a workload shape as input. Pure functions over recorded
  cost data — computing ROI from a spend log doesn't require spending anything itself.
- Ablation arm configs D (retrieval+applicability+reuse), E (+decomposition), F
  (+implementation selection), G (full stack) defined alongside existing A/B/C in
  `scripted_arms.py`'s config, per spec §25's "smallest sensible matrix" — config only, no
  live runs this pass.
