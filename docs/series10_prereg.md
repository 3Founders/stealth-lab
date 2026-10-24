# The 10-issue series: pre-registration (written 2026-10-24, before any paid run)

Committed BEFORE the series, the dry run's results or Part 4's models were looked at. It says what is measured, how an area counts as
"shown", what makes a result invalid, and what Part 4 must show for an open Bedrock model to join the cheap-model pool.

**The series is BLIND for Anuj.** Nobody is shown any comparison between the series' arms until `docs/series10_thresholds.md` has a
value on every line and is on origin/main. Until then only progress, total spend and errors are reported (see "Blindness" below). The
thresholds are written by Anuj, in a separate commit, before any comparison is seen; this file does not suggest values.

Related: `docs/acceptance_rules_preregistered_2026-10-16.md` (the cheap-model acceptance rule, whose settings the series inherits).

## What is run

Two repositories, `jsonargparse` and `sqlglot`, 10 consecutive issues each (one Python version, the gold check, the windows in
`experiments/abtest/swe_series_task.py`; a third repository only if the Part 5 estimate allows it). Five arms. Every StealthLab arm has
the same install and settings; only what persists between issues differs. The background survey is OFF in every StealthLab arm
(`STEALTHLAB_AUTO_SURVEY=off`); the survey is a setup step.

| Arm | Repo facts (`claims.md`) | Library | Local routing counts | Server routing state |
|---|---|---|---|---|
| A | plain Claude Code, no StealthLab | – | – | – |
| C0 | none (no survey) | wiped before each issue | wiped | **reset** before each issue |
| C-R | kept (one survey at series start) | wiped | wiped | **reset** |
| C-L | kept | **kept** | wiped | **reset** |
| C | kept | kept | kept | **kept** (learns) |

- The survey is `stealthlab-mcp survey` (local, no model, uploads nothing) run once before issue 1 in C-R, C-L and C. Its seconds and fact
  count are recorded with issue 1.
- The server's router state is reset with a data-only database snapshot taken right after setup; it covers every table except
  `schema_migrations`, and the run refuses to start if the router's tables (`routing_observations`, `routing_posteriors`,
  `routing_decisions`) are not among them. The list of tables is saved as `reset_scope.txt` next to each arm's results.
- Each arm has its own container and its own backend; no arm reads another's state.
- Issues: the same issue, in the same order, in all arms (per-issue pairing). Windows are chosen by the existing rules, with the reuse
  opportunity (share of issues whose gold patch touches a file an earlier issue in the window touched) as the tie-breaker among windows
  within 30 days of the tightest; each candidate's overlap is printed.

## The five comparisons

Metrics, per issue, per arm, taken from the results rows: **solved** (the hidden tests; the agent's own claim never counts), **Claude
cost** and **open-model cost** (separately and together), **cheap fixes accepted** and **how many of those were correct**, **wrong fixes
applied** (a wrong cheap fix delivered) and **correct cheap fixes thrown away**, and **seconds**.

| Comparison | Arms | Question | Metric the verdict is about |
|---|---|---|---|
| Reduce | C vs A | does StealthLab cost less at an equal or better solved rate? | total cost per issue, solved count |
| Recall | C-R vs C0 | do repo facts (the survey) help? | solved, cost, cheap accepted-and-correct |
| Reuse | C-L vs C-R | does a library of earlier fixes help? | solved, cost, cheap accepted-and-correct |
| Route learning | C vs C-L | does the router learning from outcomes help? | cost, cheap acceptance rate, seconds |
| Tests decide | every StealthLab arm | do the checks catch wrong cheap fixes and keep right ones? | wrong fixes applied; correct cheap fixes thrown away |

"Tests decide" is a count per arm, pooled; it has a threshold like the others.

## Decision rule

With 10 issues per repository there are no p-values. An area is **shown** when all of the following hold, and **not shown** otherwise:

1. its difference has the **same direction in every repository**;
2. on the **pooled** numbers the difference is **at least its threshold**, taken exactly as written in `docs/series10_thresholds.md`;
3. the **solved rate does not drop** (the arm that is supposed to be better or cheaper solves at least as many pooled issues).

Both outcomes are reported the same way, with the numbers. **The thresholds are not set yet.** Anuj writes them, one line per area
(`Reduce:`, `Recall:`, `Reuse:`, `Route learning:`, `Tests decide:`), in `docs/series10_thresholds.md`, as a separate commit, before seeing
any comparison. The file exists now as a stub with no values.

## Amortization (descriptive, not a sixth verdict)

Does memory pay for itself over the series? Reported, with no shown/not-shown label:

1. **Cumulative cost by issue, 1 to 10**, per arm and per repository, for Claude cost, open-model cost and both. The one-time survey counts at
   issue 1; failed (not accepted) cheap attempts count at the issue they happened in.
2. **The break-even issue** for C against A and for C against C0: the first issue at which C's cumulative cost is lower and stays lower;
   "not reached in 10 issues" when it does not happen, plus the trend of the gap over the last 3 issues (closing, flat, widening).
3. **First half against second half:** mean cost per issue for issues 1 to 5 against 6 to 10, for C, C-L, C-R and C0, with the solved count
   of each half beside it.
4. **Where the payback came from** (C, second half against first): cheaper cheap-model wins, more tasks handled by a cheap model, fewer wasted
   cheap attempts, library entries reused.
5. One plain sentence per repository. Repositories are pooled only if they agree. The horizon is short (10 issues); this is said in the report.

To make these computable, every result row (or a sidecar on the volume) carries: the setup survey's seconds and any tokens or dollars (zero
and `uses_a_model: false` for the local survey), the tokens and dollars of every cheap attempt that was not accepted, and Claude cost and
open-model cost for every issue.

