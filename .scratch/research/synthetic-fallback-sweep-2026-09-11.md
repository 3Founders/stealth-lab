# Synthetic-fallback grep sweep — 2026-09-11

Deferred item from CLAUDE.md hard rule #1 ("no synthetic fallbacks... fail
closed instead"), redone from scratch (the exact grep/file-list from the
original pre-compaction pass was lost). Scope: `backend/app/` only (the
importable package), excluding `tests/`, `experiments/`, `plat_v1/`,
vendored code.

## Method

Three sweeps:
1. `except <SomeError>:` immediately followed by `return (None|[]|{}|0|""|'')`
   or bare `pass` — the core "swallow an error, hand back a fabricated-looking
   default" shape.
2. `x or "placeholder-ish-string"` patterns (`"unknown"`, `"unnamed"`,
   `"none"`, `"N/A"`, `"TODO"`).
3. `except ImportError:` blocks that might silently substitute a different
   code path without telling the caller.

39 files matched sweep #1's broad pattern; each was read in context (not just
grep-matched) and judged against the rule's actual intent: does this hand a
FABRICATED value to a caller who will treat it as real data, or is it an
honest "I don't know" / "nothing found" / "this optional dependency isn't
installed" signal with a correctly `Optional`-shaped return?

## Real violations found: 1

### `app/stealth/generator.py:274-285` — `_fetch_file_intents` swallows ANY Postgres error into `{}`

```python
async def _fetch_file_intents(pool: asyncpg.Pool, run_id: str) -> dict[int, dict]:
    """Live (non-expired) file-intent declarations for the run's nodes
    (migration 56 columns on `execution_run_nodes`). Empty when none."""
    try:
        rows = await pool.fetch(
            "SELECT node_order, owner_agent_id, read_exact, read_globs, write_exact, "
            "       write_globs, symbols_expected_to_modify, file_intent_lease_expires_at "
            "FROM execution_run_nodes WHERE execution_run_id = $1::uuid "
            "AND (owner_agent_id IS NOT NULL "
            "     OR write_globs <> '[]'::jsonb OR write_exact <> '[]'::jsonb)",
            run_id,
        )
    except asyncpg.PostgresError:
        return {}
```

**Why it's a violation:** the except clause catches the entire
`asyncpg.PostgresError` hierarchy — a genuine syntax error, a permissions
problem, a connection drop, a real bug in the query — not just the narrow
"this migration hasn't landed on this DB yet" case the docstring's "Empty
when none" comment seems to be assuming. Every one of those gets silently
converted into `{}` with **no logging, no distinction, no signal that
anything went wrong**. A caller of this function (feeding the `.stealth/`
`run.md`/coordination projection) cannot tell "no agent has declared a file
intent yet" apart from "the database call failed" — both render as an empty
coordination section, which for a file meant to show multi-agent coordination
state is exactly the kind of silently-fabricated-looking absence this rule
exists to prevent.

Compare to the CORRECT pattern already used two files over in
`app/services/procedure_claim_refs.py` (`list_claim_refs_for_procedure`,
`list_procedures_for_claim`): those catch only the specific expected
exception (`asyncpg.UndefinedTableError`), log a `logger.warning(...)`
naming exactly what's suspected ("migration 66 not applied?"), and the
function's own docstring explicitly documents that `[]` in that one specific
case is not an error. `_fetch_file_intents` has none of the three: no
narrowed exception type, no log line, no docstring caveat.

**Suggested fix:** narrow the except to the specific expected condition
(likely `asyncpg.UndefinedColumnError`/`UndefinedTableError` for
"migration 56 not applied on this DB"), add a `logger.warning(...)` on that
branch naming the suspected cause (mirroring `procedure_claim_refs.py`'s own
wording), and let any other `PostgresError` propagate — a real DB failure
should surface as a real failure to whatever calls the projection generator,
not silently render as "no coordination declared."

**Severity: low-to-medium, narrow blast radius.** `stealth/generator.py`
feeds the `.stealth/` `run.md`/projection machinery
(`app/execution/stealth_projection.py` is its only real caller found this
pass) — this is NOT on the live MCP tool-call path exercised by
`find_best_way`/`search_procedures`/etc. this session; it's part of the
partially-built projection/coordination-view feature. Real, but not
release-blocking for the ingestion+knowledge lane this session's work has
focused on.

