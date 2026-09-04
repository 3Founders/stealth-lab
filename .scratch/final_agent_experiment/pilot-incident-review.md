# Pilot Incident Review

## Correct current conclusion

**UNTESTED — the 12-trial pilot was not scoreable.**

This is not a positive product result, and it is not a negative product
result. 0/12 scored trials (T1/T3/T7 x 2 trials x {A, B_default}) reached a
gradeable outcome in either arm -- every completed trial exhausted
`max_steps=8` without writing an answer file. Per the frozen protocol's own
success-first dominance rule, no efficiency or success-rate comparison
between Stealth and baseline is reportable from this pilot. The pilot's
verdict of "C. NO MEASURABLE ADVANTAGE" (recorded in
`final_report.md`) reflects that the experimental design could not produce
a measurable result -- it must never be read or cited as "Stealth showed no
benefit" or "Stealth was tested and found ineffective." The hypothesis
itself (does Stealth cause more success with less model work) remains
completely untested.

## What the pilot actually established, honestly

1. **The real mechanism runs end-to-end for real money.** A real MCP
   server, a real MCP client, real retrieval, real Agent+RepoSandbox
   tool-calling execution, and real token-usage capture (instrumented in
   the prior remediation pass) all worked as designed against
   `gpt-oss-120b` via GENERAL_COMPUTE.
2. **`max_steps=8` is too small for T1/T3/T7 as currently written.** Every
   completed trial in both arms hit the step ceiling mid-exploration,
   never calling `finish`. This is a genuine experimental-design/
   calibration gap, addressed in `budget-calibration.md`.
3. **A real, reproducible reliability incident occurred specifically on
   T7's Stealth arm** (2/2 trials, identical error). Root cause identified
   and fixed -- see `remediation-results.md`.
4. **A real, reproducible corpus-contamination problem exists**: every
   completed Stealth-arm trial's retrieval matched a test-fixture
   procedure, never real knowledge. Root cause fully investigated -- see
   `retrieval-contamination-review.md`. This is the highest-priority
   finding of this remediation pass, per the coordinator's own framing.

## Incident 1: T7/B_default hard-crashed 2/2

Both scored T7/B_default trials
(`raw/T7-B_default-c8de47d3.json`, `raw/T7-B_default-ef45cf05.json`) failed
with the identical error `ExceptionGroup: unhandled errors in a TaskGroup
(2 sub-exceptions)`, both at `wall_clock_seconds_total` of almost exactly
**60.01s** and **60.02s**. This precise, repeated timing match to the
millisecond -- not the GENERAL_COMPUTE 400 errors captured in
`raw/provider_400_errors/`, which occur at varying, unrelated wall-clock
offsets -- was the key diagnostic signal. Full root-cause trace and fix in
`remediation-results.md`.

## Incident 2: Stealth retrieval matched test-fixture procedures on every completed trial

All 4 completed `B_default` trials (T1 x2, T3 x2 -- T7's both crashed
before reaching a usable retrieval-decision state) matched one of exactly
two procedure IDs, both confirmed live-DB test fixtures created by
`backend/tests/test_find_best_way_plan_only_e2e.py`. Full investigation,
all 7 required questions answered, and a committed (intentionally failing)
regression test in `retrieval-contamination-review.md`.

## No scored trial ran during this remediation pass

Per the coordinator's explicit instruction. All work in this pass was:
calibration trials (non-scored, `.scratch/final_agent_experiment/calibration/`),
one non-scored T7 smoke-verification trial proving the provider-robustness
fix (see `remediation-results.md`), and static investigation/testing. No
row was written to `trials.jsonl`; the pilot's own 17 rows (12 scored + 5
documented environmental failures) remain exactly as they were.

## Baseline integrity

Re-confirmed at the end of this pass (not merely assumed):
- `main` @ `bd768e62a887b13a94fdd118693a5c671df1cf95`
- `evaluation-suite` @ `ebabec1350f4ae6eb1e4d09e29f245fa8a4a6d79`
- `better-ways-candidate-results` @ `54ca96a1d1aae991fe38208f54e4c21c5cce8e8b`
- `better-ways-admission` @ `f921e2c385dfab511233adc148a5b7a2b8082932`

All four unchanged. See the final commit's own verification for the exact
`git rev-parse` output.
