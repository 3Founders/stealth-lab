# Test map — what is verified, and what each test defends against

Every entry names the **failure it prevents**, not just the behaviour it
checks. Architecture context: [ARCHITECTURE.md](ARCHITECTURE.md).

```bash
cd backend && python -m pytest -q          # 345 passing
```

A test here exists because something silently produced a plausible wrong
answer. Where a bug was found in production, the measurement is quoted.

---

## T-1 · Harness and grading — `tests/test_pro_harness.py`

| id | test | defends against |
|---|---|---|
| **T-11** | gold resolves, empty patch does not | a harness that cannot recognise a known-correct answer makes every number meaningless. Pilot: 10/11 repos pass; NodeBB's gold breaks 6 unrelated tests |
| **T-12** | a test absent from output counts as **failure** | treating absence as a pass is the one bug that manufactures successes |
| T-13 | `pass_to_pass` breakage ⇒ not resolved | a patch that fixes the target and breaks the suite is not a fix |
| T-14 | failed apply distinguishable from wrong fix | `patch_failed` and `f2p_failed` need opposite responses |
| T-15 | dataset lists are Python reprs, not JSON | parsing as JSON yields an empty expectation set, and empty `f2p` grades as trivially resolved |
| T-16 | `PYTEST_ADDOPTS` with spaces stays quoted | unquoted, the shell exports one word and the test flags vanish |

## T-2 · Graph and retrieval — verified live against 731 nodes

| id | check | defends against |
|---|---|---|
| **T-21** | 731/731/731, zero orphans, dupes or NULL embeddings | a partially-embedded graph degrades silently to lexical-only |
| **T-22** | knowledge nodes reached by expansion (40/40 queries, 126/156 via traversal) | if they are never reached, the task/knowledge split is decorative |
| **T-23** | holdout hides at **raw node level** under both columns; restores to 731 | an instance retrieving itself turns the experiment into a lookup of the answer |
| **T-24** | `embedding_joint` is really searched (SQL captured; orderings differ) | a column flag that silently does nothing would show as "no effect" |
| T-25 | invalid column name rejected | the name is interpolated into SQL |
| T-26 | hierarchy `Group:` nodes excluded from entrypoints **and expansion** | 29% of returned nodes were aggregators, rendering `"Group: Group: …"` into prompts |
| T-27 | `PARENT_OF` edges not traversed during expansion | `expand_depth=1` reached 2 hops; 193/324 expanded nodes had no direct edge to any entrypoint and 12 came from foreign repos |
| T-28 | hierarchy build is deterministic | `_fetch_roots` had no `ORDER BY`; the same corpus built 113 vs 54 roots across runs, so no hierarchical number was reproducible |

## T-3 · HTN agent — `tests/test_htn_agent.py` (18 tests)

| id | test | defends against |
|---|---|---|
| **T-31** | returns the same shape as the flat agent | if the harness can tell them apart, flat-vs-HTN is not controlled |
| **T-32** | failed node replans **alone**; completed nodes keep edits, `attempts == 1` | re-running valid work is the whole thing localized backtracking exists to avoid |
| **T-33** | each node opens a **fresh** message list; `max(len) ≤ 2 + 2·steps_per_subgoal`; no earlier node's tool output present | this is the bound on context growth. Without it HTN is just the flat agent with extra prompts — the flat agent hit a 53K-token context and 1.07M tokens on teleport |
| **T-34** | plan parsing: JSON, fenced JSON, prose-prefixed, bullets, numbered | losing a plan to a code fence silently degrades every run |
| T-34b | a **single line of prose is rejected** as a plan | *"I'm not sure how to break this down"* would otherwise become a subgoal and look like a deliberate one-step plan instead of a planner failure |
| T-34c | self-loops and dangling deps dropped; cycles broken | either leaves a node permanently unready and hangs the scheduler |
| **T-35** | a failed node blocks only **transitive dependents**; independent branches still run | a linear plan propagates one failure to work that never depended on it |
| T-36 | blocked nodes consume **zero** budget | running a node whose prerequisite never landed edits against a state that does not exist |
| T-37 | execution follows topological order | a dependent running first builds on nothing |
| T-38 | replan attempts bounded | an unbounded retry on an impossible node eats the whole budget |
| T-39 | unusable plan degrades to one subgoal, flagged `decompose_failed` | a zero-subgoal run would "complete" having changed nothing |

## T-4 · Agent capability — `tests/test_agent_sandbox.py` (27 tests)

