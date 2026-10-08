# Routing arms of the local-only test: pre-registration (DRAFT, not frozen)

**Status: draft for the owner to decide the bracketed choices.** It becomes binding when `routing.json` is set to
`"frozen": true` and this file's hash is recorded in `runs/kel_frozen.json`, BEFORE any routing arm is run. After
that, any change goes to `DEVIATIONS.md` with its reason. The knowledge test in `PREREGISTRATION.md` is unchanged;
this adds arms that also choose the model.

## 1. Question

With this repository's own `.stealth` knowledge in every arm (L1), does choosing the model per task with the
product's router cost less than always using the strong model, without solving fewer tasks? And how often does a
cheaper first try get accepted by the check while being wrong?

## 2. Hypotheses (one-sided, decided before the run)

- **H1, cost:** the primary routing arm **[R80]** has a lower mean cost per task than STRONG.
- **H2, quality (non-inferiority):** its resolved rate is not lower than STRONG's by more than **[3]** points
  (95% CI lower bound of the difference above -3).
- **H3, reported, not tested:** the wrong-accepted rate (check passed, task not resolved) of every arm.

H1 and H2 must both hold for "routing helps" to be claimed. Everything else is exploratory and labelled so.

## 3. Tasks

- **Tasks:** the scored test set of `PREREGISTRATION.md` §3, the same instances, from `design.json`, valid tasks
  only (`valid_tasks.py`).
- **Order:** each repository's tasks run in `created_at` order, so a plan can learn only from earlier tasks of the
  same repository. Repositories run in parallel.

## 4. Arms (`routing.json`)

| arm | what chooses the model |
|---|---|
| R70 / R80 / R90 | the router at reliability target 0.70 / 0.80 / 0.90 (escalates on a failed check, at most 3 attempts) |
| STRONG | always **[claude-sonnet-4-5]**, one attempt (+ escalation to itself is not allowed) |
| CHEAP | always **[gpt-oss-120b]**, one attempt |
| NAIVE | **[gpt-oss-120b]**, then **[claude-sonnet-4-5]** if the check fails |

- **Models:** **[to decide: which, through which endpoints; keys for every non-Claude model]**. The router's
  candidates are exactly these models.
- **Memory:** L1 (`notes_L1.json`).
- **Agent:** `experiment.json`'s agent, budget and decoding.
- **Router:** the product's recommender, run in-process on the bundled prior (`app/routing/data`, codebook
  `cb1-20261008`). It uses no server and no database, and is conditioned on the repository's earlier outcomes.

## 5. Check and grading

- **One test run per attempt** (`grade_tests.grade_one`) gives both the check and the grade.
- **Check:** **[regression]**, i.e. the patch applies and every PASS_TO_PASS test passes: the repository's own
  tests, which an agent could run. *Alternatives:* `partial` (also one FAIL_TO_PASS test, like a reproduction
  test) or `oracle` (upper bound only).
- **Delivered attempt:** the first attempt that passed the check, else the last one.
- **Resolved:** the delivered attempt passes every FAIL_TO_PASS and PASS_TO_PASS test.
- **Cost:** the sum over all attempts, tokens × the prices in `routing.json` (the list prices of the endpoints
  used). Cache discounts are not counted unless the endpoint reports them.
- **Infrastructure errors** (provider failure, test-run error) are retried and never scored.

## 6. Analysis

- **Cost:** per-task cost difference R80 − STRONG, paired by task. Mean, and a 95% bootstrap CI over
  repositories (tasks within a repository are dependent).
- **Resolved rate:** the difference, R80 − STRONG, with a cluster-bootstrap CI and the non-inferiority margin of
  H2.
- **Also reported** for every arm:
  - resolved rate;
  - accepted rate;
  - wrong-accepted rate;
  - mean attempts;
  - mean cost;
  - which model ran first.

  Losses are reported as plainly as wins.
- **Budget stop:** **[USD cap]**. If it would be exceeded, the arms run on the same random subset of
  repositories, chosen before the run.

## 7. Order of work

1. The owner fills the bracketed choices.
2. A smoke run of 5 tasks per arm with `--allow-draft`. Its results are not used.
3. Fix plumbing only.
4. Set `routing.json` to `"frozen": true` and hash both files.
5. Run every arm.
6. Run `route_run.py --summary`, then the analysis.
