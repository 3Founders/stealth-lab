# Experiment Results — Consolidated

All runs on the local Docker `pgvector/pgvector:pg17` instance (port 5433), against the real
shipped code paths. Raw outputs in `experiments/after/results_*.json` (moved from `backend/`).

Status: Experiments 1A, 4, 5, 5b, 6, 7 complete. SWE-bench Pro harness validated (10/11 repos).
Experiment 1B stopped mid-run. Experiment 3 queued.
Experiment 2 was cut partly because its Hypothesis B tests precondition matching — **that reason
is now obsolete**: `backend/app/services/precondition_gate.py` exists and is wired into
`batch_hierarchical_search` and `resolve_subtask_reuse`. Hypothesis B is testable again. The other
reason (EvoMemBench and CL-Bench absent from this machine) still stands.

---

## Headline

**All four testable claims are falsified or heavily qualified. None survives.** Experiment 5's
cost saving — the one positive result — was retracted by its own audit (5b): the comparison count
was real, but comparisons were never where the cost lived.

The most useful output is not any number. It is **eight production defects**, every one of which
fails *silently* into a plausible wrong answer rather than an error. Two of them (the search
allowlist, the `.env` resolution) would each have invalidated an entire class of future run
without producing a single error message.

---

## Experiment 1A — task-level retrieval (n=89 single-skill tasks, 22-skill library)

| Arm | p@1 | recall@3 | recall@5 | comparisons |
|---|---|---|---|---|
| A0 lexical (`_lexical_overlap`) | 0.674 | 0.820 | 0.910 | 22.0 |
| **A1 flat vector (voyage-3-large)** | **0.787** | **0.899** | **0.966** | 22.0 |
| A2 hierarchical (beam descent) | 0.528 | — | — | 26.8 |

- A1 > A0: McNemar **p = 0.041**. Vector retrieval earns its API cost.
- A2 < A1: McNemar **p = 0.000002** (24 flat wins vs 1). Hierarchical is *both* less accurate and
  more expensive than an exhaustive scan of 22 items. Zero low-confidence aborts, so this is
  confident misrouting, not the tree declining to answer.

**Verdict: the reusability claim fails at this scale.** See Experiment 5 — this does not generalise.

---

## Experiment 5 — does hierarchy pay off at scale? (same 89 queries, corpus 22 → 1,555)

Corpus grown three ways with labels preserved: chunked skill documents, auxiliary skill files, and
731 real SWE-bench Pro issue reports as distractors. Grouping threshold fixed in advance as the
p90 of each corpus's own pairwise cosine, so the crossover cannot be manufactured by tuning.

| corpus | nodes | flat p@1 | hier p@1 | flat cmp | hier cmp | cost ratio | McNemar p |
|---|---|---|---|---|---|---|---|
| skills only | 22 | 0.652 | 0.494 | 22.0 | 22.7 | 1.03× | **0.00012** |
| + chunks | 259 | 0.652 | 0.640 | 259.0 | 57.5 | 0.22× | 1.000 |
| + some aux | 524 | 0.652 | 0.607 | 524.0 | 71.6 | 0.14× | 0.289 |
| all labelled | 824 | 0.640 | 0.640 | 824.0 | 87.3 | **0.11×** | 1.000 |
| + 731 distractors | 1,555 | 0.618 | 0.405 | 1,555.0 | 99.2 | **0.06×** | **0.00031** |

1. **Cost claim: RETRACTED — see Experiment 5b.** Comparisons grow 22.7 → 99.2 while the corpus
   grows 22 → 1,555, which looks like a 15.7× saving. It is not one. The flat figure in the table
   above was *assumed* (`flat_cmp.append(len(nodes))`), not measured, and when the real cost is
   measured hierarchical search is **5–12× slower**.
2. **Accuracy parity in a middle band.** At 259–824 nodes there is no significant difference
   (p = 1.00, 0.29, 1.00) — flat-equivalent accuracy at 4–9× fewer comparisons.
3. **Collapses on a heterogeneous corpus.** Adding realistic irrelevant content costs hierarchical
   a third of its accuracy (0.640 → 0.405) while flat barely moves (0.640 → 0.618).

**Verdict: falsified on both axes.** The accuracy parity is real but buys nothing, because the
cost saving it was supposed to pay for does not exist (5b).

