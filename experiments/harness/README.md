# §40 evaluation harness (Lane MEASURE)

Three-arm paired comparison per spec v4 §40 (`verified_procedural_experience_system_
ideal_specification_v4.md` L1378-1406):

```
A. Frontier agent from scratch
B. Frontier agent + conventional memory (RAG baseline)
C. Frontier agent + verified procedural experience (via the MCP surface)
```

Headline scoreboard metrics: **pass-rate / cost / false-reuse / stale-refusal**,
plus a power-analysis footer that prints discordant-pair counts beside every
p-value — never a bare point estimate. Full §40 telemetry (unseen-task success,
transfer, tool calls, tokens, latency, human interventions, capability inputs)
is written to the JSONL rows so later metrics never require a re-run.

## Status: skeleton + MICRO-EXPERIMENT PACK (still synthetic outcomes)

Board MEASURE items 1-2 landed the skeleton and scoreboard. Item 3 (founder
mandate 2026-08-25) adds the **micro-experiment pack**: `fixtures/micro/` holds
11 tiny real-life scenarios across the four mandated archetypes — adversarial
refund-policy rule violations, dependency-conflict debug, PDF-to-sheet pipeline
steps, env-drift staleness — each with arm-independent success criteria and
evidence-trail requirements.

```powershell
backend\.venv\Scripts\python.exe experiments\harness\run_micro_pack.py
```

prints a per-scenario verdict table (pass/fail per arm WITH the failing
requirement ids — never a bare estimate), then the usual scoreboard + power
footer. Grading lives in `micro_pack.py`; evidence assertions check the episode
record AND the MCP surface call journal (`StubSurface` logs every
search/get_procedure/check_applicability/record_refusal). Trail requirements
waive for arms with no substrate path: arm B lacking a trail is the
experimental contrast, not a failure. One scenario deliberately poisons the
gate so arm C trips it — the honest-negative slot.

## Real-model arms (MEASURE-WAVE, live OpenRouter)

Board item MEASURE-WAVE 0 (founder go 2026-08-26): the same three arms, with
`scripted_arms` decision logic replaced by live chat calls through
`openrouter_arms.py`. The AgentAdapter contract, scoring, scoreboard,
micro-pack grading and evidence journal are UNCHANGED — episodes from a real
sweep drop straight into the existing pipeline.

```powershell
# offline sanity first: validates fixtures + prints each arm's prompt, no network:
backend\.venv\Scripts\python.exe experiments\harness\run_real_arms.py --dry-run

# real sweep (key: env OPENROUTER_API_KEY, else backend/.env; never printed):
backend\.venv\Scripts\python.exe experiments\harness\run_real_arms.py
```

Behavior pinned by `tests/test_openrouter_arms.py`:

- **429 backoff** — exponential ceiling (`BACKOFF_BASE_S` doubling to
  `BACKOFF_CAP_S`) with FULL jitter per attempt; network errors retry like
  429s. The upstream shared pool saturates — this is the expected path, not
  an error path.
- **Fallback chain** — primary `ox-alpha`, then documented cheap alternates
  (`DEFAULT_MODEL_CHAIN`; override with `--models`). Non-retryable 4xx skips
  a model immediately; exhaustion raises with the full per-attempt trail.
- **Resumable** — an existing results file refuses to run without
  `--auto-resume` (paid history is never clobbered); resume skips tasks
  already holding valid all-arm rows and RETRIES error rows and
  unparseable-decision rows. `--max-tasks` caps fresh spend per invocation.
- **Spend log** — one JSONL row PER ATTEMPT (successes carry usage/cost,
  failures carry their status) beside the results file; totals print with
  the scoreboard, so a saturated-pool run shows its attempt profile.
- **Decision contract** — the model replies strict-JSON
  `{resolved, reuse[], refuse[], notes}` (one repair round-trip before the
  episode is marked invalid). Arm A gets the situation only; arm B the same
  rag blob as scripted; arm C the same surface dance with procedure cards.
  Reuse is credited ONLY after a fresh gate verdict — model proposes, gate
  disposes; refusals are recorded on the surface either way. Agents never
  read fixture ground truth (`stale`, `rag=misleading`, `solo_outcome`);
  attribution is mechanical (leaned-on-memory/reuse + failed), so grading
  truth stays in scoring/micro_pack where it belongs.

