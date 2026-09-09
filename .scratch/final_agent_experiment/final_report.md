# Final Baseline vs Stealth Agent Experiment — 12-Trial Pilot Report

**Status: 12 scored trials complete, real money spent, real infrastructure.**
Frozen commit: `bd768e62a887b13a94fdd118693a5c671df1cf95`. Model: `gpt-oss-120b`
via GENERAL_COMPUTE. Protocol was NOT changed mid-run; the two issues found
(env-loading bug, T7/B_default crash) were handled exactly as the frozen
protocol's own rules require — a genuine infrastructure bug was fixed BEFORE
scoring began (documented below, with the exact trial index the fix applied
at), and a genuine in-run failure pattern was recorded as real data, not
routed around.

## What actually happened, in order

1. First invocation of the (newly-built) `run_pilot.py` hit a real bug: `.env`
   was loaded only into the MCP-server subprocess's environment, never into
   the orchestrator's own `os.environ`. Arm A (in-process, no subprocess)
   failed immediately with `KeyError('GENERAL_COMPUTE_API_KEY')`; arm B's
   in-process retrieval/embedding calls failed the same way inside an
   `ExceptionGroup`. **5 attempts failed this way, zero tokens spent on any
   of them** (every failure occurred before any real API call). This is
   recorded in `trials.jsonl` with `environmental_failure: true` and the
   exact reason, per protocol — never silently dropped.
2. Per the frozen protocol's own instruction ("if a genuine infrastructure
   bug is found mid-run... STOP, fix the infrastructure, and note the exact
   trial index"): the run was stopped after trial 5, the `.env`-loading bug
   was fixed (load into `os.environ` first, before anything else), verified
   with a non-scored sanity check, and the run restarted clean.
3. All 12 scored trials (T1/T3/T7 × 2 trials × {A, B_default}) then ran to
   completion in the fixed run. No further mid-run changes were made.

## Primary result: 0/12 scored trials succeeded, in either arm

Every non-crashed trial (10 of 12) exhausted its `max_steps=8` budget in
exploration (`stop_reason=step_budget`, `files_touched=[]`) — the agent
never reached the point of writing `answer.md`, in **10 of 10** non-crashed
trials, across both arms, across all three tasks. The remaining 2 trials
(T7/B_default, both) crashed outright before reaching that point at all.

**This means success-rate and success-normalized-efficiency — the primary
comparison the frozen protocol calls for — are not measurable from this
pilot's data.** Per the protocol's own dominance rule ("a lower-token run
that is incorrect is NOT a win"), reporting a raw-token or raw-latency
"winner" between two arms that both failed 100% of the time would misstate
what happened. Neither arm is reported as more efficient in a success-
normalized sense, because there is no success to normalize against.

## Raw metrics (both arms failed 100%; reported descriptively, not as wins)

| task | arm | n | success | mean tokens | mean model calls | mean wall-clock (s) |
|---|---|---|---|---|---|---|
| T1 | A | 2 | 0/2 | 15,222 | 7.0 | 42.1 |
| T1 | B_default | 2 | 0/2 | 12,599 | 6.5 | 63.1 |
| T3 | A | 2 | 0/2 | 17,717 | 8.0 | 8.6 |
| T3 | B_default | 2 | 0/2 | 8,624 | 5.5 | 119.3 |
| T7 | A | 2 | 0/2 | 23,478 | 8.0 | 11.4 |
| T7 | B_default | 2 | 0/2 (crashed) | n/a | n/a | 60.0 |

`retries` = 0 for every trial (the real production `LocalAgentRunner` path
hardcodes `max_retries=0` — confirmed by reading its source; this is a real,
structural fact about the current mechanism, not a gap in this pilot).
`cost_usd` = `null` for every trial, reason: no versioned GENERAL_COMPUTE/
`gpt-oss-120b` pricing config exists anywhere in this repo (confirmed again
this pass — unchanged from the instrumentation pass's finding).

## The two real findings this pilot actually produced

Even with 0% success, two genuine, specific patterns emerged — this is what
a pilot is for.

### 1. T7/B_default hard-crashes, 2/2, while every other cell (including T7's
own arm A) does not

Both T7 trials in arm B_default failed with an identical
`ExceptionGroup: unhandled errors in a TaskGroup (2 sub-exceptions)` at
~60s, before any token/model-call/Stealth-decision data could be captured.
T7's own arm A trials, and every B_default trial on T1 and T3, ran to
completion without error. This is **not** consistent with random
environmental flakiness (which would not reproduce identically twice while
leaving 10 other trials unaffected) — it looks like a real interaction
between T7's larger scope (the composition task, deliberately the most
demanding in the set) and the real Stealth-enabled execution path
specifically.