## Everything else checked: correctly designed, not violations

The remaining ~38 files matching sweep #1, reviewed in context:

- **`app/local_agent/runner.py`** (4 hits, `except OSError: return None`) —
  reading `.git/HEAD`, `.git/<ref>`, `.git/packed-refs`, `.git/config`
  directly off disk. Every function is typed `Optional[str]`, and the
  module's own docstrings say explicitly: *"absence... is honest and
  handled, never fabricated."* Correct honest-abstention pattern.
- **`app/services/call_graph.py`, `environment_facts.py`, `import_deps.py`**
  (`except OSError: return []`) — best-effort local filesystem probes for
  code-graph/environment-fact/import-dependency signals, each gated by an
  `os.path.isfile()` check first; the except only catches a narrow
  TOCTOU race (file removed/permission change between check and open).
  `[]` here means "found nothing here," the correct signal for an advisory
  best-effort scan, not a fabricated result standing in for real data.
- **`app/services/procedure_claim_refs.py`** (2 hits,
  `except asyncpg.UndefinedTableError: logger.warning(...); return []`) —
  this is the CORRECT version of the pattern `stealth/generator.py` should
  be copying: narrow exception type, logged, explicitly documented in the
  docstring as an intentional backward-compat degrade for a DB missing
  migration 66, not a silent failure.
- **`app/services/temporal_conflict.py`** (`except ValueError: return None`)
  — a date-range parser typed `Optional[tuple[...]]`; `None` means
  "unparseable," which is exactly what the caller's own docstring
  (`compute_overlap`) says to expect.
- **`app/services/invariants.py`** (`_z3_available()`,
  `except ImportError: return False`) — a capability probe ("is the z3
  SMT library installed"), correctly feeds into this module's documented
  `undecidable` bucket rather than being silently treated as "constraint
  satisfied."
- **`app/api/implementations.py`** (`except ImportError:` →
  `_inline_capability_fallback(evidence)`) — the docstring explicitly
  documents this as *"honest, clearly-labeled `provisional: true`"* — a
  deliberate, labeled fallback, not a silent substitution.
- The remaining files (mcp_server/server.py, mcp_server/resources.py,
  mcp_server/tasks_extension.py, services/embeddings.py, services/authn.py,
  services/domain_search.py, services/claims.py, services/procedures.py,
  services/claim_belief.py, services/applicability.py,
  services/skill_ingestion.py, services/ingestion_jobs.py,
  services/claim_publication.py, stealth/journal.py, stealth/faults.py,
  stealth/atomic.py, execution/durable_resume.py, api/deps.py, and others)
  matched only because the multiline regex also caught ordinary
  `except (TypeError, ValueError): return default` query-param parsers with
  an explicit, caller-documented default (e.g. `_int("limit", 200)` in
  `server.py`'s `/claim-graph/data` route), or `except TokenRejected: pass
  # try the shared-secret fallback below` (a documented, deliberate,
  two-stage auth fallback, not silent), or hit inside a docstring/comment
  block containing the word "except" with no real code below it. None of
  these are the smell this rule targets.

Sweep #2 (`or "placeholder-string"`) found 5 hits
(`api/agents.py`, `export/markdown_diff.py`, `services/ingestion_admission.py`,
`services/provider_policy.py`, `services/publication_deps.py`) — all inside
human-readable log/error/audit-message string formatting, never written as a
structured field a downstream caller would treat as real data. Not
violations.

Sweep #3 (`except ImportError:`) found 4 hits total
(`observability.py`, `mcp_server/server.py`, `api/implementations.py`,
`services/invariants.py`) — covered above; all documented, honest, or
genuinely optional-dependency capability probes.

## Total

**~40 files inspected in context** (39 from sweep #1 + overlap with sweeps
#2/#3). **1 real violation found** (`app/stealth/generator.py`, one
function). No fixes applied — this pass is audit-only per the directive.
