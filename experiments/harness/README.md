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

## Status: SKELETON, synthetic fixtures only

Until CORE-A lands 1.7 there is no ExecutionPlan persistence to integrate with,
so tonight everything runs on synthetic fixtures and a scripted agent policy
(`scripted_arms.py`). The seams are real:

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