---

## Experiment 5b — cost audit: the saving does not exist

Experiment 5's headline compared a *measured* tree cost against an *assumed* flat cost. Audited
with `EXPLAIN (ANALYZE)`, wall-clock timing, and a round-trip counter:

| corpus | nodes | flat as written | flat index-friendly | hierarchical | hier round trips |
|---|---|---|---|---|---|
| skills only | 22 | 2.64 ms | 2.53 ms | 23.07 ms | 11.0 |
| all labelled | 824 | 4.38 ms | 2.22 ms | 24.23 ms | 13.0 |
| + distractors | 1,555 | 12.17 ms | **5.43 ms** | **65.02 ms** | 14.7 |

**Hierarchical search is 5.3–8.7× slower than flat, and ~12× slower than a correctly indexed flat
query at 1,555 nodes.**

Why the comparison count misled: the tree makes **11–14.7 sequential database round trips** (one
per descent level, plus a children lookup each), against flat's **1**. At these corpus sizes cost
is dominated by round-trip latency, not by distance arithmetic — 1,555 distance computations in
one indexed query is far cheaper than 99 comparisons spread across 15 network hops. The comparison
count was never wrong; it was simply never the cost.

For the tree to win, per-comparison cost would have to dominate per-round-trip cost — millions of
vectors, or expensive comparisons (cross-encoder / LLM routing), or in-process traversal rather
than 15 network hops. None hold here.

*Caveat:* Experiment 5 uses `mxbai-embed-large` locally (Voyage's free tier cannot embed 1,555
documents in reasonable time), so absolute p@1 is below 1A's and the two are not cross-comparable.
Every arm within Experiment 5 shares the embedder, so the comparisons above hold. The 824- and
1,555-node builds produced 12 and 18 roots rather than 1, so descent starts wider than ideal.

---

## Experiment 6 — SOTA retrieval methods, self-implemented (same 22-skill library, 89 queries)

BM25 validated against `rank-bm25` before use: Pearson **0.990**, top-1 agreement **39/40**.

| method | p@1 | recall@3 | recall@5 | p vs dense |
|---|---|---|---|---|
| **bm25** | **0.809** | 0.921 | 0.966 | 0.774 |
| **rrf(dense + bm25)** | **0.809** | **0.955** | **0.978** | 0.625 |
| dense voyage-3-large | 0.786 | 0.899 | 0.966 | — |
| mmr(dense, λ=0.7) | 0.786 | 0.888 | 0.966 | 1.000 |
| hyde → dense | 0.764 | 0.910 | 0.933 | 0.625 |
| jaccard (codebase path) | 0.753 | 0.888 | 0.933 | 0.581 |
| hyde + dense rrf | 0.753 | 0.899 | 0.966 | 0.250 |
| **cross-encoder rerank** | **0.528** | 0.809 | 0.910 | **0.00012** |

- **A free, offline BM25 matches or beats the paid embedding API.** Not significant alone
  (p = 0.77), but it never rate-limits and costs nothing.
- **Ship `rrf(dense + bm25)`**: same top-1, clearly best recall@3 and @5.
- **The codebase's lexical fallback is leaving ~13 points on the table.** `_lexical_overlap` is
  Jaccard set overlap — no term saturation, no length normalisation. It scores 0.674 in 1A where
  proper BM25 scores 0.809 on the same data. This is the path every vector call silently degrades
  to on failure.
- **Cross-encoder reranking actively hurts** (the only significant row). `ms-marco-MiniLM` is
  trained on short passage ranking; these are 2,000-char documents against long instructions.
- **HyDE does not help here.** The two techniques most likely to be added on reputation are the
  two that lose.

---

## Experiment 4 — SLM trajectory transfer, token cost (n=20 composite tasks per arm)

| Arm | condition | prompt | completion | total |
|---|---|---|---|---|
| SLM 8B — Groq `llama-3.1-8b-instant` | no context | 880 | 731 | 1,611 |
| | + context | 2,119 | 744 | **2,863** (1.78×) |
| Large 120B — GC `gpt-oss-120b` | no context | 922 | 2,675 | 3,596 |
| | + context | 2,162 | 2,638 | **4,800** (1.33×) |