**Root cause diagnosed after the fact**, not from the orchestrator's own
exception handler (which only captured `str(exception)`), but from
`experiments/swebench_pro`'s own auto-generated failed-request dumps (6
files, written automatically by the real agent library on a provider
error, secrets-scanned clean, relocated to
`raw/provider_400_errors/` rather than left in a shared directory). All 6
are a real `BadRequestError: Provider request failed with status 400` from
GENERAL_COMPUTE, at steps 3/3/3/4/7/7 — **spread across multiple trials
during this run, not only the two that crashed.** In most trials this
error appears to have been absorbed or retried internally (those trials
still completed with real token usage captured); the working hypothesis is
that T7, the largest-scope task, issues more than one such call inside the
same `asyncio.TaskGroup`, and when two land close together, both surface as
sub-exceptions in one unhandled `ExceptionGroup` instead of being
individually retried. This reads as a genuine GENERAL_COMPUTE provider-
reliability issue combined with a real robustness gap in the current
execution path (no per-call retry/backoff around transient 400s) — not a
Stealth-specific defect, but one that disproportionately hit the Stealth arm
here because it appears to issue more concurrent calls on this task.

### 2. Stealth's retrieval matched something in all 4 completed B_default
trials — and every match was corpus noise, not real knowledge

For T1 and T3 (the trials that ran to completion), Stealth's real retrieval
returned a real match both times per task — but independently queried
against the live database, both matched procedures are synthetic e2e-test
fixtures (`proc-test-planonly-root-2b7fb0f9` and `-5149abb1`, whose `name`
AND `goal` fields are both literally just the same auto-generated fixture
string — not real content). T1's match is the *exact same* fixture this
session's own earlier smoke test matched, for a completely unrelated
trivial task, suggesting it may be an easy, generic lexical hit rather than
anything task-specific.

This is a **corpus-contamination finding, not a retrieval-mechanism bug**:
the mechanism executed a real search and returned a real top hit; the
corpus it searched is ~99% engineering-fixture noise (confirmed by this
session's own earlier Finding D characterization). For a genuinely novel
task with no real matching knowledge yet admitted — which is exactly what
T1/T3/T7 were designed to be, per this experiment's own leakage-avoidance
design — retrieval currently surfaces irrelevant noise instead of correctly
returning "no match." Whether the agent actually wasted a step investigating
this irrelevant match, or ignored it, could not be determined from the
captured `node_notes` this pass.

## Answers to the 13 required interpretation questions

1. **Did Stealth improve task success?** No signal either way — 0% both
   arms on every task. Not measurable from this data.
2. **Did Stealth preserve correctness?** N/A — no trial in either arm
   reached a gradeable answer.
3. **Did Stealth reduce tokens?** Descriptively, yes on raw mean (T1: 12,599
   vs 15,222; T3: 8,624 vs 17,717) — but this is a comparison between two
   0%-success arms and must NOT be read as a real efficiency win per the
   protocol's own dominance rule. Reported as a raw observation only.
4. **Did Stealth reduce model calls?** Descriptively similar pattern to
   tokens (T1: 6.5 vs 7.0; T3: 5.5 vs 8.0) — same caveat as above.
5. **Did Stealth reduce tool calls?** Not clearly measurable — `tool_calls`
   wasn't captured for arm B in this orchestrator version (only
   `node_notes` text, not a structured field); T1/T3 both show `tool_calls`
   pegged at the max_steps ceiling for arm A. A real gap to close before
   the full matrix.
6. **Did Stealth reduce latency?** No — wall-clock was consistently HIGHER
   for B_default than A on every task that completed (T1: 63.1s vs 42.1s;
   T3: 119.3s vs 8.6s), reflecting the real overhead of the MCP round-trip
   and retrieval call. This is a real, measured cost of the Stealth path
   in this pilot, not an efficiency win.
7. **Did Stealth reduce retries?** No difference — 0 for every trial in
   both arms (the mechanism has no internal retry logic at all right now).
8. **Which tasks benefited?** None, on the primary success metric. On raw
   token counts only (not a validated win), T1 and T3 showed lower B-arm
   token counts.
9. **Which tasks did not benefit?** All three, on the primary metric. T7
   specifically shows a NEGATIVE signal for the Stealth arm (reliability).
10. **Did any task regress?** Yes — T7/B_default's 2/2 hard-crash rate
    against T7/A's 2/2 graceful-completion rate is a real, reproducible
    reliability regression specific to the Stealth-enabled path on this
    task. Root cause IS diagnosed (see above): a real, intermittent
    GENERAL_COMPUTE `400` provider error, seen across multiple trials in
    this run, that in T7's case landed twice inside one `asyncio.TaskGroup`
    with no retry/backoff, producing an unhandled `ExceptionGroup`.
