# StealthLab V1 evaluation suite

This directory plus `backend/tests/evaluation/` and `evaluation-results/` together answer one
question with reproducible evidence: **is StealthLab V1 correct, safe, reliable, useful,
generalizable, and economically worthwhile?** Frozen baseline: commit `a5dace6`, tag
`v1-baseline-2026-09-02`. See `ARCHITECTURE.md` for how the pieces fit together and why they
live where they do, and `METRICS.md` for exact metric definitions.

This is a **test system**, not a verdict. A green suite here means the invariants and gold
benchmarks it checks hold on this baseline — it is not, by itself, a claim that StealthLab's
product performance is strong. `evaluation-results/final-scorecard.md` keeps those two claims
explicitly separate.

## What is tested, and what isn't rebuilt

`backend/tests/` (pre-existing, ~208 files) already carries rigorous, behavioral coverage of
most subsystems: event ingestion, event→episode construction, historical Claude/ChatGPT
evidence classification, procedure extraction, candidate maturation/trust, multi-episode
generalization, retrieval fusion mechanics, applicability, invariants/state, staleness
propagation, provenance, failure-learning, execution graph, implementation replay, procedure
composition, security/privacy, and cross-user isolation. This suite does not duplicate any of
that. What it adds:

1. A **gold-labeled metrics layer** (`backend/tests/evaluation/{retrieval,applicability,
   learning,generalization,evidence}/`) computing the spec's named metrics (Recall@k, MRR,
   nDCG, precision/recall, false-accept/false-reject, generalization/transfer rates, ...)
   against the real production modules, over curated gold cases with known-correct answers.
2. A short list of **specific behavioral gaps** the pre-existing suite didn't cover: retrieval
   trust-safety (a locally-close unverified candidate must not outrank a verified applicable
   procedure), a true claim-change→selection-change chain, a true end-to-end provenance chain,
   ingestion ordering/adversarial-payload robustness, and a ChatGPT edited/regenerated-branch
   evidence case.
3. **Security additions**: prompt-injection-in-imported-content and SQL/input-injection
   adversarial cases (`backend/tests/evaluation/security/`), genuinely missing from the existing
   suite.
4. **Concurrency/chaos at small scale** (`backend/tests/evaluation/concurrency/`): simultaneous
   publishers, duplicate job processing, claim-update-during-retrieval, resumed-execution-twice,
   DB/LLM/embedding timeout injection.
5. **Economics/ROI machinery** (`backend/tests/evaluation/economics/`): a cost model and
   ROI/break-even calculator. Built as extension points; not executed against live spend this
   pass (see Known limitations below).
6. A **v1 baseline run + final scorecard** (`evaluation-results/`) so results are
   machine-readable and reproducible against the frozen baseline.

`experiments/harness/` (built by the MEASURE lane) already provides the baseline-vs-Stealth
three-arm design (A=frontier agent from scratch, B=+RAG, C=+Stealth), a `run_task()`-shaped
entrypoint, JSONL results with cost/token/latency fields, and rigorous paired statistics. This
suite extends that harness's schema and defines ablation arm configs rather than building a
second runner — see `ARCHITECTURE.md`.

## How to run

All commands assume `cd backend` first (this repo's existing convention — `python`, never
`python3`; a Windows Store stub answers to `python3`).

```bash
# Fast, deterministic — no DB, no network, no LLM calls (includes this suite's harness
# self-tests and every gold-set test whose SUT calls don't need a live DB):
python -m pytest tests/ -q
python -m pytest tests/evaluation/ -q

# Full integration — needs a real (disposable) Postgres via DATABASE_URL, exactly like the
# existing test_*_e2e.py files:
DATABASE_URL=postgresql://... python -m pytest tests/ -q

# This suite's security adversarial cases only:
python -m pytest tests/evaluation/security/ -q

# This suite's concurrency/chaos cases only (needs DATABASE_URL, small scale):
DATABASE_URL=postgresql://... python -m pytest tests/evaluation/concurrency/ -q

# Regenerate the v1-baseline evaluation-results artifact:
python scripts/export_evaluation_baseline.py   # see ARCHITECTURE.md
```

```powershell
# experiments/harness/ (separate lane, own venv invocation, offline/synthetic by default;
# live-model runs cost real OpenRouter spend and are NOT part of this pass -- see
# experiments/harness/README.md and evaluation-results/final-scorecard.md's deferred items):
backend\.venv\Scripts\python.exe -m pytest experiments/harness -q
```

There is intentionally no single command that runs the deterministic suite and a live-LLM
evaluation together — spec section 32 requires that a plain `pytest` invocation never require
frontier-model calls. Expensive evaluation (baseline-vs-Stealth, ablation, economics execution,
capacity-at-scale) is deliberately a separate, explicit, user-triggered step.

## How metrics are calculated

See `METRICS.md` for exact definitions. Every metric is implemented once, in
`backend/tests/evaluation/harness/metrics.py`, and every gold-set test calls that
implementation rather than recomputing it inline.

## How to reproduce the baseline

`evaluation-results/v1-baseline/manifest.json` records the exact commit, tag, environment,
model/embedding configuration, and corpus state a given `results.jsonl` was produced against.
To regenerate: check out `v1-baseline-2026-09-02`, run the fast + full commands above, and
re-run the export step. Any drift between a fresh run and the committed `results.jsonl` for the
same commit is itself a finding — the harness is deterministic wherever its underlying gold
data and code are.

## How to add a new evaluation case

1. Pick the right gold set under `backend/tests/evaluation/fixtures/gold_<area>/` (or add a new
   `gold_<area>/` if it's a genuinely new subsystem).
2. Add a case object to that gold set's JSON file, following the existing cases' shape (each
   case needs a unique `id` and whatever fields that area's `run_case` function expects — see
   the matching `test_gold_<area>_offline.py`).
3. If it's a regression for a bug found during evaluation, add it to
   `backend/tests/evaluation/regression/` instead, following the `TEST.md`-style convention:
   name the failure the case prevents, not just the behavior it checks.
4. When Chaitanya's external-corpus work admits a canonical procedure, register it the same
   way — see `ARCHITECTURE.md`'s "Chaitanya convergence point" section. This is the only
   required bridge between the two projects; this suite does not depend on or duplicate his
   discovery/fetch/extraction machinery.

## Known limitations

- Baseline-vs-Stealth (spec §24), ablation (§25), and economics/ROI (§26) machinery is built
  but **not executed** this pass — no live-LLM runs, no real spend. Triggering them is a
  separate, explicit step with its own budget/scope decision.
- Load/capacity testing (spec §30) covers correctness at small/modest scale (100s–1000s of
  records), not capacity-at-scale (1k→1M procedures).
- Execution-graph retry/resume (spec §16) was found genuinely absent in
  `backend/app/execution/graph_executor.py` at the code level, not just untested. Per "no new
  V1 features," this is documented as a discovered production gap, not implemented here — see
  `evaluation-results/final-scorecard.md`.
