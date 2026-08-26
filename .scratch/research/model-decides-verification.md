# Model-Decides Tier — Independent Verification

**Lane:** research · **Date:** 2026-08-27 · **Task:** independently verify MEASURE's
live sweep of the model-decides stale-procedure tier (board Lane MEASURE fifth wave,
commit `5929904`, "model-decides stale-procedure tier implemented + live sweep").
**Method:** same discipline as `run1-verification.md` — fresh recomputation from raw
JSONL with an independent classifier (`.scratch/research/model_decides_verify.py`,
pure stdlib, does not import `model_decides.py` or `scoring.py`), cross-checked
against the committed `model_decides_report.json`, plus a hand-read of every
`C_journal` entry across all 24 tasks to confirm refusals are genuinely
model-decided.
**Inputs:** `C:\Users\user\sl-measure\experiments\harness\model_decides_results.jsonl`
(24 rows) + `...\model_decides_spend.jsonl` (144 rows) — read-only, gitignored,
sl-measure's worktree, one sweep only (no second sweep exists yet — see §5) —
cross-checked against the **committed** `experiments/harness/model_decides_report.json`
and `experiments/harness/fixtures/model_decides/{tasks,procedures,rag_corpus}.json`.

## Verdict table

| Board claim | Recomputed | Status |
|---|---|---|
| Sensitivity: 11 discordant pairs (B-only 1, C-only 10), exact p=0.0117 | Identical: 11 discordant (1, 10), p=0.01171875 | **CONFIRMED exactly** |
| Specificity: C false-refusal 0/12 on control tasks | Identical: 0/12 | **CONFIRMED exactly** |
| power@observed=0.74, MDE q>=0.924, n-for-80%=9 | Identical via `mcnemar_power.py` (unchanged, pinned since RUN #1) | **CONFIRMED exactly** — see §4 for a presentation nuance |
| "every one of these 12 trap refusals is genuinely model-decided" | Confirmed: every `check_applicability` verdict is `True` (bypass working); zero refusals carry the mechanical gate string; all 11 refusal reasons are situation-specific model prose | **CONFIRMED** |
| 24/24 valid first try, $0.0725, "72 absorbed 429s/network retries" | 24/24 valid confirmed; $0.072497 confirmed; **the 72 absorbed attempts are 100% HTTP 404 on `ox-alpha`, zero 429s anywhere in the spend log** | **SPEND COUNT CONFIRMED, CHARACTERIZATION WRONG** — see §3 |

## 1. Sensitivity — CONFIRMED exactly

Recomputing per-task pass/fail independently from raw episodes (`B.reuse_caused_failure`
for B; `stale_offer_pid in refused_procedure_ids AND ground-truth stale` for C) over
the 12 trap tasks (`dec-*-101/102/103` per domain) reproduces the shipped numbers
bit-for-bit:

```
concordant: 1  discordant: 11 (B_only=1, C_only=10)
exact two-sided McNemar p = 0.01171875  (q_observed=0.9091)
```

Hand check: `p = 2 * (C(11,0) + C(11,1)) / 2**11 = 2*12/2048 = 0.01171875`. Matches.
The one concordant pair is `dec-pdf-102` (both B and C avoided the trap); the single
`B_only` pair is `dec-pdf-103` (§6). Cross-checked against `model_decides_report.json`'s
`sensitivity_discordant` field: **MATCH**.

## 2. Specificity — CONFIRMED exactly

Recomputing `false_refusal := offered_pid in refused_procedure_ids AND NOT
ground-truth-stale` over the 12 control tasks (`dec-*-104/105/106`) gives 0/12,
identical to the shipped `n_false_refusal`/`false_refusal_rate`. C never refused a
procedure it should have reused, in this one sweep.

## 3. NEW FINDING — the entire sweep ran on `openai/gpt-4o-mini`, not `ox-alpha`, and the board's failure-mode description is wrong

This is the headline finding of this verification, and it matters more than any
single number: **every one of the sweep's 24×3=72 model-decision calls was actually
served by `openai/gpt-4o-mini`, the documented fallback, not `ox-alpha`, the
documented primary.** Raw spend log, all 144 attempt rows:

```
('ox-alpha', 404): 72
('openai/gpt-4o-mini', 200): 72
429 count: 0   404 count: 72
```

Every `ox-alpha` attempt returned **HTTP 404** (model not found), a non-retryable
status per `RETRYABLE_STATUSES` — the chain fell through to `gpt-4o-mini`
immediately (attempt 0 on each model, exactly as `openrouter_arms.py`'s documented
fallthrough behavior specifies), which then succeeded 100% of the time. A raw pair
of consecutive spend rows for the same task makes the mechanism visible directly:

```json
{"task_id": "dec-refund-101", "arm": "A", "model": "ox-alpha", "attempt": 0, "status": 404, "error": null, "cost_usd": 0.0}
{"task_id": "dec-refund-101", "arm": "A", "model": "openai/gpt-4o-mini", "attempt": 0, "status": 200, "error": null, "cost_usd": 0.00076}
```

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
live model catalog, out of scope for a read-only verification pass), but two facts
are suggestive: (a) `ox-alpha` succeeded normally as recently as sweep #3
(commit `40121b6`, 18:17) — 41 successes, 9× 429, zero 404s; (b) this session's own
board Log elsewhere records a different model (`claude-3-5-haiku`) being "RETIRED
upstream [first smoke: clean 404 trail]" during the same evening (CORE-B's debate
panel wave). A model alias becoming unavailable partway through a long session is
a plausible, mundane explanation — but it is a fact for MEASURE/founder to confirm,
not to assume silently. **Recommendation: before any second sweep, confirm
`ox-alpha`'s current OpenRouter model id is still valid; if it has been renamed or
retired, `DEFAULT_MODEL_CHAIN`'s primary needs updating, not just its fallback
absorbing the loss silently.**

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