Context costs ~1,240 prompt tokens and buys back nothing measurable: completion deltas −12.6
(Wilcoxon p = 0.59) and +36.8 (p = 0.28).

**Verdict: falsified.** Graph context is a cost, not a saving.

*Caveats:* the 120B ignored `max_tokens=900` (reasoning tokens are not bound by the visible-output
cap), so absolute completions are not comparable across arms — within-arm comparisons are valid.
The 8B has mild right-censoring (4/20 and 6/20 rows at the cap). Accuracy is not reported at all:
**AFTER ships zero validators**, so no objective pass/fail exists on this substrate, and an
LLM-judge substitute would not support the original "closes the gap to frontier" claim.

---

## Experiment 7 — can every other golden patch localize this bug? (n=731, leave-one-out)

For each SWE-bench Pro instance, retrieve from the golden patches of all 730 others. Query = the
issue's `problem_statement`. Correct = a retrieved patch touches a file the current gold patch
also touches. No execution, no generation — pure retrieval, which is what makes it gradeable here.

**scope = all** (candidates are all 730 others — repo must be inferred, not given):

| method | hit@1 | hit@3 | hit@5 | file_recall@5 | file_prec@5 | same-repo@5 | p vs popularity |
|---|---|---|---|---|---|---|---|
| random | 0.014 | 0.045 | 0.074 | 0.021 | 0.005 | 0.097 | — |
| **popularity** (query-blind) | 0.099 | 0.109 | 0.122 | 0.048 | 0.005 | 0.116 | — |
| bm25 | 0.419 | 0.564 | 0.614 | 0.345 | 0.080 | 0.617 | <1e-6 |
| dense | **0.467** | 0.591 | 0.635 | 0.367 | 0.086 | 0.699 | <1e-6 |
| **rrf(dense+bm25)** | 0.465 | **0.606** | **0.652** | **0.375** | 0.091 | 0.705 | <1e-6 |

**scope = repo** (oracle repo routing — candidates restricted to the same repository):

| method | hit@1 | hit@3 | hit@5 | file_recall@5 |
|---|---|---|---|---|
| random | 0.134 | 0.298 | 0.404 | 0.161 |
| **popularity** | 0.297 | 0.442 | 0.501 | 0.224 |
| bm25 | 0.498 | 0.639 | 0.702 | 0.400 |
| dense | 0.514 | 0.652 | 0.700 | 0.402 |
| **rrf(dense+bm25)** | **0.521** | **0.662** | **0.711** | **0.406** |

**Verdict: supported, and the effect is large.** `rrf` reaches hit@5 = 0.652 against a query-blind
popularity control of 0.122 — a 5.3× improvement, p < 1e-6 at every k. This is the first
experiment in the series where the memory premise holds.

Three things worth extracting:

1. **The popularity control was necessary and it changes the reading.** Within a single repo,
   "just return the most frequently patched files" already gets hit@5 = 0.501 — hot files carry
   half the signal. Across the full corpus it collapses to 0.122. Any within-repo result reported
   without this control would be roughly half artefact.
2. **Retrieval does its own repo routing.** With no repo hint, 70–82% of retrieved patches come
   from the correct repository, and handing the retriever an oracle repo filter only lifts hit@5
   from 0.652 to 0.711. Cross-repo confusion costs ~6 points, not the bulk of the error.
3. **It is a lead, not a solution.** file_recall@5 = 0.375 (most gold files still missed) and
   file_precision@5 = 0.091 (22 files offered, ~2 relevant). Useful as a localization prior for a
   coding agent; not a substitute for search.

This validates specifically the `KNOWLEDGE_UPDATION_EXPERIMENT.md` design decision to **index
rather than duplicate** — store where the fix landed, not the diff. Location is what transfers.

---

## Eight production defects found

Every one fails silently — producing a plausible answer rather than an error. That pattern, not
any individual bug, is the finding.