| id | test | defends against |
|---|---|---|
| **T-41** | `create_file` makes nested dirs; emits `new file mode` + `--- /dev/null`; `delete_file` emits `deleted file mode` | **243 of 731 instances (33.2%)** add a file. Without this they were impossible, not hard — and a malformed header makes `git apply` fail, which grades identically to a wrong answer |
| **T-42** | whitespace-tolerant match: spaces↔tabs, **nested depth preserved**, ambiguity refused, content differences still rejected | 16 edit attempts across three episodes, all rejected, in the rhythm `edit → read → edit → read`. Go is 38% of the corpus and tab-indented. The first fix for this **flattened nested blocks** → `IndentationError`, 0 tests parsed, while reporting "edited" |
| **T-43** | `\ No newline at end of file` emitted; lines not fused | difflib pieces without a trailing newline fused into `-two+TWO`; git rejected the whole patch. **127 of 4853 ansible files (2.6%)** lack a trailing newline, including the changelog fragments its gold patches always touch. Deleting one failed every time |
| T-44 | `search` sees `.go`, `.ts`, `.tsx`, `.js`, extensionless | the old allowlist was blind to **69% of gold-patch files**; in 356/731 instances not one target file was searchable. It survived because the only measured run was 9/9 ansible |
| **T-45** | every tool in `TOOLS` is dispatched, and vice versa | a tool declared but not dispatched is a capability that exists and is never used |
| T-46 | path traversal refused on read, create and delete | |

## T-5 · Scoring and statistics — verified against scipy

| id | check | defends against |
|---|---|---|
| **T-51** | McNemar matches `scipy.binomtest` across **255 combinations**; `(0,0)` returns "no test possible", not p=1.0 | reporting p=1.0 on zero discordant pairs implies evidence of no difference where there is no evidence at all |
| **T-52** | resume skips completed and gold-excluded rows but **retries** harness errors | keying on `instance_id` alone baked transient failures in permanently — an earlier file has frozen `api_error` rows |
| **T-53** | copyability 1.0 when a precedent contains the whole gold patch, 0.0 when unrelated; `+++` headers ignored | without it a memory-arm win cannot be separated from near-duplicate lookup |
| **T-54** | retrieval scoring: same-dir-different-file ⇒ `file_recall 0.0, dir_recall 1.0` | exact match alone cannot distinguish "wrong subsystem" from "neighbouring file", which need opposite responses |
| T-55 | invalid/excluded rows kept out of rates and token totals | a provider-killed episode counted as a task failure is the bug that invalidated Experiment 1 Hyp B |
| T-56 | selection: 20 instances, 10 repos, 2 each, all gold files hand-editable | protobuf/lockfile regeneration is a `make generate` task an agent fails regardless of retrieval |

## T-6 · Extraction — `tests/test_graph_ingest.py` (26 tests)

| id | test | defends against |
|---|---|---|
| T-61 | literal `\n` unescaped | **391 of 731** problem statements carry backslash-n; anything splitting on newlines saw one 3000-char line |
| T-62 | `title_of` never returns a bare label | produced **116 degenerate titles, 52 of them literally `"Title"`** — 52 nodes colliding under one name in both the lexical index and the vector space |
| T-63 | `patch_facts` extracts files and hunk symbols across Go and TS | the localization signal; empty ⇒ the knowledge node points at nothing |
| T-64 | search/replace conversion: no `@@` survives, SEARCH is byte-identical to the original | `@@ -42,7` refers to the *precedent's* file, not the one being edited |

---

## Verified outside the suite

Some properties cannot be unit-tested and were checked against reality:

- **`git apply` acceptance** — create + tolerant-edit + delete in one patch,
  applied to a real git repo, `rc=0`, all three landing correctly
- **Real container round-trip** — a `RepoSandbox` diff fed through
  `evaluate()` returns `apply_status: applied`
- **Live graph** — 731 nodes, holdout, restore, joint-column ordering
- **Provider health** — 2.3–4.8s per call at probe time

## Known limits, stated rather than hidden

- **n=20 cannot detect a doubling of the resolution rate** (power 0.041). See
  the table in [ARCHITECTURE.md](ARCHITECTURE.md#power--read-this-before-reading-any-p-value)
- Two instances (flipt, vuls) have `n_tests_parsed = 1` — one flaky test flips
  the verdict
- The flat agent's token cost is quadratic by construction; both arms bear it
  equally, so comparisons hold but absolute costs are inflated
- Copyability is indentation-insensitive and dedupes repeated lines