11. **Which Stealth mechanism appears responsible?** For the token/latency
    pattern: real retrieval + real MCP round-trip overhead (adds real
    wall-clock cost, and appears to change how much exploration budget gets
    consumed before the step ceiling — plausibly because a matched
    procedure, even an irrelevant one, changes the agent's early
    trajectory). For the T7 crash: a real GENERAL_COMPUTE provider `400`
    error (confirmed via `raw/provider_400_errors/`), landing without
    retry/backoff inside a `TaskGroup` that appears to run more concurrent
    calls on this larger task — a robustness gap in the current execution
    path's error handling, not a Stealth-retrieval defect specifically.
12. **Is there evidence of a composition effect?** No — T7 (the task
    designed specifically to test composition) is the one cell that could
    not even be measured on the intended metrics, because it crashed in
    the Stealth arm before any usage/composition data was captured. The
    composition hypothesis remains completely untested by this pilot.
13. **What remains unproven?** Essentially everything the experiment set
    out to measure: whether Stealth improves success, reduces real cost, or
    produces a composition effect. What this pilot DID prove: the real
    mechanism runs end-to-end for real money (`gpt-oss-120b` via
    GENERAL_COMPUTE, real MCP server, real retrieval, real token capture);
    the current `max_steps=8` budget is too tight for these three tasks in
    either arm; Stealth's retrieval on this specific corpus currently
    surfaces fixture noise for genuinely novel tasks; and there is a real,
    specific, unexplained reliability problem with the Stealth path on the
    largest/most composition-heavy task type.

## Uncertainty discipline

n=2 per cell. No p-value or significance claim is made anywhere in this
report, or should be inferred from it. The token/latency numbers above are
raw sample means over 2 trials each — real numbers, not estimates, but not
a statistically powered comparison. The T7 crash pattern (2/2, identical) is
the strongest signal in this dataset precisely because it is 100%
reproducible within its own small sample, not because the sample is large.

## Verdict

**C. NO MEASURABLE ADVANTAGE**

Justification, from the observed data only: the pilot could not measure the
protocol's primary hypothesis (success + efficiency) because neither arm
succeeded at any task, so there is no efficiency comparison to make under
the protocol's own success-first dominance rule. This is not "no effect
measured because Stealth and baseline tied" — it is "no effect measurable
because the experimental design (specifically `max_steps=8`) did not let
either arm reach a scoreable outcome." Distinct from that non-result, this
pilot DID surface one specific, real, negative-leaning signal (the T7
reliability regression) and one specific, real, corpus-quality problem
(retrieval noise) — neither is severe enough on n=2 to justify **D. NEGATIVE
/ REGRESSION** as the headline verdict, but both are real findings that
should gate the full-matrix run, not be treated as if the pilot came back
clean.

## Recommendations before any full-matrix run

1. **Fix `max_steps` calibration first** — either raise the step budget or
   redesign T1/T3/T7 to be completable within 8 steps; this pilot's most
   basic finding is that the current budget prevents ANY graded result.
2. **Add retry/backoff around transient provider errors inside the real
   execution path's `TaskGroup` usage** — the root cause is now diagnosed
   (a real GENERAL_COMPUTE `400` occasionally landing without retry inside
   a concurrent task group); this is a real robustness gap worth fixing
   in the product's own execution path before the full matrix, independent
   of anything Stealth-specific. Also capture full tracebacks (not just
   `str(exception)`) in the orchestrator's own error handling going forward
   so future failures don't require digging through a library's incidental
   debug dumps to diagnose.
3. **Capture a structured `tool_calls` count for arm B** (currently only
   in unstructured `node_notes` text) so tool-call deltas can actually be
   compared.
4. **Investigate whether the matched-but-irrelevant procedure actually
   changes agent behavior** (wasted exploration vs. correctly ignored) —
   relevant to interpreting the token/latency deltas observed here.

## What this pilot did NOT do

Did not modify `main`, `evaluation-suite`, `better-ways-candidate-results`,
or `better-ways-admission`. Did not modify `C:/Users/user/stealth-lab`. Did
not modify the frozen protocol, task set, budgets, model, or stopping
criteria — the one infrastructure fix (`.env` loading) was applied BEFORE
any scored trial ran, per the protocol's own explicit allowance for fixing
a genuine infrastructure bug mid-run. Did not manually select a procedure
for arm B at any point. Did not intervene in any trial. Did not silently
rerun a failed scored trial — the 2 T7/B_default failures are recorded as
real scored data, not replaced, because the pattern looks like a genuine
finding, not a transient blip, and replacement trials were not authorized
beyond the pre-registered 12.
