# SWE-bench Pro pilot — does repo memory buy accuracy or tokens?

A paired, executable experiment on real SWE-bench Pro instances. Two arms
differing in exactly one thing: whether the agent starts with retrieved
context from the repository's own earlier issues.

This is the **pilot** step 0 from `KNOWLEDGE_UPDATION_EXPERIMENT.md` —
"1 repo, ~20 issues. Shakes out harness bugs, rough effect size. **Not a
result.**" Everything below is scoped to that.

## Why SWE-bench Pro and not Atlas

Both were profiled. Pro ships a `dockerhub_tag` per instance and the
`scaleapi/SWE-bench_Pro-os` harness ships a per-instance `run_script.sh` and
`parser.py` — 1000 of them, and they genuinely differ (8 distinct parsers
across 40 ansible instances alone). Atlas ships only an `environment_config`
of build commands, so every repo's environment has to be built and validated
locally before a single instance can be graded, with no published evidence
that gold patches resolve in it.

On one machine with 36 GB free, Pro runs this week and Atlas does not. If
the Atlas result is wanted specifically, that is a separate build.

## Why ansible/ansible

All 11 Pro repos were profiled (`profile_dataset.py` → `profile.json`):

| repo | n | lang | image | net at test time | median P2P |
|---|---|---|---|---|---|
| **ansible/ansible** | **96** | py | **0.54 GB** | **no** | **10** |
| internetarchive/openlibrary | 91 | py | 0.97 GB | no | 5 |
| flipt-io/flipt | 85 | go | 0.70 GB | no | 0 |
| qutebrowser/qutebrowser | 79 | py | 0.58 GB | no | 46 |
| gravitational/teleport | 76 | go | 2.42 GB | no | 0 |
| protonmail/webclients | 65 | js | 3.94 GB | no | 1 |
| NodeBB/NodeBB | 44 | js | 0.85 GB | **yes** | 182 |
| tutao/tutanota | 20 | ts | 1.31 GB | no | 0 |

ansible wins on every axis that matters here: the most instances (so the
memory corpus is deep), the smallest image, plain pytest, and a `run_script`
that never touches the network — so the test container can run with
`--network none` and preserve the invariant `app/services/repo_execution.py`
exists to enforce. NodeBB was excluded specifically because its run script
does `npm install`, which cannot honour that.

## Design

**Split (frozen in `subset.json` before any model ran).** 96 ansible
instances, dated by their `base_commit` against a real clone, sorted. The 20
latest are the eval set (2024-03-07 → 2025-06-04); the 76 earlier ones are
the memory corpus (2019-01-04 → 2024-03-05).

Date-ordered, not random, because **the memory arm may only remember the
past**. Each instance's store is rebuilt with a cutoff at that instance's own
commit date. A random split would put later fixes in the memory of earlier
issues and any gain from that is leakage, not memory.

**Arms.** Same model (`deepseek-v3.1`), same tools, same 25-step budget,
same temperature 0. The only difference is a retrieved context block in the
first user message.

- `no_memory` — issue text only. Must localize by searching.
- `memory` — issue text plus the top-5 RRF-fused prior issues, rendered as
  their titles, the files their fixes touched, and the functions inside.

**What memory stores, and what it deliberately does not.** Titles, problem
statements, changed file paths, changed symbols. **Never prior patch text.**
A store containing diffs could hand an agent a working fix for a
near-duplicate issue, and the number would then measure duplicate lookup
rather than transfer.

**Retrieval** is a port of `app/services/retrieval.py`'s `HybridRetriever` —
same Reciprocal Rank Fusion, same `RRF_K = 60`, vector leg (voyage-3-large,
1024-dim) plus lexical leg. It is a port, not a call into that class:
`HybridRetriever` needs live Postgres with pgvector and the full ontology
schema, and this pilot runs standalone. Fusion arithmetic identical, storage
not. Results here are evidence about the retrieval idea, not about that
deployment.

