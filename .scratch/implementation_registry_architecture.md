# Implementation Registry Architecture

Written 2026-09-01, against `main` @ `a34393b`.

## The six objects, kept distinct (directive Sec 0)

| Object | Answers | Real module |
|---|---|---|
| Task | WHAT needs to be done | `task_nodes` (existing) |
| Procedure | WHAT sequence of tasks is a reusable way to accomplish a goal | `procedures` (existing) |
| **Implementation** | HOW can this task actually be executed | `implementations` (**new, migration 33**) |
| **Provider** | WHERE / through whom can this implementation be invoked | `app/execution/providers.py` (previous wave) |
| Execution | WHAT happened when we actually ran it | `executions` (existing) |
| Evidence | WHY should we trust the result | `evidence` (existing, `target_type='implementation'` now populated) |
| Capability | HOW reliably does this implementation perform this task under this context | derived from evidence, see `.scratch/implementation_migration_plan.md` |

## Why a table, not a JSONB blob on `procedures` or `task_nodes`

An implementation must be **globally addressable independent of any one
procedure step that happens to reference it** (directive Sec 4): the
same `Graphify.query_graph` implementation can be linked to many
different tasks (`implementation_tasks`), and a task's linked
implementations must be listable, filterable, and lifecycle-managed on
their own — none of that is possible if implementation data lives only
as an unindexed JSONB fragment inside `procedures.steps` or
`task_nodes.io_schema`.

## Schema shape (`db/33_implementation_registry.sql`)

```
implementations
├── id (uuid pk)
├── name, provider, version         -- unique triple = identity
├── kind                            -- TEXT+CHECK, mirrors implementations.py's IMPLEMENTATION_KINDS + wasm/computer_use/api
├── status                          -- candidate/active/deprecated/disabled/quarantined (mutable in place)
├── verification_status             -- unverified/verified (separate axis, directive Sec 61)
├── content_hash                    -- sha256 or equivalent, nullable
├── locator, invocation             -- JSONB, protocol-specific
├── input_schema, output_schema     -- JSONB
├── requirements, auth_requirements, resource_requirements   -- JSONB; auth_requirements NEVER a secret, only a credential_ref
├── source_ref, author, license     -- first-class columns, NULL = honestly unknown
├── derived_from                    -- self-FK, forking lineage
├── deprecated_at, disabled_at
├── created_by, visibility, owner_id, scope_type, scope_entity_id
└── t_created

implementation_tasks
├── id (uuid pk)
├── implementation_id (fk -> implementations)
├── task_node_id (fk -> task_nodes)
├── created_by, t_created
└── UNIQUE(implementation_id, task_node_id)
```

No `implementation_versions` table (a new version is a new row, same
invalidate-and-append shape `procedures`/`claims` already use). No
`implementation_capability_stats` table (derived from `evidence`, same
pattern `procedure_evidence_stats` already establishes). No
`implementation_credentials` table (credentials are never stored here at
all, by rule — a real credential vault, if built, is separate, out-of-scope
infrastructure).

## Identity vs. mutable lifecycle (directive Sec 4/19)

A row's **identity** — `name`/`provider`/`version`/`locator`/
`invocation`/`input_schema`/`output_schema` — never changes after
creation. Its **lifecycle** — `status`/`verification_status`/
`deprecated_at`/`disabled_at` — is a real, ordinary `UPDATE` in place.
This is deliberately NOT bi-temporal invalidate-and-append the way
`claims`/`procedures` are: those objects need point-in-time historical
truth-maintenance ("what did we believe on commit X"); an implementation
registry entry does not need to re-litigate its own past status the same
way — a frozen `executions.implementation_id` reference stays resolvable
forever simply because the row is never deleted, regardless of how many
times its `status` column has since changed.

## Resolution vs. execution vs. routing — three separate concerns, on purpose

```
implementation_registry.resolve(task_node_id, hint_kinds)
    -> WHICH durable implementation identity, if any, is linked +
       active for this task, honoring a kind preference. No cost/
       capability weighing here.

implementation_executor.execute_implementation(node, context)
    -> given an ALREADY-RESOLVED-OR-NONE node, dispatch to the real
       provider (providers.py) and return a NodeResult. Falls back to
       today's real, unchanged frontier-default behavior when nothing
       durable was ever bound -- backward compatibility is load-bearing.

(future) router
    -> WHICH of several resolved candidates to prefer, weighing cost/
       capability/latency. Not built this wave -- directive Sec 38's own
       routing flow needs real capability data (this wave's capabilities.py)
       to exist and accumulate evidence FIRST.
```

Keeping these three separate mirrors this codebase's own existing
discipline of keeping RRF fusion and applicability's non-compensatory
cascade apart (CLAUDE.md's hard rule) — resolution, execution, and
routing are three different kinds of decision and conflating them into
one function would make each harder to reason about and test.

## What changed vs. what stayed frozen

Frozen, byte-for-byte, this entire wave: `app/execution/implementations.py`
(the KIND vocabulary + `resolve_implementation`), `app/execution/
providers.py` (`ImplementationProvider`/`FrontierProvider`/
`DeterministicProvider`), `app/execution/graph_executor.py`'s core loop,
`PlanNode`/`Execution`'s existing field shapes. Nothing about how the
system executes a plan TODAY changed by this migration landing — the new
table is real and queryable, but until `implementation_executor.py`'s
resolve/bind step is wired into a real compile-time call site (a
deliberately separate, smaller, later change — see `.scratch/
implementation_migration_plan.md`), every existing procedure continues
to execute exactly as it did before this wave.
