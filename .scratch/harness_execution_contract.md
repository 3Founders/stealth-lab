# Harness Execution Contract

Written 2026-09-01, against `main` @ `a34393b`. Describes exactly what an
external agent harness (Claude Code, Cursor, Codex, or any other
MCP-speaking client) needs to know to use Stealth Lab's capability layer
— and, just as importantly, what it never needs to know.

## What a harness NEVER needs to know (directive Sec 48)

- Postgres table names, column names, or join structure.
- UUID formats/generation (`uuid7()` is purely internal).
- The `implementations`/`implementation_tasks` schema this wave added.
- How `visibility_predicate()`/`scope_predicates()` filter rows.
- Which specific provider class (`FrontierProvider`, `DeterministicProvider`,
  …) executes a given kind internally.

A harness only ever sees domain objects (procedure, task, implementation
descriptor) and REST/MCP responses shaped by this repo's own Pydantic
models — never a raw row.

## The canonical flow (directive Sec 77)

```
USER GOAL
   │
   ▼
GET /v1/solutions/search?q=...        (existing, this session's earlier wave)
   │  -- OR --
POST /v1/search/recommend             (existing, find_best_way-style read)
   │
   ▼
procedure or task result
   │
   ▼
for each task in the result (directly, or via the procedure's own tasks):
    GET /v1/tasks/{id}/implementations         (this wave)
    -- OR --
    POST /v1/tasks/{id}/resolve-implementation (this wave)
   │
   ▼
execution descriptor (see below)
   │
   ├── harness-native execution (Mode A) ──► harness invokes it via its
   │                                          own tool infrastructure
   │
   └── Stealth-mediated execution (Mode B) ─► POST a report back once
                                               done (existing report_execution-
                                               style MCP flow; this wave
                                               does not add a new mediated-
                                               execute REST endpoint — see
                                               "Not built this wave" below)
   │
   ▼
report_execution / evidence recorded
```

The harness never constructs a database id by hand — every id it holds
came from a prior response.

## The execution descriptor (directive Sec 47) — the capability ABI

Shape returned by `GET /v1/implementations/{id}` and
`POST /v1/tasks/{id}/resolve-implementation` (both this wave):

```json
{
  "implementation_id": "...",
  "kind": "tool",
  "provider": "graphify",
  "status": "active",
  "verification_status": "unverified",
  "locator": {"protocol": "mcp", "server": "...", "tool": "query_graph"},
  "invocation": {"protocol": "mcp", "operation": "call_tool", "tool": "query_graph"},
  "input_schema": {...},
  "output_schema": {...},
  "requirements": {"network": true, "credentials": ["graphify"]},
  "reason": "resolved via hint preference order"
}
```

`auth_requirements` never appears with a resolved secret — only a
`credential_ref` shape a harness's own credential store resolves. This
repo's registry never stores a plaintext credential at all (enforced by
convention at the write boundary — `implementation_registry.register()`'s
own docstring names this explicitly, not a DB constraint, since JSONB
shape isn't policed at the schema layer, same posture `evidence.
success_criteria` already has).

## Two execution modes (directive Sec 46, 78-79)

**Mode A — harness-native.** Stealth names an implementation; the
harness calls it through infrastructure it already has (its own MCP
client, its own credentials). Stealth never touches the actual
invocation. This is the mode this wave's REST surface is built for —
`GET`/`POST resolve-implementation` hand back a descriptor, nothing
executes server-side.

**Mode B — Stealth-mediated.** The harness asks Stealth to run something
directly. This wave's `app/execution/implementation_executor.py` is the
real internal mechanism this WOULD sit behind (`execute_implementation`),
but **no REST/MCP endpoint exposing it publicly was built this wave** —
see "Not built this wave" below. Internally, `graph_executor.py`'s own
`run_node` closures are Mode-B-shaped already (they execute in-process);
this wave's job was only to make the resolution/identity side durable,
not to add a new public "execute arbitrary implementation on my behalf"
surface, which is a real security-review-worthy decision deliberately
left for its own gated phase.

## Not built this wave — named, not hidden

- A public `POST /v1/implementations/{id}/execute` (Mode B, harness-
  triggered) endpoint. `implementation_executor.py`'s `execute_
  implementation` is real and tested, but exposing it over REST/MCP to
  an arbitrary caller is a distinct security decision (arbitrary code
  execution surface, directive Sec 70's own explicit warning) that
  deserves its own review, not a default inclusion in this wave.
- Real `MCPProvider`/`GraphifyProvider`/`MonidProvider`/`ComposioProvider`
  adapters — same reasoning `providers.py` gave the previous wave: this
  environment cannot verify one end-to-end without real external
  credentials, and the directive's own rule ("only actual implementations
  should be marked runnable") argues against building one unverifiable.
- Credential resolution from an authenticated actor/session
  (directive Sec 49-50) — `authn.py`'s real actor identity exists, but
  no code in this wave resolves a provider credential FROM that identity;
  `auth_requirements.credential_ref` is a real, storable pointer with no
  real resolver behind it yet.
