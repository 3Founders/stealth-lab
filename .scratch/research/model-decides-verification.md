# Model-Decides Tier — Independent Verification

**Lane:** research · **Date:** 2026-08-27 · **Task:** independently verify MEASURE's
live sweep(s) of the model-decides stale-procedure tier (board Lane MEASURE fifth
wave, commit `5929904` for run1; sixth wave for run2, landed while this
verification was in progress — both covered below).
**Method:** same discipline as `run1-verification.md` — fresh recomputation from raw
JSONL with an independent classifier (`.scratch/research/model_decides_verify.py`,
pure stdlib, does not import `model_decides.py` or `scoring.py`), cross-checked
against the committed report JSON for each run, plus a hand-read of every
`C_journal` entry across all 24 tasks × both runs to confirm refusals are
genuinely model-decided.
**Inputs:** `C:\Users\user\sl-measure\experiments\harness\model_decides_results.jsonl`
+ `..._results_run2.jsonl` (24 rows each) and their matching `..._spend*.jsonl`
(144 rows each) — read-only, gitignored, sl-measure's worktree — cross-checked
against the **committed** `experiments/harness/model_decides_report.json` /
`model_decides_report_run2.json` and
`experiments/harness/fixtures/model_decides/{tasks,procedures,rag_corpus}.json`.

## Verdict table