## Extraction error floor (Band 3 prep)

`fixtures/error_floor/` holds 42 hand-gold trace excerpts (agreed-correct
observations written by a human, each with a notes defense; ambiguous cases
excluded by authorship rule) and `_rubric.md`, which IS the grading contract:
typed canonical-key equality after documented normalization (path separators,
whitespace collapse), token-Jaccard >= 0.5 for free-text semantic labels,
one-to-one greedy matching in gold order, reason codes on every FP/FN.

Every future extractor change prints its precision/recall against that floor:

```powershell
# real extractor (backend on PYTHONPATH in the CALLER's env — this harness
# never imports backend/**, lane rule):
backend\.venv\Scripts\python.exe experiments\harness\run_error_floor.py \
    --adapter app.services.observations:extract_deterministic_observations
# offline demo baseline (mirror of deterministic_v1's rules incl. quirks):
backend\.venv\Scripts\python.exe experiments\harness\run_error_floor.py \
    --adapter demo_extractor:deterministic_v1_demo
```

prints every discrepancy with its reason code, then the scoreboard section
(also available standalone: `scoreboard.format_error_floor()`, or appended to
any §40 run via `scoreboard.main(..., "--error-floor-results <detail.json>")`).
Exit code 1 iff an adapter errored on any excerpt — a partial run never
masquerades as a floor. Landing baseline (demo mirror, 2026-08-26):
P 22/25 (0.880) · R 22/30 (0.733) · F1 0.800; the five deliberate v1 quirks
(compound/flagged git commit, pip install pytest-cov substring, NotebookEdit
whitelist, no semantic layer) are pinned by test in
`tests/test_error_floor_end_to_end.py`.

## First real corpus (Claude Code sessions)

```powershell
backend\.venv\Scripts\python.exe experiments\harness\session_corpus.py
backend\.venv\Scripts\python.exe experiments\harness\run_micro_pack.py --corpus-manifest corpus\cc_manifest.jsonl
```

Ingests every transcript under `~/.claude/projects/**.jsonl` (subagent sibling
files included) into a LOCATOR-ONLY manifest — session id, line number, char
length, timestamp; never message text (episode_assembly privacy discipline).
Manifest rows flow through all three arms as unscored dry-run pipeline
exercises; they are the P4 dogfooding seed. Manifests are gitignored
(machine-local paths); regenerate locally.

## Status notes carried from the skeleton phase

Until CORE-A's persistence is wired to a live surface there is no real
ExecutionPlan integration, so everything runs on synthetic fixtures and the
scripted agent policy (`scripted_arms.py`). The seams are real:

- `mcp_surface.McpSurface` is the protocol arm C talks through
  (`search`/`check_applicability`/`get_procedure`) — swap the offline stub for
  the real MCP client without touching scoring or the scoreboard.
- `scripted_arms.AgentAdapter.run()` is the arm contract — real frontier-agent
  adapters implement the same method.

Nothing here imports `backend/**` (lane rule). Run offline:

```powershell
backend\.venv\Scripts\python.exe experiments\harness\run_harness.py
backend\.venv\Scripts\python.exe -m pytest experiments/harness -q
```

Output is a resumable JSONL (append-only, completed task+arm sets skipped on
re-run, transient error rows retried — same discipline as
`experiments/swebench_pro/run_graph_experiment.py`, which this adapts).

## Metric semantics pinned by spec

- **false reuse** — a reuse attempt itself causing the failure (§36 cause list;
  spec v4 L1259: "`false_reuse` marks a reuse attempt itself causing the
  failure"). Success despite a rocky reuse is NOT false reuse.
- **stale refusal** — refusing a procedure whose assumptions no longer hold,
  graded against fixture ground truth (§40 strongest result: "correctly
  refusing procedures whose assumptions no longer hold"; §39 invariant 11).
- **paired statistics** — an instance contributes only if EVERY arm produced a
  valid episode; exact binomial McNemar on discordant pairs only; zero
  discordant pairs reports "no input", never p=1.0.
