# StealthLab

**North star:** *agents that earn the right to remember.* Most agent memory
records what happened. StealthLab distills what an agent *believes*, tracks
whether that belief still holds, and refuses to let an agent reuse a
procedure whose preconditions have quietly changed — with a cited reason,
not a shrug.

This file describes two things on purpose, clearly separated, because they
are not the same thing yet:

1. **What you can run today** — a working MCP server and CLI, described below.
2. **What the product is being built into** — the "earned memory" v0.1 slice
   defined in [`demo.md`](demo.md), whose full technical positioning lives in
   [`commLLM.md`](commLLM.md). The refusal mechanism in that document is real
   and independently verified against a live model (see
   `.scratch/research/model-decides-verification.md`), but it isn't reachable
   through the server below yet — `demo.md` names this gap explicitly.

If you only read one doc, read `demo.md` — it's the shorter, current
statement of what ships and what proves it.

## What runs today

A local-first MCP server exposing **9 tools** over a bi-temporal
knowledge/task graph: ingest execution traces, detect a bottleneck, run a
multi-model debate to propose a fix, evaluate it statistically, and apply it
only after a human approves — every state change auditable and reversible
via the graph's supersede-not-delete history.

| Tool | What it does |
|---|---|
| `retrieve_precedent` | Find prior solved patterns relevant to a query |
| `detect_conflict_trigger` | Find a real conflict between knowledge nodes, open a debate |
| `propose_synthesis` | Run a real multi-round model debate on a trigger, produce a scorecard |
| `submit_approval` | Approve/reject a scorecard — applies + audits + closes the debate atomically |
| `decompose_task` | Turn an unstructured problem into a structured change proposal |
| `decide_decomposition` | Approve or reject a decomposition proposal |
| `apply_change_set` | Apply a change set directly, no approval gate (ungated — use narrowly) |
| `solve_task` | Retrieval-grounded coding agent against a real repo on disk |
| `check_procedure` | Applicability check → `ALLOW` or structured `WOULD_REFUSE` with a cited reason (audit mode only) |

Full setup, the stdio vs. hosted-HTTP split, and the known v1 limitations
(no job queue, `apply_change_set` is an ungated write, `repo_path` is
caller-controlled) are documented in
[`backend/README_MCP_SERVER.md`](backend/README_MCP_SERVER.md) — that's the
accurate, current setup doc; follow it over anything below.

**Quick install**, via the packaging layer
([`packaging/README.md`](packaging/README.md)), which imports the same
backend code above and adds zero business logic of its own:

```bash
pip install -e packaging/
stealthlab-mcp-server            # Streamable HTTP on loopback, or --stdio
stealthlab-trace-hook            # Claude Code hook: redact + collect one trace event
stealthlab-status-page           # read-only: episodes -> claims -> procedures, capability scores
stealthlab-public-board          # static scoreboard page from a real-arms sweep + spend ledger
```

Needs a Postgres 15+ instance with the `pgvector` extension (Supabase
works) — see `backend/README.md` for the exact image/connection-string
gotchas (stock `postgres:15` cannot run the migration chain; use
`pgvector/pgvector:pg15`).

Tests: `cd backend && python -m pytest tests -q` — offline, no DB or API
keys required for the bulk of the suite.

### Ingesting collected traces

`stealthlab-trace-hook` above only writes collector files
(`.claude/traces/*.jsonl`) — something still has to load them into Postgres.
That's `backend/scripts/run_ingestion.py`:

```bash
cd backend
python scripts/run_ingestion.py --once            # single pass, then exit
python scripts/run_ingestion.py --interval 30      # loop every 30s
```

It's free (no model calls) and gets each trace event as far as `trace_events`
and `observations`. It does not go further yet — episode assembly is
bypassed entirely and the break is at observation → claim, so a fresh
install's own prior work won't show up via `retrieve_precedent` from this
script alone (see `demo.md`'s C2 entry-point note and §3 reuse-demonstration
checklist item for the engine-verified measurement).

## What it's becoming

`demo.md` defines the minimal shippable v0.1 slice: ingest an agent's traces
→ distill evidence-backed procedures → surface them on similar future tasks
→ **refuse stale reuse with cited reasons**, in audit mode (the agent is
informed, never blocked, until Band 3). Every capability in that document
ships with the exact command that proves it — nothing is claimed without a
passing test on the release commit. `check_procedure` (refuse stale reuse
with a cited reason, audit mode only) is now part of what runs today, not
what's becoming — see the tool table above. `commLLM.md` carries the full
positioning: the research base, the competitive landscape, and the tool
surface this is still expanding into (`explain_decision`,
`explain_failure`).

The engine behind that story is real and extensively tested — evidence
tracking, capability scoring, precondition/applicability gating, failure
classification and routing all exist and are proven offline and, where
claimed, against a live model. What's still in progress is wiring that
engine into the MCP tool surface a coding agent actually calls; `demo.md`
tracks that gap in its ship checklist rather than hiding it.

## Security & data

[`SECURITY.md`](SECURITY.md) — threat model, stated plainly: a bearer token
gates *who* reaches the server, not *what* they can do once in.
[`DATA_STATEMENT.md`](DATA_STATEMENT.md) — local-first, redact-before-persist,
no phone-home telemetry in v0.1, never train on user traces without consent.

## Where things are, if you're exploring the repo

- `demo.md` — the current, short definition of what ships (start here).
- `commLLM.md` — full technical positioning, research base, competitive landscape.
- `ROADMAP.md` — the band-by-band work plan behind both of the above.
- `backend/` — the FastAPI app, the graph, the debate/eval/governance
  services, and the MCP server. `backend/README_MCP_SERVER.md` is the
  accurate setup doc for the server as it exists today.
- `packaging/` — the installable CLI wrapper (`stealthlab-connect`) described above.
- `experiments/harness/` — the evaluation harness comparing a solo agent,
  ordinary RAG, and the verified substrate on real model calls; this is
  where the earned-memory refusal claim in `demo.md` is currently proven.
- `.scratch/build-board.md` — the running multi-lane build log, if you want
  the detailed history of how any of the above got here.
