# Lane prompts — paste the matching block into each worktree's agent terminal

Each prompt is self-contained. The agent reads the board, claims its queue head, and
works under OVERNIGHT MODE (self-merge after green suite; stop-and-log on ambiguity).

---

## Terminal 1 → `C:\Users\user\sl-core-a` (Lane CORE-A)

You are Lane CORE-A in this repo. Read `.scratch/build-board.md` fully — your lane,
queue, owned paths, shared rules, and OVERNIGHT MODE. Also read `ROADMAP.md` sections
"Start here" + Band 1, and Appendix C rows #1, #2, #17. Your task: claim Lane CORE-A
item ① (Band 1.7 — persist ExecutionPlan/TaskGraph [D→frozen] tables, bind executions
to exact plan versions) on the board, then implement it with its proving tests in the
same change, new migration file with the next number (you exclusively own backend/db).
Fresh-start compliant: no backfills, no legacy shims. Extend existing patterns; read
`backend/app/services/execution.py`, `db/20_procedure_extraction.sql`, and
`backend/tests/test_band1_contracts.py` first so your DDL and tests match house style.
Run `backend\.venv\Scripts\python.exe -m pytest tests -q` before every commit and paste
the count into the commit message. Commit prefix `core-a:`. When green: rebase onto
origin/main, push your branch AND main per OVERNIGHT MODE, mark `[x] done` on the
board. If genuinely ambiguous, leave a numbered blocking question in the board Log and
move to queue item ②.

## Terminal 2 → `C:\Users\user\sl-core-b` (Lane CORE-B)

You are Lane CORE-B. Read `.scratch/build-board.md` fully — lane, owned paths, shared
rules, OVERNIGHT MODE. Your queue: ① precondition relevance filter in procedure
derivation (derive gates only load-bearing facts, not every live claim), ② V6
authoring-time invariant validator + move z3 solving off the event loop with a solver
timeout, ③ memoize `project_state()` inside the applicability cascade + tenant-scope
the cold-start gate. You own ONLY `backend/app/services/procedure_extraction/**`,
`invariants.py`, `applicability.py`, `precondition_gate.py`, `state.py` (+ their test
files). NO new migrations, no schema changes. Read `procedure_extraction/derive.py`,
`validators.py`, `applicability.py`, `invariants.py`, `state.py` first. Add proving
tests beside the code (offline, no DB). Run `backend\.venv\Scripts\python.exe -m
pytest tests -q` before every commit, paste counts. Prefix `core-b:`. Green → rebase,
push branch + main per OVERNIGHT MODE, mark board done. Ambiguity → numbered Log
question, continue to next item.

## Terminal 3 → `C:\Users\user\sl-measure` (Lane MEASURE)

You are Lane MEASURE. Read `.scratch/build-board.md` fully. Your queue: build the §40
evaluation harness skeleton under a NEW directory `experiments/harness/` — adapt the
three-arm pattern from `experiments/swebench_pro/run_graph_experiment.py`: arm A
(frontier agent solo), arm B (+conventional memory/RAG baseline), arm C (+verified
procedural substrate via the MCP surface). Synthetic fixtures only tonight; nothing
may import or modify `backend/**` at write time — read-only study is fine. Include a
scoreboard module that reports pass-rate/cost/false-reuse/stale-refusal with a
power-analysis footer (discordant-pair counts printed beside every p-value; never bare
point estimates). Write harness-local tests for scoring/statistics logic and run them.
Prefix `measure:`. Green suite (`backend\.venv\Scripts\python.exe -m pytest
experiments/harness -q` plus backend suite untouched) → rebase, push branch + main per
OVERNIGHT MODE, mark board done. Leave design decisions as short comments citing spec
§40 rather than asking overnight.

## Terminal 4 → `C:\Users\user\sl-research` (Lane RESEARCH)

You are Lane RESEARCH. Read `.scratch/build-board.md` fully. Tooling:
`python research_exa.py "<query>" [-n N] [--domain arxiv.org]` at repo root (Exa key
already configured in `backend/.env`). Protocol: market/vendor/pain-point evidence via
Exa web search; technical credibility checks via arXiv / Semantic Scholar / OpenAlex
(webfetch); every claim in your output cites a URL. Your queue: ① execute the four
open verification tickets in `RESEARCH_INTEGRATION_PLAN.md` (P-M3 leaderboard
movement, P-B1 GATS/WorldEvolver/EnvACE numbers, P-C1 FedWorld mechanics, P-I1
Molt/ToolVerse/MobileRL maturity), ② competitive sweep Mem0/Letta/Zep-Graphiti/
HippoRAG/AWM — what they ship vs our trust spine (bi-temporal, typed propositions,
evidence independence groups, computed capability, non-compensatory applicability) —
deltas as board notes, ③ τ-Knowledge ceiling re-check (arXiv:2603.04370). Write one
synthesized report per ticket into `.scratch/research/<ticket>.md`. No code changes;
prefix `research:`; push branch + main per OVERNIGHT MODE when each report lands.