| Board claim | Recomputed | Status |
|---|---|---|
| run1 sensitivity: 11 discordant (B-only 1, C-only 10), p=0.0117 | Identical: 11 discordant (1, 10), p=0.01171875 | **CONFIRMED exactly** |
| run1 specificity: C false-refusal 0/12 | Identical: 0/12 | **CONFIRMED exactly** |
| run2 sensitivity: 10 discordant (B-only 1, C-only 9), p=0.0215 | Identical: 10 discordant (1, 9), p=0.02148438 | **CONFIRMED exactly** |
| run2 specificity: C false-refusal 0/12 | Identical: 0/12 | **CONFIRMED exactly** |
| power@observed=0.74 (run1), MDE q>=0.924, n-for-80%=9 | Identical via `mcnemar_power.py` (unchanged, pinned since RUN #1) | **CONFIRMED exactly** — see §4 for a presentation nuance |
| "every one of these 12 trap refusals is genuinely model-decided" (both runs) | Confirmed both runs: every `check_applicability` verdict is `True` (bypass working); zero refusals carry the mechanical gate string; all refusal reasons are situation-specific model prose | **CONFIRMED, both runs** |
| run1: 24/24 valid, $0.0725, "72 absorbed 429s/network retries" | 24/24 valid confirmed; $0.072497 confirmed; **72 absorbed attempts are 100% HTTP 404 on `ox-alpha`, zero 429s** | **SPEND COUNT CONFIRMED, CHARACTERIZATION WRONG** — see §3 |
| run2: 24/24 valid, $0.0722 (board's run2 entry gives no model breakdown) | 24/24 valid confirmed; $0.072204 confirmed; **run2 ALSO 100% HTTP 404 on `ox-alpha`, zero 429s — same undisclosed issue, unflagged a second time** | **SAME ISSUE PERSISTS, still uncharacterized on the board** — see §3 |

## 1. Sensitivity — CONFIRMED exactly, both runs

Recomputing per-task pass/fail independently from raw episodes (`B.reuse_caused_failure`
for B; `stale_offer_pid in refused_procedure_ids AND ground-truth stale` for C) over
the 12 trap tasks (`dec-*-101/102/103` per domain) reproduces the shipped numbers
bit-for-bit, for both runs:

```
run1: concordant: 1  discordant: 11 (B_only=1, C_only=10)
      exact two-sided McNemar p = 0.01171875  (q_observed=0.9091)
run2: concordant: 2  discordant: 10 (B_only=1, C_only=9)
      exact two-sided McNemar p = 0.02148438  (q_observed=0.9000)
```

Hand check run1: `p = 2 * (C(11,0) + C(11,1)) / 2**11 = 2*12/2048 = 0.01171875`.
Hand check run2: `p = 2 * (C(10,0) + C(10,1)) / 2**10 = 2*11/1024 = 0.021484375`.
Both match. In run1 the one concordant pair is `dec-pdf-102`; in run2, two pairs turn
concordant (`dec-pdf-101` joins `dec-pdf-102`) and the `B_only` pair is `dec-pdf-103`
**in both runs** (§6). Cross-checked against each run's committed report JSON's
`sensitivity_discordant` field: **MATCH, both runs**.

Both runs are significant at α=0.05, same direction, comparable magnitude — a real
run-to-run repeat, not a fluke of one sampling draw, as the board itself notes. One
caveat on what "two runs" means here: both ran on the same fallback model (§3), so
this is two independent *samples of gpt-4o-mini's* behavior at temperature-driven
variance, not two samples spanning different models or conditions. Repeat-consistency
under those two draws is still meaningful evidence, just narrower in scope than "two
independent confirmations of the target model's behavior" would be.

## 2. Specificity — CONFIRMED exactly, both runs

Recomputing `false_refusal := offered_pid in refused_procedure_ids AND NOT
ground-truth-stale` over the 12 control tasks (`dec-*-104/105/106`) gives 0/12 in
**both** runs, identical to each run's shipped `n_false_refusal`/`false_refusal_rate`.
C never refused a procedure it should have reused, in either sweep.

## 3. NEW FINDING — both sweeps ran entirely on `openai/gpt-4o-mini`, not `ox-alpha`, and the issue is still undisclosed on the board after a second occurrence

This is the headline finding of this verification, and it matters more than any
single number: **every one of both sweeps' 24×3=72 model-decision calls was actually
served by `openai/gpt-4o-mini`, the documented fallback, not `ox-alpha`, the
documented primary — in run1 AND run2.** Raw spend logs, all 144 attempt rows each:

```
run1: ('ox-alpha', 404): 72   ('openai/gpt-4o-mini', 200): 72   429 count: 0
run2: ('ox-alpha', 404): 72   ('openai/gpt-4o-mini', 200): 72   429 count: 0
```

Every `ox-alpha` attempt in both sweeps returned **HTTP 404** (model not found), a
non-retryable status per `RETRYABLE_STATUSES` — the chain fell through to
`gpt-4o-mini` immediately (attempt 0 on each model, exactly as `openrouter_arms.py`'s
documented fallthrough behavior specifies), which then succeeded 100% of the time,
both runs. A raw pair of consecutive spend rows for the same task makes the
mechanism visible directly (run1 shown; run2 is byte-identical in shape):

```json
{"task_id": "dec-refund-101", "arm": "A", "model": "ox-alpha", "attempt": 0, "status": 404, "error": null, "cost_usd": 0.0}
{"task_id": "dec-refund-101", "arm": "A", "model": "openai/gpt-4o-mini", "attempt": 0, "status": 200, "error": null, "cost_usd": 0.00076}
```

**This is not a one-off blip.** `ox-alpha` succeeded normally in sweep #3
(commit `40121b6`, 18:17, 41 successes / 9× 429 / zero 404s) and has now returned
404 on all 144 attempts across two separate sweeps spanning at least
**2h42m** (run1 files dated 23:47 → run2 files dated 02:29 the same session).
Timing note, for the record: both sweeps had already run before this verification
began (this report started after run2's files already existed, discovered mid-task
via a board rebase conflict — not a case of a recommendation being ignored, just
two runs back-to-back hitting the same still-live, still-undisclosed issue). The
board's run2 entry gives no per-model breakdown at all (just a total spend figure),
so the substitution has now happened twice without anyone's write-up naming it.
**Whoever runs a third sweep of anything on this chain should confirm `ox-alpha`'s
current OpenRouter model id first** — this is now a load-bearing, standing
recommendation, not a hypothetical.

The board's own write-up (line ~1529 of `build-board.md`) describes these 72
non-billed attempts as **"72 absorbed 429s/network retries under the existing
backoff"** — that phrasing is factually wrong. There is not a single 429 anywhere
in `model_decides_spend.jsonl`; the counts (72/72) match the board exactly, but the
*kind* of failure does not. A 429 is transient rate-limiting, consistent with the
established pattern from RUN #1 / sweeps #2 / #3 (which show `ox-alpha` succeeding
the majority of the time: run1 44/95 billed, run2 63/108, run3 41/50 — see those
sweeps' own spend logs). A 404 is categorically different: it means OpenRouter did
not recognize the model id at all, and the chain's own documented behavior treats
it as **non-retryable and permanent for the call** — there was never a chance this
sweep would land on `ox-alpha` once the first request 404'd.

**Why this matters for interpretation:** every prior real-arms sweep in this
project (RUN #1, sweeps #2/#3) is, in the board's own framing, evidence about
`ox-alpha`'s behavior specifically. This sweep is not — it is 100% evidence about
`gpt-4o-mini`'s behavior. The sensitivity result (p=0.0117, q=0.909) is real and
correctly computed, but it says "gpt-4o-mini detects staleness better through the
verified surface than through prose RAG," not "ox-alpha does." Treating this sweep
as a continuation of the same model-level evidence base as RUN #1 would be a
comparability error the same shape as (though smaller in structural consequence
than) `run1-verification.md`'s Caveat 2.

**On cause:** not independently verified here (would require checking OpenRouter's
live model catalog, out of scope for a read-only verification pass), but the facts
now point more strongly than a single sweep could to a standing outage rather than
a transient blip: (a) `ox-alpha` succeeded normally as recently as sweep #3
(commit `40121b6`, 18:17) — 41 successes, 9× 429, zero 404s; (b) it then 404'd on
100% of 144 attempts across two sweeps 2h42m apart; (c) this session's own board
Log elsewhere records a different model (`claude-3-5-haiku`) being "RETIRED
upstream [first smoke: clean 404 trail]" during the same evening (CORE-B's debate
panel wave). A model alias becoming unavailable partway through a long session and
staying unavailable is a plausible, mundane explanation — but it is a fact for
MEASURE/founder to confirm, not to assume silently.

## 4. Minor nuance — the board's power framing, not a computation error

`mcnemar_power.py` is unchanged since RUN #1 (previously independently re-derived
and confirmed correct); its numbers here are individually all correct. But one
board phrase invites a misreading: *"already exceeds n-for-80%-power@this-ratio=9"*
(n=11 discordant pairs > required_n=9). That comparison is literally true, but
exact-test power is **not monotonic in n** — `required_n()` returns the smallest n
that FIRST reaches the target, not a floor every larger n also clears:

```
n= 9  power(q=0.9091) = 0.806   (first n clearing 80%)
n=10  power              = 0.771   (dips back below 80%)
n=11  power              = 0.736   (this sweep — still below 80%)
n=12  power              = 0.911   (clears again)
```

The correct number for "do we actually have 80% power right now" is the one printed
immediately before it on the same board line — `power@observed-split(q=0.909,n=11)
=0.74` — which is honestly below 0.8. The board isn't hiding this (both numbers are
printed), but the prose summary headlines the more reassuring-sounding one. Worth
naming plainly: **at n=11 this sweep does not yet have 80% power against its own
observed effect size**, even though it already clears the exact-test significance
threshold (p=0.0117 < 0.05) — those are two different claims, and only the second
is unambiguously true here.

