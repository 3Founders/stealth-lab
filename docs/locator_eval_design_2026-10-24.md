# Locators in procedures + output-shape reference from `.stealth`: how to measure the benefit

Written 2026-10-24, against [results_index.md](results_index.md) and the survey stage files. The idea has two parts:

1. **Locators inside procedures** — a procedure step carries a typed pointer (file path, symbol anchor, or
   search pattern) so an agent lands on the right file with fewer greps/globs/reads.
2. **Output-shape reference** — the hook message points the agent at a concrete expected-output format stored
   in `.stealth` (artifact shape, report shape), instead of prose.

They are two different claims and one of them can move a number the other cannot. Measured separately, then
together. Below: what the existing evidence allows, three measurement stages (two free, one cheap), and the
decision gates.

---

## 1. What the evidence already on disk says about headroom

| Evidence | Number | What it limits |
|---|---|---|
| [results_index C3] | top-3 correct file **96%**; **39 of 44** wrong answers had touched the right file | Localization is *not* the main failure mode on the survey set. A locator that improves top-3 from 96% has almost no accuracy room there |
| [results_index C5] | best acceptance rule **4 wrong of 25**; product default **8 wrong of 25** | The weakest point is acceptance. **A locator cannot fix this, and a claim that it does is excluded** |
| [results_index F2] | inside routing time: brief 41%, **locate 44%**, triage+lookup+gate ~16% | Locate is the single biggest measured slice inside routing. This is the hook's own retrieval step — a different "locate" from the agent's file search, see §2 |
| [results_index F4/F5] | routing ≈ 27% of C's added median; whole-task p50 ≈ 2× A | Latency is where a concrete, honest win is available |
| [results_index D5] | C vs C0 (memory carried over): **no benefit yet** on 1 issue/repo | Prior on "`.stealth` context helps": null so far. The locator claim must be proven, not assumed |
| [results_index A9] | plan_and_run: **2.5–4.4× tokens**, deepseek −6.1 pts | Adding *format* to the agent's life has already been measured once as harmful. The output reference must stay small and optional |
| [results_index A10] | hook fixed 6 of Sonnet's failures and broke 5 of its successes | Confident hints can mislead strong models — the locator-precision gate matters more than the coverage one |
| A/B failure taxonomy (handoff §11/§12) | NO_EDIT_at_all ~11, LOCALIZATION_miss ~9–10 of 150 code tasks; RIGHT_FILE_wrong_fix ~12 | The population where locators can plausibly change *correctness*: ~20 of 150 code tasks. Small but real |

**Honest prior, stated up front:** locator-type knowledge is steps-like knowledge, and our own E3 finding was
"steps-only knowledge did nothing." Cross-repo benefit is the risky claim; same-repo reuse is the plausible
one; latency is the near-certain one. Design to let the null surface cheaply.

## 2. Two different "locates" — do not conflate them