| # | Defect | Consequence |
|---|---|---|
| 1 | `.env` `GENERAL_COMPUTE_BASE_URL` missing `/v1` | Every panel call 404s |
| 2 | `build_hierarchy_for_table` ignored its own `threshold` arg | `_pairwise_similarity` hardcoded the default; every sweep value built an identical tree |
| 3 | `resolve_subtask_reuse` had no flat fallback | A tree that declined to route recorded *nothing* — identical to "no reusable component exists" |
| 4 | `decompose()` discarded `SubtaskReuseReport.candidates` | Similarity scores computed then thrown away; threshold miscalibration invisible |
| 5 | `find_reusable_nodes` returned hierarchy group nodes | `/v1/decompose` recommended `"Group: statistics, Group: docx…"` as reusable work |
| 6 | `RetrievalResult.as_context()` unbounded by document size | Up to 25 full descriptions inlined → a **28,595-token** request from one `/v1/decompose` call, rejected by a 12K TPM ceiling |
| 7 | `_vector_candidates` `ORDER BY similarity DESC` | Defeated the HNSW index — a sort on a computed alias cannot use it. 14.78 ms → **1.52 ms** once ordered by `embedding <=> $1` |
| 8 | `config.py` resolved `.env` against the **CWD**, not the package | Every script launched from the repo root got a fully-default `Settings` — every secret `None` — surfacing only at the first API call, i.e. after the expensive part had already run |

Plus one defect introduced and caught during this work: the flat fallback added for #3 bypassed the
Rule 1 precondition gate, letting a match the gate deliberately blocked return via `method:'flat'`.
Strictly worse than having no fallback — the tree appears to reject an incompatible procedure while
the flat path silently reinstates it. Caught by `test_precondition_gate_blocks_a_match_end_to_end`.

All fixed. **289 tests pass** (250 at the start of this work, +17 agent-sandbox, +26 graph-ingest,
−4 from a removed orphan module).

### Calibration finding

`FULL_MATCH_THRESHOLD = 0.90` **sits above the entire observed similarity distribution** on this
corpus — measured twice, independently:

- skill ↔ skill pairwise cosine: min 0.390, **max 0.758**, median 0.585
- subtask ↔ skill (101 real comparisons): **max 0.821**, mean 0.474, median 0.453

So `resolve_subtask_reuse` at its default threshold can never fire here. A single-threshold run
would have returned zero matches on all 40 tasks and read as "the mechanism does not work."

---

## What this means for the thesis

The original plan's claim was that reusability comes from *structurally-matched,
precondition/postcondition-typed* procedures rather than name or embedding match. When these
experiments were first written no such mechanism existed. **It exists now** —
`precondition_gate.py` implements a Jaccard postcondition gate (threshold 0.25) applied inside
`batch_hierarchical_search` and, since this round, inside the flat fallback too. The claim is
therefore testable for the first time, and has not yet been tested.

Measured honestly against the mechanisms that *were* exercised:

- Hierarchical retrieval **loses on accuracy** (0.528 vs 0.787 flat, p=0.000002) and wins **only on
  comparison count** — which 5b showed is not the cost.
- Hierarchical search is **5.3–8.7× slower** than flat in wall-clock time; the round trips dominate.
- A free BM25 implementation **beats the paid embedding path** (0.809 vs 0.786).
- Graph context **increases** token cost (+17.6% in the agent run) with no measurable return.

These are publishable negative results with clean paired statistics. **No positive claim survives**
— the ~15× comparison saving previously stated here was retracted by Experiment 5b and should not
be cited.

The one strongly positive retrieval result is Experiment 7: leave-one-out gold-patch localization
at hit@5 0.652 versus a 0.122 popularity baseline (p<1e-6). That is retrieval quality, not
end-to-end task performance, and the two have now been shown to be different questions — see below.

---

## SWE-bench Pro: harness validated, and what the agent run actually says

**Harness pilot — 10/11 repos grade their own gold patch correctly** (go 4/4, python 3/3, ts 1/1,
js 2/3). Tutanota 107/107 f2p and teleport 43/43 are the strong signals: large expectation sets
parsed and matched exactly across different toolchains. The lone failure is NodeBB, where the
*reference* patch passes all 3 f2p and then breaks 6 unrelated tests in `test/user/emails.js` —
an ungradeable instance, not a hard one. `run_experiment.py` already gates on gold-first, so such
rows self-exclude before any agent tokens are spent.

Grading cost is ~340 s per container run, three runs per ablation instance → **~15–20 min per
instance** before any agent time. A 50-instance ablation is most of a day.