## 5. Second sweep — landed mid-verification, now covered

Only run1 existed when this verification began; run2's raw files
(`model_decides_results_run2.jsonl` / `..._spend_run2.jsonl`, both dated
2026-08-27 02:29) turned up in `sl-measure`'s worktree partway through, surfaced by
a board-rebase conflict rather than a fresh check — worth normalizing as a working
pattern: **re-run `git fetch` immediately before finalizing any cross-worktree
verification**, since another lane can land a relevant second data point mid-task.
Both runs are now independently verified above (§1–§3); no third sweep exists yet.
`model_decides_verify.py` is rerunnable against any future sweep by pointing
`--results`/`--spend`/`--report` at the new files — no script changes needed.

## 6. `dec-pdf-103` — the one discordant loss, repeated in both runs, and what it actually shows

The board describes this task as "C neither refused nor reused the offered stale
card at all (silently routed around it)" — accurate at the structured-field level
in both runs, but incomplete. C's own `decision_notes` read, run1:

> "The assumptions of the procedure do not hold due to the version mismatch."

and run2:

> "The assumptions of the procedure do not align with the current environment."

Both are the model **correctly identifying the staleness** in free text — the
procedure card is `pdf-sheet-v1` (assumption: "pdfplumber 0.7 `table.extract()`
API"), the situation states pdfplumber 0.12/0.13 is installed, and the model's own
reasoning names the mismatch in its own words each time. But that reasoning never
became a structured `refuse[]` entry in either run (`C_journal` shows only `search`
+ `check_applicability`, no `record_refusal`), and the episode never resolved
either time (`resolved: false` both runs) — so it scores as `pass: false` on the
strict metric (design §5: only a structured refusal of the ground-truth-stale id
counts), consistently.

This is not a detection failure — it's a **repeatable schema-capture gap**: the
model reasons its way to the right answer on this specific replicate twice
straight and doesn't act on it in the one channel the metric reads. Board's own
characterization ("a real and now-repeated model behavior... not sampling noise")
is right about the repetition but doesn't mention the reasoning is correct both
times — worth stating plainly: in the direction of the tier's own claim, this makes
C's true detection rate look **understated, not inflated**, by the strict metric
(11 of 12 / 12 of 12-by-reasoning trap tasks show correct reasoning in prose across
the two runs; only 10 of 12 / 11 of 12 show a structured refusal) — the opposite
bias from RUN #1's Caveat 2, where the gate made C's refusal rate look inflated
relative to genuine model reasoning. Worth flagging to MEASURE as a possible design
refinement (e.g., also scoring whether `decision_notes` names the specific violated
assumption, as a secondary, lower-confidence signal) — not a defect in the current
metric, which is doing exactly what design §5 specified, twice.

## 7. Control-task "abstain" cases — a related, smaller, also-repeated instrumentation gap

`dec-dep-105` shows the same pattern in both runs: the model's own reasoning
affirms the offered (valid, non-stale) procedure applies, without the structured
`reuse[]` field ever being populated. Run1 notes: "The assumptions of the procedure
hold true, as pip 24.2 is installed and a lockfile-compatible constraints file is
present." Run2 notes: "The assumptions of the procedure apply to the current
situation." Both runs: `reused_procedure_ids: []`, `resolved: false` — a genuine,
repeatable miss: correct reasoning, no credited action, task unresolved. (`dec-env-105`
/ `dec-env-106` showed the same reasoning-without-structured-action shape in run1
with `resolved: true` instead — not re-checked in run2 in this pass, since it
doesn't touch either headline metric.)

None of this corrupts the false-refusal metric (§2) — nothing was refused, so it
correctly reads `false_refusal: false` both runs. But it confirms `reused_procedure_
ids`/`resolved` alone don't fully capture whether the model's *reasoning* matched
ground truth on control tasks either, on both draws — a parallel, smaller, now
twice-observed version of §6's gap. Not a defect in the shipped specificity number;
a note for whoever next tunes this tier's decision schema or prompt.

## 8. Journal check — genuinely model-decided, confirmed directly, both runs

For every one of the 24 tasks in **both** sweeps, `C_journal`'s
`check_applicability` entries show `verdict: true` — consistent with
`substrate_bypasses_gate: true` on every task in this tier (mcp_surface.py:85-86's
unconditional bypass). Because the gate can only ever return `True` under bypass,
the mechanical refusal string `"assumptions no longer hold (gate)"`
(mcp_surface.py:96, `openrouter_arms.py:566`) is **structurally impossible** to
appear in this tier's journals — and indeed it appears zero times across all 11
recorded refusals in run1 and all 11 in run2. Every refusal reason in both runs is
free-text model output that names a specific fact from that task's `situation`
string (the exact dollar amount, day count, pip/pdfplumber/python version) —
e.g. run1 *"The purchase age exceeds the maximum allowed of 90 days"*, run2
*"The purchase age exceeds the maximum limit of 90 days"* (near-identical wording
across runs on the same replicate — consistent with a low-temperature or
low-diversity setting on the fallback model, not itself a problem but worth noting
if MEASURE later wants genuinely independent linguistic variation across repeat
sweeps). This is exactly the missing evidence `run1-verification.md` asked for:
refusals attributable to the model's own reasoning over the offered card's content,
not the substrate's pre-offer gate. **Confirmed independently in both runs, not
just taken from the board's say-so.**

## Summary

The two headline numbers are exactly right in both runs (run1: p=0.0117, 0/12;
run2: p=0.0215, 0/12), and the refusals genuinely are model-decided in both — the
design's core goal is met, and repeated. But neither sweep is comparable to
RUN #1/#2/#3 on the model axis (100% `gpt-4o-mini` in both, not `ox-alpha`, due to
`ox-alpha` returning 404 on every single attempt in both sweeps — a fact the board
currently misdescribes for run1 as absorbed 429s and doesn't characterize at all
for run2), and the one discordant loss both runs share (`dec-pdf-103`) is better
read as a repeatable instrumentation gap than a detection failure — if anything it
means C's true detection rate is a hair better than the strict numbers already
show. None of this reverses the finding; all of it belongs in the caveats before
any public phrasing, the same discipline this lane applied to RUN #1. The standing
recommendation for whoever runs a third sweep: confirm `ox-alpha`'s current
OpenRouter model id first.

## Artifacts

- Verification script: `.scratch/research/model_decides_verify.py` (rerunnable;
  takes `--results`/`--spend`/`--fixtures-dir`/`--report` paths — none hardcoded,
  since the raw files live in another lane's worktree and are gitignored)
- Raw data (read-only, gitignored, machine-local):
  `C:\Users\user\sl-measure\experiments\harness\model_decides_results.jsonl` +
  `..._results_run2.jsonl`, `...\model_decides_spend.jsonl` + `..._spend_run2.jsonl`
- Cross-checked against committed: `experiments/harness/model_decides_report.json`
  + `model_decides_report_run2.json`,
  `experiments/harness/fixtures/model_decides/{tasks,procedures,rag_corpus}.json`
- Code refs: `mcp_surface.py:85-91` (bypass + refusal recording),
  `openrouter_arms.py:552-611` (`RealProcedureAgent.arun`, gate-context threading),
  `model_decides.py:135-170` (row-shape functions this report cross-checks
  against), `mcnemar_power.py` (unchanged, exact test — see §4 for the one
  presentation nuance found).