## 5. No second sweep exists yet

Only one timestamped set of `model_decides_results.jsonl` /
`model_decides_spend.jsonl` exists in `sl-measure`'s worktree (both dated
2026-08-26 23:47); the board's own entry says so explicitly ("this is a SINGLE
sweep, n=1 per task ... a second sweep ... not yet run this session"). This
verification therefore covers sweep #1 only. **Re-run this verification (or extend
`model_decides_verify.py`, which is rerunnable and takes `--results`/`--spend`
paths as arguments) once a second sweep lands** — per the task's own instruction
and the same discipline this lane applied to RUN #1's repeat-sweep question.

## 6. `dec-pdf-103` — the one discordant loss, and what it actually shows

The board describes this task as "C neither refused nor reused the offered stale
card at all (silently routed around it)" — accurate at the structured-field level,
but incomplete. C's own `decision_notes` for this episode read:

> "The assumptions of the procedure do not hold due to the version mismatch."

That is the model **correctly identifying the staleness** in free text — the
procedure card is `pdf-sheet-v1` (assumption: "pdfplumber 0.7 `table.extract()`
API"), the situation states pdfplumber 0.12 is installed, and the model's own
reasoning names the mismatch. But this reasoning never became a structured
`refuse[]` entry (`C_journal` shows only `search` + `check_applicability`, no
`record_refusal`), and the episode also never resolved (`resolved: false`) — so it
scores as `pass: false` on the strict metric (design §5: only a structured refusal
of the ground-truth-stale id counts).

This is not a detection failure — it's a **schema-capture gap**: the model reasoned
its way to the right answer and then didn't act on it in the one channel the
metric reads. In the direction of the tier's own claim, this makes C's true
detection rate look **understated, not inflated**, by the strict metric (11 of 12
trap tasks show correct reasoning in prose; only 10 of 12 show a structured
refusal) — the opposite bias from RUN #1's Caveat 2, where the gate made C's
refusal rate look inflated relative to genuine model reasoning. Worth flagging to
MEASURE as a possible design refinement (e.g., also scoring whether
`decision_notes` names the specific violated assumption, as a secondary,
lower-confidence signal) — not a defect in the current metric, which is doing
exactly what design §5 specified.

## 7. Control-task "abstain" cases — a related, smaller instrumentation gap

Three control episodes show the model's own reasoning affirming the offered
(valid, non-stale) procedure applies, without the structured `reuse[]` field ever
being populated:

- `dec-dep-105`: notes — "The assumptions of the procedure hold true, as pip 24.2
  is installed and a lockfile-compatible constraints file is present." —
  `reused_procedure_ids: []`, `resolved: false`. A genuine miss: correct reasoning,
  no credited action, task unresolved.
- `dec-env-105` / `dec-env-106`: notes affirm the procedure applies;
  `reused_procedure_ids: []`, but `resolved: true` — the model apparently solved
  the task by some other path without invoking the offered procedure at all.

None of these corrupt the false-refusal metric (§2) — none refused anything, so
all three correctly read `false_refusal: false`. But they show that
`reused_procedure_ids`/`resolved` alone don't fully capture whether the model's
*reasoning* matched ground truth on control tasks either — a parallel, smaller
version of §6's gap. Not a defect in the shipped specificity number; a note for
whoever next tunes this tier's decision schema or prompt.

## 8. Journal check — genuinely model-decided, confirmed directly

For every one of the 24 tasks, `C_journal`'s `check_applicability` entries show
`verdict: true` — consistent with `substrate_bypasses_gate: true` on every task in
this tier (mcp_surface.py:85-86's unconditional bypass). Because the gate can only
ever return `True` under bypass, the mechanical refusal string
`"assumptions no longer hold (gate)"` (mcp_surface.py:96, `openrouter_arms.py:566`)
is **structurally impossible** to appear in this tier's journals — and indeed it
appears zero times across all 11 recorded refusals. Every refusal reason is
free-text model output that names a specific fact from that task's `situation`
string (the exact dollar amount, day count, pip/pdfplumber/python version) —
e.g. *"The purchase age exceeds the maximum allowed of 90 days"*,
*"PEP 517 build isolation is enabled by default in pip 23.3"*. This is exactly the
missing evidence `run1-verification.md` asked for: refusals attributable to the
model's own reasoning over the offered card's content, not the substrate's
pre-offer gate. **Confirmed independently, not just taken from the board's say-so.**

## Summary

The two headline numbers (sensitivity p=0.0117, specificity 0/12) are exactly
right, and the refusals genuinely are model-decided this time — the design's core
goal is met. But this sweep is not comparable to RUN #1/#2/#3 on the model axis
(100% `gpt-4o-mini`, not `ox-alpha`, due to `ox-alpha` returning 404 on every
attempt — a fact the board currently misdescribes as absorbed 429s), it is a single
sweep with n=1 per task (no variance data yet), and its one discordant loss
(`dec-pdf-103`) is better read as an instrumentation gap than a detection failure.
None of this reverses the finding; all of it belongs in the caveats before any
public phrasing, the same discipline this lane applied to RUN #1.

## Artifacts

- Verification script: `.scratch/research/model_decides_verify.py` (rerunnable;
  takes `--results`/`--spend`/`--fixtures-dir`/`--report` paths — none hardcoded,
  since the raw files live in another lane's worktree and are gitignored)
- Raw data (read-only, gitignored, machine-local):
  `C:\Users\user\sl-measure\experiments\harness\model_decides_results.jsonl`,
  `...\model_decides_spend.jsonl`
- Cross-checked against committed: `experiments/harness/model_decides_report.json`,
  `experiments/harness/fixtures/model_decides/{tasks,procedures,rag_corpus}.json`
- Code refs: `mcp_surface.py:85-91` (bypass + refusal recording),
  `openrouter_arms.py:552-611` (`RealProcedureAgent.arun`, gate-context threading),
  `model_decides.py:135-170` (row-shape functions this report cross-checks
  against), `mcnemar_power.py` (unchanged, exact test — see §4 for the one
  presentation nuance found).