**The agent (deepseek-v3.1, 25 steps, n=9, all ansible): 1/9 resolved in both arms.**

| | no_memory | with memory |
|---|---|---|
| resolved | 1/9 (11.1%) | 1/9 (11.1%) |
| total tokens | 1,258,684 | 1,480,513 (**+17.6%**) |
| edited a **correct** file | **7/9** | 5/9 |
| edited only wrong files | **0/9** | 1/9 |
| no patch at all | 2/9 | 3/9 |

**Zero discordant pairs.** Both arms solved the same instance and failed the same eight, so McNemar
has no input — this is not "no significant difference", it is no information.

**The mechanism behind that null is visible in the file-level data: localization is not the
bottleneck.** The agent reaches a correct file in 7 of 9 runs and edits a wrong-file-only in 0 of 9,
yet 8 of 9 fail. The failure is in the *content* of the change, not its location. (An earlier draft
attributed this to editing too few files; the patch-size data refutes that — the one instance that
resolved edited a single file against a four-file gold patch, at the same 0.153 size ratio as the
median failure. File count and patch size do not separate success from failure.)

This predicts that a memory arm supplying **file locations** has near-zero headroom, and that
scaling *n* would yield a precise null rather than a difference. A memory arm supplying **what a
similar fix had to satisfy** — `requirements`, `interface` — targets the part that is actually
failing. Both are now in the graph.

**Search was blind to 69% of the corpus.** `RepoSandbox.search` filtered candidate files through an
extension allowlist containing no `.go`, `.ts`, `.tsx` or `.js` — 2,556 of 3,705 gold-patch files
invisible, and in **356 of 731 instances not one file needing the edit was searchable**. Go alone
is 38.3% of the corpus. It survived undetected because the only measured run was 9/9 ansible, where
`.py` and `.yml` are both on the list. Now a binary denylist plus a NUL-byte sniff, with 17
regression tests parametrized over all four languages.

---

## The graph ingestion (in progress)

All 731 gold patches ingested into the real backend graph (`stealthlab_swebench`), replacing the
standalone flat store in `knowledge.py` — which was a faithful RRF port but had no task/knowledge
split, no edges, no hierarchy and no bi-temporal validity, and whose own docstring says a result
from it is evidence about rank fusion rather than about this system.

- **task_node** per instance — title, problem statement + requirements + interface,
  `issue_categories`/`issue_specificity` kept structured, `fail_to_pass` in `success_criteria`
- **knowledge_node** per instance — `code_location`: files and symbols from the gold diff, plus the
  interface contract
- **edge** — `OWNS`/`RESOLVED_AT` between them, so hybrid retrieval matches the *similar problem*
  and one-hop traversal supplies the *answer location*

Leave-one-out is performed by setting `t_invalid` (the TMS mechanism) rather than re-ingesting, and
the HTN tree is rebuilt after each holdout because internal nodes route on the mean of their
children's embeddings — a tree built while the held-out leaf was live has that leaf folded into its
parent's routing signal. Verified: 731 → 730 across all three tables, held-out rows invisible to
every filtered read, fully restorable. The runner aborts if a held-out instance retrieves itself.

Three extraction defects found and fixed on the way, each of which produced plausible-looking
garbage rather than an error: no `issue_title` column exists; **391 of 731** problem statements
carry literal `\n` instead of newlines; and title extraction yielded **116 degenerate titles, 52 of
them the bare string "Title"** — 52 nodes colliding under one name in both the lexical index and
the vector space. Now 0 degenerate, covered by 26 tests.

### The agent could not do a third of the corpus — capability audit

Every SWE-bench result recorded before this section was produced by an agent **missing tools the
task required**. Audited against all 731 gold patches:

| requirement | instances | agent could do it? |
|---|---|---|
| **create a new file** | **243 (33.2%)** | **NO** — `edit_file` returned `"not a file"` and nothing else wrote to disk |
| **delete a file** | 18 (2.5%) | **NO** |
| rename a file | 10 (1.4%) | only as create + delete |
| edits past line 250 | 32.7% of hunks | yes (`read_file` takes `start_line`) |
| file mode / binary / symlink | 0 | n/a |