- **Hook-side locate (F2's 44%):** retrieval *inside* StealthLab routing, finding which procedure/knowledge to
  hand over. A locator field in procedures can shrink this too, but it is a second-order effect.
- **Agent-side locate (the user's idea):** the agent's own exploration — grep/glob/read cycles before its
  first edit. This is what the locator targets primarily.

The primary endpoint below is defined on the agent-side locate. Hook-side effects are a secondary read.

## 3. Locator taxonomy (what counts as a locator)

Each procedure step may carry zero or more typed locators:

| Kind | Shape | Valid for | Example |
|---|---|---|---|
| **absolute** | exact `path` + `symbol` anchor | same repo, repeat failures (the Reuse case, "not measured" per the index) | `jsonargparse/_core.py :: ArgumentParser.__init__` |
| **query** | a *fast* resolution recipe: grep pattern / symbol kind / directory hint | cross-repo, per failure class | "failure lives in the arg-parse helper: `rg -l 'def .*parse.*args' --type py`, read top hit" |
| **negative** | what it is *not*, to block the known wrong-file classes | all | "not the CLI entrypoint; entrypoint files were first-edit targets in 9/44 misses" |

Production shape: locators live as fields on procedure steps (compiled into `.stealth/procedures.md`),
resolved count is recorded per run in `.stealth/run.md` (`locator_hits`, `locator_resolved`, tool calls
saved). That run-record is what makes every later measurement free (§4, M0's rule).

## 4. Measurement design

### M0 — instrument existing traces (no model calls, days, $0)

Define the endpoints once, then compute them on traces already on disk
(`experiments/abtest/results/chaitanya-2026-10-16/survey/*.jsonl`, the series v4–v7 `turn_log`s, and the
205-task run logs). Truth comes from the grader's gold diff (files the real fix touched), never from the
agent's own account — the same rule the survey followed.

Per task, from the trace:

| Endpoint | Definition |
|---|---|
| `locate_calls` | search/read tool calls (grep, glob, ls, read, semSearch, mcp-search) **before the first Edit/Write** |
| `locate_tokens` | billed input+output tokens on those turns |
| `locate_seconds` | wall time from run start to first edit |
| `gold_rank` | rank of the first gold file among pre-edit reads (their stage-3 scorer already computes top-3 — reuse it) |
| `wrong_first_edit` | first edit targeted no gold file |
| `dead_reads` | pre-edit reads of files absent from the final graded diff (wasted exploration) |
| `first_pass` | the check accepted the first cheap answer without a retry |

M0 delivers the baseline distribution of all of these. It also re-answers C3 with the tight definition above
(the survey's 96% was computed pre-edit on candidate files; the trace version adds the wasted-read and per-arm
split). **Gate: if `dead_reads` and `locate_calls` turns out to be small on both real-repo sets, the locator's
upside is already spent and we stop here.** Expected finding, given F2/C3: locate is not huge on benchmark
issues but is plausible on the long-horizon series (sqlglot C runs read many files before editing).

### M1 — offline locator quality (no agent, ~2–3 days, $0–$5)

For the 25 valid survey issues (and the 2 series issues), write the locator that *should* exist for the gold
target given only the failure class (no peeking at the answer's diff), then resolve it statically against the
repo at the issue's start commit:

| Metric | Definition | Gate |
|---|---|---|
| coverage | issues of the class where a locator was constructible at all | < ~60% → the locator product is thinner than assumed; report and stop |
| precision@1 / @3 | locator's resolved file is the gold file, in rank 1 / 3 | ≥ ~70% @1; below that the hint misleads (A10's bar) |
| staleness | locator's path/symbol exists at the issue's start commit | also fills the "facts going stale" gap in the index's unmeasured list |
| resolution cost | literal cost of executing the locator (1 grep vs the baseline agent's median `locate_calls` from M0) | if executing the locator is not cheaper than the agent's own median search, the cross-repo claim dies here, for free |

This stage is the finder of false locators before they mislead anyone (A10's risk), and produces the paper
without markers: coverage and precision are computed against the stored survey ground truth, written before
any green run.

### M2 — live ablation on the 25-issue survey set (days, ~$10–25 total)

Paired, three arms, same 25 issues, cheap-model-driven exactly like C1 (≈ $0.09–0.11/issue):

| Arm | What changes |
|---|---|
| **X0** | current default (procedures, no locators, no output reference) |
| **X1** | procedures carry locators; nothing else changes |
| **X2** | X1 + a short expected-output reference in the hook message (one line pointing at the `.stealth` artifact shape; not the plan_and_run ceremony — A9 showed that hurts) |

Primary endpoint (pre-registered before the run files exist): **locate-phase cost = `locate_tokens` +
`locate_calls` + `locate_seconds`, paired per issue** (paired design like the earlier surveys, deltas per issue,
bootstrap CI — the machinery from acceptance_rules_preregistered is reusable).

Secondary: `wrong_first_edit` rate, `gold_rank`, `first_pass`, $/task, solve rate (guard only).

**Guard rails copied from the existing discipline:**

- Solve rate may not drop: report CI; a locator that lands faster but solves less is a fail (compare A10).
- The grader is fixed (C7); re-check the first 5 arms against it before believing anything (42/107 were
  mis-scored last time — the check bug that flipped v6's sign).
- The two harness bugs behind v7-sqlglot's waste (hand-in at the limit, worktree file, spiral) are fixed but
  **not re-measured (D6)** — re-run the one free confirmation before M2, or M2 inherits that noise.

### What M2 does not touch

**Acceptance (C5).** The locator can reduce how often verification is even needed (fewer wrong-file patches
to reject), and the output reference can make the checked artifact parse first-time, but the acceptance rate
itself is measured by series10, unchanged. **Do not alter series10's preregistered arms; only harvest its
traces afterwards into M0/M1.**

## 5. Decision gate (what "works" means)

Ship locators into the default path only if, with CI:

1. `coverage ≥ 60%` and `precision@1 ≥ ~70%` on M1 (else the hint misleads more than it helps, A10's bar);
2. locate-phase cost reduction has CI excluding zero and is worth the hook's added tokens (A5's overhead
   framing: the locator text is billed to the agent — keep it small, ≤ ~150 words);
3. solve rate CI includes no regression (X1 vs X0 paired);
4. the output reference (X2 vs X1) shows `first_pass` improvement **without** the X2 token overhead repeating
   A9's 2.5–4.4× pattern — if X2 only adds tokens, the output reference ships as an opt-in, not a default.

If the result is null cross-repo but positive same-repo, the honest product line is: locators are a
**reuse** feature (the index's unmeasured row), and the claim is written that way.

## 6. Cost

| Stage | Money | Wall time |
|---|---|---|
| M0 | $0 (scripts over existing jsonl/turn_log) | 1–2 days |
| M1 | $0–5 (static resolution + spot checks) | 2–3 days |
| M2 | ~$10–25 (~3 × 25 issues at survey rates) + grader | 3–5 days |

M0 and M1 can run in parallel; both are offline. Nothing here touches a client, a deadline, or an
application — it is bench work between the series10 runs.

## 7. Sources

- [results_index.md](results_index.md) rows C1, C3, C5, C7, D5, D6, F2, F4, F5, A5, A9, A10
- `experiments/abtest/results/chaitanya-2026-10-16/survey/` (stage-3/4 jsonl, `rules-r1-2026-10-17.txt`)
- [acceptance_rules_preregistered_2026-10-16.md](acceptance_rules_preregistered_2026-10-16.md) (paired-analysis machinery)
- [series10_prereg.md](series10_prereg.md), [series10_thresholds.md](series10_thresholds.md) (arms not to be altered)
- [HANDOFF_abtest_2026-10-11.md](HANDOFF_abtest_2026-10-11.md) §11/§12 (failure taxonomy; 205-task traces)