## What would make a result invalid

Any of these voids the affected issue (and is reported, not worked around):

- an arm stopped mid-issue (a hang, a crash, a container restart that lost the issue's state);
- a key ran out of quota (General Compute, z.ai, Claude) during the issue;
- the issue failed its gold check (the gold patch does not fix it in this environment);
- state leaked between arms (a workspace, `.stealth/`, the database, or a results file read by another arm; or a wipe or reset that did not
  do what the table says);
- one arm's sessions ran on a different model than the other arms' (the same model id family is required; Bedrock's id for it counts as the
  same model).

**An issue may be served by the subscription or by Bedrock**, with the same model (Opus 5.5 for the session, Sonnet 5.5 for the reviewer).
Every session is recorded with the auth that served it, and the report shows which. A session is never split between the two: a usage limit
restarts the issue from its beginning, with its state restored.

## Blindness

Until `docs/series10_thresholds.md` has a value on every line and is on origin/main:

- Reported while the run goes, and after it ends: progress (issues done per arm), total spend (subscription sessions, Bedrock sessions,
  Bedrock dollars), errors and invalid-result events.
- **Not** computed, printed, committed or sent: any arm-versus-arm comparison, any per-arm total of solved or cost, any per-issue result
  table, any amortization figure. The container logs print only a session's status; `report.py` is not run (`--blind`).
- Raw result files are saved on the Modal volume only, not committed.
- The Part 5 dry run (1 issue, all five arms) reports only its checks, minutes and cost per session, and errors: not which arm solved the
  issue, and no cost comparison between arms.
- If anyone asks how the arms compare before then, the answer is that the run is blind until the thresholds are on main.

## Spend limits

AWS credits $100: at most $20 for Part 4, at most $70 as Claude overflow in the series, $10 kept back. The overflow is controlled by a ledger
(`bedrock_ledger`, one file per unit on the volume, summed before every new Bedrock session, with room kept for one typical session per
unit that could start at the same time); AWS Budget alerts at $60 and $85 are the backstop. At a cap the work stops and waits for the
subscription to reset.

## Part 4: Bedrock's open coding models as cheap models (not blind)

Question: is any of Bedrock's open coding models as good a cheap first try as the current ones (gemma, gpt-oss, minimax, glm-5.3-flash on
General Compute and z.ai)? It does not change the series: the series keeps the current cheap models. Nothing is added to the product's
defaults; the result is a recommendation.

Design: 3 to 4 models chosen from the Part 0 list by this filter, in order (reliable tool calling in an agent loop; reachable through
Bedrock's OpenAI-compatible endpoint or a LiteLLM proxy; price per million tokens at most about 2x the current cheap models; context at
least 64k; then ranked by a public agentic-coding score with the source cited), aiming for the strongest Qwen Coder, a small fast coder,
DeepSeek's latest, and gpt-oss as the control (same model as on General Compute, so the difference is the provider). One smoke issue per
model first (drop on errors, rate limits or a tool loop that cannot finish); then about 10 issues from a repository NOT used in the series
(or, if no other repository passes the gold check, issues outside the series' windows), each model as the only cheap try, graded by the
hidden tests (stage 4 of `modal_survey.py`). The current General Compute models run on the same issues unless that would bring General
Compute's daily quota below what the series needs that day; then the latest survey's numbers are used and said to be.

Per model: accepted, accepted-and-correct, cost per task, median and p95 seconds, errors and rate limits.

**Decision rule (written now, before Part 4 runs).** A Bedrock model joins the recommended cheap-model pool when, on the same issues:

1. its accepted-and-correct count is **at least that of our best current cheap model** (the one with the most accepted-and-correct on these
   issues), and
2. its cost per task is **no more than that model's**, and
3. its median seconds are **no worse than 1.5x** that model's, and
4. it hit no more errors or rate limits than the best current model hit **plus 2** over the run (so one flaky evening does not decide).

If it passes 1 and 3 but not 2, it is reported as "better but dearer" and the cost gap is shown; it is not recommended. With about 10 issues
the comparison is by counts, not p-values, and the report says so. Part 4 is not blind.