**Grading** is `scaleapi/SWE-bench_Pro-os`'s own entryscript ordering and its
per-instance parsers, ported in `pro_harness.py`. Resolved iff every
`FAIL_TO_PASS` passes and no `PASS_TO_PASS` breaks. Grading a benchmark with
a homegrown harness and reporting the number as that benchmark's score is
how results stop being comparable.

**Gold-patch validation runs first on every instance.** An instance whose
gold patch does not resolve is broken; both arms would fail it and the
accuracy floor would move for a reason unrelated to either. Costs 14 s.

**The agent never sees the tests.** The snapshot it explores stops at
`git reset --hard base_commit` and deliberately does *not* run
`before_repo_set_cmd`, which is what checks out the new `FAIL_TO_PASS` test
files. The grading container runs it; the agent's copy does not.

## Retrieval quality, measured before any agent ran

`retrieval_check.py`, offline, no LLM:

| | value |
|---|---|
| mean ceiling (files touched by *any* earlier issue) | 53.2% |
| instances with ceiling > 0 | 15/20 |
| mean recall@5 | 36.8% (11/20 instances hit) |
| mean recall@10 | 43.6% (13/20) |
| mean recall@20 | 50.2% (15/20) |

So retrieval at k=5 recovers ~69% of what is achievable, and k=20 is at the
ceiling. Changelog fragments are excluded — every ansible PR adds a uniquely
named one, no prior issue can have touched it, and no test checks it.

This number bounds the whole experiment: memory cannot help on the 5
instances whose files no earlier issue ever touched.

## Running it

```bash
export GENERAL_COMPUTE_API_KEY=...   # agent
export VOYAGE_API_KEY=...            # embeddings

git clone --depth 1 https://github.com/scaleapi/SWE-bench_Pro-os   # run_scripts/
git clone --filter=blob:none --no-checkout https://github.com/ansible/ansible

python profile_dataset.py                     # repo selection evidence
python select_subset.py --git-dir <ansible> --scripts-dir <pro>/run_scripts --n-eval 20
python precompute_embeddings.py --work-dir <work>
python retrieval_check.py --work-dir <work> --top-k 5 10 20
python -u run_experiment.py --scripts-dir <pro>/run_scripts --work-dir <work>
python analyze.py --results results.jsonl
```

`run_experiment.py` is resumable — `results.jsonl` is append-only and
completed instances are skipped, so an interrupt does not re-bill.

## Statistics

Paired throughout: both arms solve the same instances, so each instance is
its own control.

- **tokens, tool calls** — Wilcoxon signed-rank. Token counts are heavily
  right-skewed (a flailing episode costs 10× a clean one) so a mean-based
  test tracks whichever arm drew the worst outlier.
- **accuracy** — exact McNemar (binomial on discordant pairs). At n≈20 the
  chi-square approximation is invalid.
- **family** — Benjamini-Hochberg across the three, reusing
  `app/eval/statistics.py`'s implementation.

`welch_comparison` from that module is deliberately **not** used: Welch
assumes independent groups, and these are the same 20 instances measured
twice.

**Power, stated up front.** At n=20, exact McNemar needs roughly 6+
discordant pairs in one direction to clear p<0.05. Accuracy here will
almost certainly be underpowered, and "underpowered" is reported as a
distinct outcome from "no effect". Token cost is a paired continuous
measure and is much better powered at this n — it is the metric this pilot
can actually speak to.

## Known limits

- One repo, one model, n=20. Nothing here generalizes across repos yet.
- The memory block sits in the conversation prefix and is therefore re-paid
  on every LLM call in the episode. For the arm to win on tokens it has to
  save more exploration than that tax costs — that is a real property of
  this design, not a measurement artifact.
- `deepseek-v3.1` on a 25-step scaffold is not a frontier agent. Absolute
  resolve rates are not comparable to the SWE-bench Pro leaderboard, and
  are not offered as such. Only the paired difference is.
