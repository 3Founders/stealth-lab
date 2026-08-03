# Workflow Debate Platform

A governance layer for changes to an operational knowledge graph.

Most systems that hold knowledge treat a write as a storage operation. Here a
write is a proposed change that has to survive an argument, a review, and a human
before it lands — and once it lands, the version it replaced is still there and
still queryable.

## Why it's built this way

The bet is that you get further by making bad states impossible to represent than
by trying to detect them afterwards.

So far that shows up in exactly one place. Anything generated from untrusted
public input can only create new nodes and connect them to each other. It can't
modify, delete, or attach to anything that already exists, because those
operations aren't in the vocabulary it's allowed to emit. A fully hijacked model
produces a junk subgraph sitting in a review queue, not a corrupted workflow. We
checked this by writing a model that deliberately tries to escalate, and
confirming the refusal fires both at generation time and again at apply time — in
case a stored proposal was tampered with in between.

The other half is that nothing gets destroyed. Every table is bi-temporal: rows
carry both when they were true and when we learned it. An update closes the old
row's validity window and appends a new one, and edges pointing at the old
version get rewired forward so the new one doesn't arrive orphaned. "What did
this workflow look like in March, and what changed it" is a query rather than an
archaeology project.

## The loop

1. Execution traces are ingested. Threshold rules flag bottlenecks.
2. Three models from three labs debate a fix. Each proposes a structured
   `ChangeSet`, not prose — prose can't be applied to a graph, and if something
   had to interpret it at write time, the approver would be signing off on text
   while something else got written.
3. A fourth model, from a family none of the panel uses, reviews the arguments
   for fallacies. Every citation is resolved against the database. A model
   claiming a citation exists is not the same as the citation existing, and only
   the database can settle that.
4. Surviving candidates are replayed against historical executions. Welch's
   t-test rather than Student's, because a change can affect variance as well as
   the mean. Benjamini-Hochberg across metrics. Results are labelled simulation
   rather than measurement, because that's what they are.
5. A human sees the transcript, the objections, and the exact operations that
   will run. They approve or reject.
6. Approved changes close the old row and append a new one. The approval itself
   is written into the graph.

V2 adds a public surface: describe a problem in plain language, get a proposed
task decomposition back. That's the first place untrusted text reaches a model,
which is why the capability boundary above exists.

## Where this is going

Almost none of this is built. It's written down so the direction is somewhere
other than in our heads, not because any of it works today.

**Tasks should compose.** Right now a task is a flat node — there's no way to say
one task is made of several smaller ones, because the edge vocabulary has no
part-of relation. That's the biggest single gap. With it, a task is either a leaf
that actually does something or a composite that declares an interface and
expands into a subgraph. Without it nothing can be built more than one layer
deep, and improving one step of a workflow means superseding the entire task.

**Implementations should compete rather than overwrite.** A task node says what
has to be true when the step is done. What satisfies that should be open — a
shell command, a regex, a lookup table, a small fine-tuned model, a frontier
model, a person. When several exist, the cheapest one that clears the bar gets
used and the others stay available. That turns "do we even need an agent here"
from an architecture argument into a measurement, and we suspect the honest
answer for a lot of tasks is no.

Those two together are what make improvement local. If someone writes a better
version of one leaf, everything that composes it gets better without anyone
rewriting a workflow or superseding a node, and rolling back is a routing change
instead of a migration. Today an improvement has to be a supersession, which
means it's coarse, it drags every dependent through a rewiring, and there's no
way for a consumer to stay on the old version if the new one doesn't suit them.

**More things should be unrepresentable.** Containment is the one instance that
exists. Two more belong there: a decomposition whose steps consume outputs
nothing produces shouldn't be proposable — that check is currently delegated to a
critique model when it could be a deterministic function — and once obligations
attach to data classes, a workflow that touches personal data with no consent
step shouldn't compose at all. Failing to compile beats getting flagged in
review.

**Eventually this should be shared.** A commons of procedures, where a task
carries its own eval, anyone can publish a competing implementation, and whoever
wins gets routed and paid on use. That needs real identity, which doesn't exist —
right now the viewer is an unverified header — and it needs everything above
first.

## Run it

Postgres 15+ with pgvector. Schema files are idempotent and run in order.

```bash
createdb workflow_db
for f in db/0*.sql; do psql -d workflow_db -f "$f"; done
```

