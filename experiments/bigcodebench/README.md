# BigCodeBench demo: Kel knowledge + model routing vs a frontier model

This is a small, local, isolated test of the whole loop:

1. benchmark tasks become Goals;
2. models attempt them;
3. verified solutions become Procedures;
4. outcomes feed the recommender;
5. new held-out tasks arrive through the real `find_ways` tool;
6. the router picks a model ladder.

It is **not** a publishable result. See the caveats at the end.

## Isolation

- Every script imports `demo_env` first. `demo_env` strips the production environment:
  - `SEARCH_DATABASE_URL`, `CONTROL_DATABASE_URL`, and the `K0xx` shard DSNs;
  - any DSN that isn't the local demo DB.
- It then pins `DATABASE_URL` to the local Postgres (`127.0.0.1:55432/kel_bcb_demo`), and `verify_after_import()` re-checks this after the app has loaded.
- Model-generated code runs in `evaluate.py` under these limits:
  - a subprocess in a temp dir;
  - no secrets in its environment;
  - a timeout;
  - an import screen (`safety.py`: no os/sys/subprocess/socket/requests/…).
- Only tasks whose libraries are all on the safe list run: 294 of 1140.
- `data/` and `runs/` are gitignored.

## Pipeline (in order)

| step | script | what it does |
|---|---|---|
| sample | `select_sample.py` | 40 fit + 20 held-out safe tasks (tasks whose own reference fails here are dropped) |
| import | `import_fit.py` | `app.benchmarks` importer: the fit tasks become Goals under domain Goals, with frozen benchmarks (visible ≈1/3 of tests; the full suite is the gold grade). **Held-out tasks are not imported.** |
| attempt | `run_models.py raw`, `sonnet_io.py` | General Compute models (gemma-4-31B-it, gpt-oss-120b, deepseek-v3.2) + Sonnet as a fresh, context-free subagent |
| learn | `learn.py extract/knowledge/evidence/observe/refit` | verified solutions → Procedures (`code_solution_v1@1`, method-level steps, no code stored) → validation runs → observations → recommender refit (NUTS) |
| retrieve | `retrieve.py` | each held-out task goes to the in-process `find_ways` MCP tool, following the v1 planner policy |
| evaluate | `run_models.py kel`, `evaluate_heldout.py` | four setups, reliability targets 0.5–0.9, `allow_retries=False` (temperature 0 is deterministic) |

## Results (20 held-out tasks)

| setup | solved | cost (USD) | vs frontier |
|---|---|---|---|
| 1. frontier raw (Sonnet) | 18/20 | 0.02569 | — |
| 2. best small raw (gpt-oss-120b) | 13/20 | 0.00716 | −72%, but 5 fewer solved |
| 3. small + Kel knowledge | 13/20 | 0.0072–0.0077 | — |
| 4. full Kel, target 0.5, baseline retrieval | 18/20 | 0.01754 | **−32%** |
| 4. full Kel, target 0.5, + ways from more specific Goals | 18/20 | 0.01587 | **−38%** |
| 4. full Kel, target 0.8–0.9 | 18/20 | ≈0.0236–0.0242 | ≈ −6 to −8% (mostly Sonnet first) |

- **Retrieval coverage:** 1/20 held-out tasks received knowledge with baseline retrieval. With the downward lookup plus candidate ways, 4/20 did.
- **Effect of knowledge:** on those 4 tasks DeepSeek went from 14 to 15 solved. gpt-oss did not change.
- **Wrong answers:** every Kel setup delivered 1 wrong answer, because the runtime check passed but the gold suite failed. The frontier delivered none.

## Findings

- **The extractor gap was the first blocker.** The grounded extractor turned code answers into "call write_code / run_tests". `CodeSolutionExtractor` fixes this: it only fires on verified solutions, cites only APIs that appear in the code, and stores no code.
- **The partial→ambiguous gate hid knowledge.** When a match was only partial, `find_ways` answered "ambiguous" and never offered any ways. Now candidates carry judged ways, and a Goal with no Procedure of its own offers judged ways from its more specific Goals, labelled with the Goal each was observed on.
- **Library size limits transfer.** BigCodeBench tasks are deliberately distinct, and the judge (correctly) rejects most sibling Procedures. Most of the savings come from routing; knowledge adds only a few points here.
- **Retries have to match determinism.** Repeating a temperature-0 attempt wastes money, hence `allow_retries=False`.
- **Pre-existing bug (not fixed):** when no LLM client exists, the deterministic extractor is tagged with the LLM extractor's name.

## Caveats

- Only 20 held-out tasks: the numbers are noisy.
- Open-model prices in `prices.json` are **placeholders**.
- Sonnet token counts are estimated.
- Sonnet ran as a Claude Code subagent, not through the API.
- Only the safe-library subset of tasks ran.
- The fit-set validation used each task's own Procedure (in-sample).