**6 of the 20 selected experiment instances required a new file.** They were unwinnable in both
arms regardless of model, retriever or rendering — the hidden test imports a module the agent had
no way to author. element-web failing identically across all six configurations (deepseek/gemma ×
split/joint × unified-diff/SEARCH-REPLACE) was not noise; it was structural.

They also poisoned the ablation: 30% of the sample producing **guaranteed concordant failures**,
contributing zero discordant pairs — the only quantity McNemar reads.

`create_file` and `delete_file` added, with git headers (`new file mode`, `--- /dev/null`,
`deleted file mode`) verified against real `git apply`, not just unit tests. A malformed header
makes a patch fail to apply, and **a patch that fails to apply grades identically to a wrong
answer** — the fix would have silently done nothing.

### Four defects found by independent verification agents

Three subagents checked execution, retrieval and scoring separately. Two of the four defects were
in code written the same day, and both were worse than what they replaced.

| # | defect | consequence |
|---|---|---|
| 1 | whitespace-tolerant `edit_file` computed depth as `len(rel) // len(guessed_unit)`, and the "unit" was the shallowest absolute indent | **flattened every nested line** inside any method. Produced `IndentationError`, **0 tests parsed**, while reporting `"edited"`. Fixed by reading depth off a ladder of the snippet's own distinct indent widths — no unit guess needed |
| 2 | `diff()` never emitted `\ No newline at end of file` | difflib pieces without a trailing newline **fused** (`-two+TWO`); `git apply` rejected the whole patch. **127 of 4853 ansible files (2.6%)** lack a trailing newline, including the changelog fragments its gold patches always touch. Deleting one failed every time |
| 3 | `expand_depth=1` reached **two hops** — `traverse_from`'s frontier holds depth 0 *and* 1 — and rode `PARENT_OF` edges | expansion walked entrypoint → hierarchy group → **sibling**. 193 of 324 expanded nodes (60%) had no direct edge to any entrypoint; **12 came from repos no entrypoint belonged to** and, having real instance ids, survived into the rendered memory block |
| 4 | expansion did not filter hierarchy aggregators | **215 of 739 returned nodes (29%)** were `Group:` nodes, burning context budget and rendering `"Group: Group: Address parsing normalizes"` into prompts verbatim |

Excluding groups at *entrypoint selection* had fixed only half of #4; the expansion leg was still
open. Also fixed: resume keyed on `instance_id` alone, so a transient harness error was baked in
permanently and never retried — which is how `graph_experiment_1.jsonl` acquired frozen
`api_error` rows.

Verified PASS: gold round-trip (5/5 F2P, empty fails), a test that never ran counts as failure,
`TOOLS` ↔ `_dispatch` agree exactly, graph integrity 731/731/731 with zero orphans, knowledge
nodes genuinely reached by traversal (40/40 queries), holdout hides at raw node level under both
embedding columns, McNemar matches scipy across 255 combinations, and invalid/excluded rows are
correctly kept out of resolution rates.

### The n=20 design cannot detect the effect it was built to measure

McNemar's exact test floors at `2/2^k` for k discordant pairs, so **k≥6 all in one direction** is
required for p<0.05. Discordant pairs cannot exceed the instances solved by exactly one arm.

Power at a 10% baseline (optimistic — assumes independent arms):

| n | +10pp | +20pp | +30pp | +40pp |
|---|---|---|---|---|
| **20** | **0.003** | **0.041** | 0.188 | 0.435 |
| 40 | 0.017 | 0.146 | 0.505 | 0.839 |
| 120 | 0.029 | 0.508 | 0.968 | 1.000 |

**At n=20 a doubling of the resolution rate is detected 4.1% of the time.** For 80% power:
n=215 (+10pp), n=71 (+20pp), n=37 (+30pp). At ~10 h per 20 instances, n=71 is ~36 h.

The 10% baseline was, however, measured with the broken agent described above. The current run is
therefore a **pilot to measure the post-fix resolution rate**, which is what determines whether the
real experiment is n=37 or n=215.

---

### Joint issue+diff embeddings beat issue-only — SIGNIFICANT (n=400, p=0.0066)

The first positive result in this document that survives a properly powered test.

Two representations of the same 731 instances, same corpus, same queries, leave-one-out:

- `embedding` — title + problem statement (the issue alone)
- `embedding_joint` — title + problem statement + **the gold diff that fixed it**