```bash
cd backend_v2/backend_v2
pip install -r requirements.txt
cp .env.example .env               # DATABASE_URL at minimum
uvicorn app.main:app --reload
python scripts/bootstrap_demo.py   # seeds a workflow + traces at an 80% error rate
```

```bash
cd frontend_v2/frontend_v2
npm install && cp .env.local.example .env.local && npm run dev
```

Tests: `python -m pytest tests/ -q`. No database, no API keys needed.

The debate wants four keys — `ANTHROPIC_API_KEY`, `FIREWORKS_API_KEY`,
`OPENAI_API_KEY`, and `GOOGLE_API_KEY` for the judge, which is deliberately from
a family none of the panel uses. `VOYAGE_API_KEY` for embeddings.
`USE_LOCAL_MODELS=true` runs the whole loop against Ollama for free. Spend caps
default to $10/day globally and $1/day per identity.

## API

| Route | Purpose |
|---|---|
| `POST /v1/traces` | ingest traces; bad rows rejected per-record, batch survives |
| `POST /v1/admin/scan` | detect bottlenecks, run the loop |
| `GET /v1/approvals/pending` | scorecards awaiting a decision |
| `GET /v1/approvals/{id}` | case file: reasoning, change set, transcript |
| `POST /v1/approvals/{id}` | approve or reject |
| `GET /v1/graph/{id}?depth=N` | subgraph for rendering |
| `POST /v1/chat` | grounded Q&A, citations resolved against the graph |
| `POST /v1/decompose` | problem in, proposed decomposition out |
| `POST /v1/decompose/{id}/decide` | approve (applies) or reject |

Frontend: `/workbench`, `/approvals`, `/approvals/[id]`, `/archive`.

Deploying: set Root Directory to the inner path — `backend_v2/backend_v2` and
`frontend_v2/frontend_v2`. Not ready for a public URL, see below.

## What's actually been checked

148 offline tests pass. Seven scripts run the same code against a real,
disposable Postgres, because several of the bugs we hit only show up against a
real engine. Two worth naming: every JSONB write was pre-serialising in Python
and casting in SQL, which silently corrupted how the connection decoded JSON on
every later read once a custom type codec was registered; and the rate limiter
allowed 10 requests against a limit of 3 under concurrent load, because a
transaction alone doesn't stop two connections reading the same stale count.

Live checks cover access control (11, including a public node reachable only
through a private edge, which stays hidden because the predicate runs inside the
recursive CTE rather than filtering the result), rate limiting and spend (15),
decomposition (9, including two escalation attempts being refused), and the
inherited V1 set — bi-temporal supersession with edge rewiring, the state
machine, trigger detection, ingestion, Layer 1, and approval end to end.

**None of this has run against a real paid model.** Every LLM call so far has
used a mock or a fake OpenAI-compatible server. That proves the plumbing —
responses get parsed and checked correctly — and proves nothing about whether a
frontier model reliably returns output in the shape this expects. It's the most
likely thing to break first, and it's also the cheapest unknown left to close.

## Limits

Blocking a public deploy:

- No authentication. Identity is an unverified `X-Viewer-Id` header, safe only
  because nothing is private yet. Startup refuses to boot if private visibility
  is enabled without real auth.
- No job queue. Debates run inside the request handler, and Layer 2 can be up to
  40 serial model calls per candidate — past most proxy timeouts.
- `/v1/decompose` has spend caps but no rate limit.
- Anonymous rate-limit keys use the socket IP, which collapses to one bucket
  behind a proxy.

Known gaps in the graph itself:

- Knowledge nodes can be created but never superseded. There's no
  `UpdateKnowledgeNodeOp`, so a policy that changes or a fact that turns out to
  be wrong has no approved path to being corrected. Only task nodes can be
  updated.
- Superseding a task rewires every dependent edge forward without checking that
  the new version's `io_schema` is still compatible with what those dependents
  expected. That's a latent bug, not a design choice.
- `io_schema` and `success_criteria` exist on every task node and are never
  validated against anything.

Untested at scale: `traverse_from` has never been load-tested, and a hub node
makes a depth-2 traversal explode.

Not built: the public bounty surface, identity and payments, Prover-Estimator,
and Layer 2's stronger evidence tiers. Layer 2 itself is fully implemented but
isn't reachable from the API.

Full accounting in `V2_STATUS.md`.
