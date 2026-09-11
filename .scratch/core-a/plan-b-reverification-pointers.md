# Pointers for a future independent re-verification of Plan B (B1–B38)

**Status:** the founder has said Plan B (MCP + procedure-conditioned
execution hardening, `7a6e18f`) is considered done and will be verified
later, by them or someone else — not blocking now. This file exists only
to hand that future pass a head start: specific things I found or
suspected while auditing the ingestion/knowledge lane (Plan A) that touch
or border Plan B's territory, worth checking first rather than re-reading
all ~40 modules cold.

I did **not** independently re-verify Plan B end to end. Everything below
is either (a) something I confirmed directly with a grep/read this
session, or (b) a claim from Plan B's own self-audit
(`MCP_HARDENING_DEFERRED_ITEMS.md`) that I have not cross-checked myself —
marked which is which.

## Confirmed directly (not just cited) — worth a second look

1. **`app/services/applicability.py::_fetch_candidate_pool`** — the
   `access_scope` parameter IS correctly applied, but only at the FINAL
   full-row fetch (`visibility_predicate` on the `id = ANY(...)` query,
   the "ACCESS FILTERING BEFORE RANKING" block near the end of the
   function). The earlier id-gathering legs (cost/similarity/lexical) run
   with no scope filter at all — deliberate, by that block's own comment
   ("unranked noise"), but worth a second pair of eyes to confirm no path
   ever returns or exposes those raw unranked id lists before the filtered
   fetch runs.
2. **`app/mcp_server/server.py` — `find_best_way` tier-1 and
   `search_procedures`** used to call `find_applicable_procedures(...)`
   with NO `access_scope` at all, silently defaulting to
   `AccessScope.unrestricted()`. Fixed this session
   (`1ae27f7`) — both now pass `access_scope=_caller_access_scope()`. Worth
   checking: are there OTHER MCP tool call sites of
   `find_applicable_procedures` / similar retrieval functions with the
   same omission? I fixed the two I found; did not exhaustively grep every
   tool for this specific pattern beyond `find_applicable_procedures`
   itself.
3. **`app/execution/coordination.py`** — `symbols_expected_to_modify` is
   stored/returned but (confirmed by the code's own comment at line ~22,
   and now closed this session — see the "smallest size" / symbol-conflict
   work) was not used in conflict detection.
4. **`app/services/verification.py`** — grepped for the spec's six named
   verification states (`SELF_REPORT`/`ARTIFACT_INSPECTION`/
   `DETERMINISTIC_CHECK`/`INDEPENDENT_AGENT`/`HUMAN_REVIEW`/
   `REAL_WORLD_OUTCOME`); only `SELF_REPORT` appears, in a docstring. No
   ranked/required ladder structure exists. Confirmed directly, not cited.

## Cited from Plan B's own log, not independently re-checked — verify first

From `MCP_HARDENING_DEFERRED_ITEMS.md` (that lane's own words):

5. **B19 (private-by-default execution)** — their own item 5a/20: tier-2
   capture correctly passes `visibility="private"`, but tier-1's own
   automatic lookup was said to run with `AccessScope.unrestricted()`
   internally. **This is the exact bug class item #2 above fixes** — worth
   confirming whether their described gap is now fully closed by this
   session's fix, or whether they meant a different, deeper spot.
6. **B9-B13 cycle detection** — their item 17, `[DESIGN]`: cycle detection
   is by literal `procedure_id` only, not semantic/goal-equivalence. Stated
   as deliberate scope, not a bug — confirm that's still the intended
   posture.
7. **B11 failure-propagation** — their item 16, `[GAP]`: "retry OR search
   alternative OR branch OR ask user OR fail parent" is explicitly not
   automated; a failed child leaves the parent node merely resumable.
   Confirm this is still an accepted gap, not silently expected to exist.
8. **Item 0, `[STILL OPEN]`** in their own log: a `durable_run.py` jsonb
   double-encoding bug, explicitly deferred by them as "needs its own
   dedicated pass, including checking every reader for compensating
   double-`json.loads()` calls." Worth confirming this was ever picked up.
9. **B3/B4** — their own `[DESIGN]` items 5/6: `verification_plan_id` and
   a run-level `implementation_bindings` column were deliberately NOT
   added (no real referent yet / would drift from
   `execution_run_nodes`'s own source of truth). Confirm this reasoning
   still holds if/when the verification ladder (item 4 below) gets built —
   a real `VerificationPlan` object would remove the "no real referent"
   objection.

## Follow-on from THIS session's own work, relevant to Plan B's surface

10. **The verification ladder (T10/B34)** is a real, confirmed gap (item 4
    above). If it gets built, it should also close Plan B's own
    `[DESIGN]` item 5 (see #9 above) by giving `verification_plan_id` a
    real referent.
11. **Symbol-level conflict detection** in `coordination.py` — implemented
    this session (see the audit doc / commit history for the exact
    change). Worth a second look at whether the static-analysis approach
    chosen (language-aware or regex-based — check the actual commit) is
    precise enough for Plan B's own multi-agent test suite
    (`test_coordination_e2e.py`) to exercise meaningfully, or whether it's
    advisory-only like the glob-overlap check already is.

## Suggested order for the re-verification pass

Cheapest/highest-signal first: #2 → #5 (same bug class, quick to confirm
together) → #4/#10 (verification ladder, one coherent unit of work) → #1/#3
→ #6/#7/#8/#9 (Plan B's own named deferrals, lower urgency since they
already know about them) → #11 (validate this session's own new work).
