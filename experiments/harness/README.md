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