| metric | split (issue) | joint (issue+diff) | delta |
|---|---|---|---|
| file_recall | 0.3983 | **0.4135** | +0.015 |
| dir_recall | 0.6156 | **0.6404** | +0.025 |
| same_repo_rate | 0.8092 | **0.8355** | +0.026 |
| hit_any_file | 0.6825 | **0.7175** | +0.035 |

**Paired sign test on hit_any_file: joint better on 19 queries, worse on 5, tied on 376.
p = 0.00661** over 24 discordant queries.

Real rather than lucky: at n=100 the same comparison gave 7:1 and p=0.070; at 4× the sample
the effect sizes held while p fell an order of magnitude. A fluke shrinks when data is added.

**Mechanism.** Issue-only vectors cannot separate two senses of a domain word. On the flipt
instance, *"Flipt Fails to Authenticate with AWS ECR Registries"* (registry login,
`internal/oci/ecr/*`) outranked *"Authentication cookies are not cleared"* (request middleware)
for a query about auth middleware — both problem statements share the vocabulary and only the
diffs distinguish them. The 376 ties matter here: the representation does not reorder most
queries, it changes the minority where the issue text is genuinely ambiguous. That is why the
mean deltas are small while the paired test is decisive.

**Caveat, and it is not small.** This measures RETRIEVAL QUALITY, not task resolution. The
flipt run already showed the two diverge — right package, wrong file, agent failed anyway — and
the localization finding below says better pointing is necessary but not sufficient.

**Method note.** Vector-only, not RRF: HybridRetriever's lexical leg is identical under both
columns and fusing it would dilute the difference being measured. Leave-one-out is done by
excluding the query's own instance id in SQL rather than by setting `t_invalid`, so the
comparison is read-only and safe to run while another experiment owns the graph's mutable state.

---

### First graph-backed run (n=1, flipt-io/flipt, Go) — retrieval good, ablation VOID

Held-out instance: *"Authentication middleware does not support client tokens via cookies"*, whose
gold patch touches exactly one hand-editable file. Gold resolved, so the instance is gradeable.

| | value |
|---|---|
| file_recall | **0.0** |
| dir_recall | **1.0** (`internal/server/auth`) |
| deepest shared path prefix | 3 segments |
| same_repo_rate | 1.0 |
| retrieval latency | 1.0 s |

**Right repo, right package, wrong file.** The exact-match miss is a content problem, not a ranking
one: both retrieval legs lock onto the token "authenticat…" and rank *"Flipt Fails to Authenticate
with AWS ECR Registries"* (registry login, `internal/oci/ecr/*`) above *"Authentication cookies are
not cleared after unauthenticated response"* — which is the same mechanism as the held-out issue.
A bag-of-words/embedding match cannot separate two senses of a domain word. That is precisely the
case the precondition/postcondition gate exists for, and it is now testable.

**Both arms are INVALID** (`valid: false`, `provider_error_truncated_episode`). A General Compute
400 (`type: provider_error`) killed each episode around step 34 of 40, after one edit and before
`finish`.

| | no_memory | graph_memory |
|---|---|---|
| tools used | 34 | 37 |
| of which `search` | **27** | **23** |
| `edit_file` | 1 | 3 |
| tokens | 197,099 | 245,230 |
| file edited | gold file | gold file |

Characterised but not solved: the captured payload 400s on every replay; 68 messages pass and 70
fail; it survives replacing the edit arguments, shortening the tool result and renaming the
`tool_call_id`; yet a *synthetic* tool pair appended to the same 68 messages passes, and 100
synthetic turns at 80K tokens pass. Failing payloads are saved as
`experiments/swebench_pro/failed_request_*.json`. **Until an episode can survive this, no
multi-instance ablation is meaningful** — every run truncates at the same place.

**The finding that does survive**: both arms edited `internal/server/auth/middleware.go`, the only
gold file, and the no-memory arm did so with no memory at all. Combined with the ansible run
(correct file in 7/9, wrong-file-only in 0/9), that is four independent confirmations that
**localization is not the bottleneck**. The tool mix says the same thing from the other side —
27 of 34 calls were `search`, against four file reads and one edit. The agent is not failing to
find the code; it is failing to stop looking and to write a correct change once there.
