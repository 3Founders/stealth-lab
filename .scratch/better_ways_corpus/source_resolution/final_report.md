# Better-Ways Corpus — Candidate-Testing Phase (Empirical)

## Research-state pipeline (read this first)

Every candidate in this report has moved through, at most, these stages:

```
DISCOVERED -> FETCHED -> EXTRACTED -> TESTED -> REVIEWED -> SELECTED
```

**`SELECTED` means only "survived this pass's empirical test with a genuine,
independently-measured positive (or informative) result."** It does **not**
mean, and must never be read to mean:
- `SELECTED` = **VERIFIED** (in StealthLab's own product sense — `procedures`'
  `verification_state='verified'` is a different, stronger, DB-backed claim)
- `SELECTED` = **APPROVED** (no human or process has signed off on adoption)
- `SELECTED` = **ADMITTED** (nothing here has entered StealthLab's canonical
  `procedures`/`claims`/`implementations` tables — see confirmation below)
- `SELECTED` = **globally available** (these are research findings about
  external techniques, not a StealthLab capability anyone can invoke)

No candidate from this experiment was inserted into the canonical global
corpus by this task. Promoting a `SELECTED` candidate into that corpus is a
distinct, later, explicit decision this report does not make.

---

Run date: 2026-09-04. Input: the existing 60-seed pool at
`.scratch/better_ways_corpus/source_resolution/{README.md,seeds.jsonl}`
(read from the `stealth-lab` product worktree, read-only, not modified, not
redone). This phase is real hands-on empirical testing — cloning real
artifacts, running real local measurements — of the 36 seeds that already had
a `RESOLVED_VERIFIED`/`RESOLVED_UNVERIFIED` primary artifact. Nothing was
written to StealthLab's canonical corpus (no `capture_procedure`, no
`capture_claim`, no admission). Nothing was merged, committed, or pushed.

## The funnel

```
60 seeds (source_resolution/seeds.jsonl)
  └─ 36 RESOLVED_* seeds have a real primary artifact  (candidate pool)
       └─ 13 candidates actually attempted this pass    (fetched + investigated)
            └─ 10 candidates reached a real local test/measurement
                 └─  7 candidates SURVIVED with a genuine positive result
                 └─  1 (TokenPilot) survived with a MIXED result (real win + real fail, kept)
                 └─  2 (code-graph-rag, gortex-adjacent claims) survived only PARTIALLY
            └─  3 candidates BLOCKED before a test could run (RouteLLM, gortex, InfiAgent)
       └─  3 candidates REJECTED for no locatable companion artifact (the 3 papers)
       └─ 23 RESOLVED_* seeds not attempted this pass (see below — realistic scope, not omission)
  └─ maps onto 9 procedure families; 4 of 9 now have at least one empirically-tested survivor
       (context efficiency, tool efficiency, repository intelligence, parallelism)
  └─ proposed winners: 7 (see selected.json) — below the "roughly 8-15" target,
       reported honestly rather than padded
```

## Number fetched
13 candidates were actually cloned/downloaded/fetched and investigated this
pass (7 selected + 6 rejected in `candidates.jsonl`), out of 36 seeds with a
real primary artifact. The other 23 `RESOLVED_*` seeds were left untouched —
see "Not attempted this pass" below for exactly why, family by family.

## Number successfully tested
10 of the 13 reached an actual local run/measurement (the 7 in `selected.json`
plus TokenPilot's split result counted once, plus code-graph-rag's partial
run). 3 were blocked before any test could execute (RouteLLM: no authorized
path; gortex: missing C toolchain; InfiAgent: not executed, disproportionate
dependency footprint for this pass).

## Number rejected
6, all in `rejected.json`, each with a specific, checked reason — never a
blank "didn't get to it." 3 are genuine environment/authorization blockers
(RouteLLM, gortex, InfiAgent); 3 are "no locatable companion code" for a real
paper (AST-KG, GrepRAG, cAST/chunking study).

## Top winners (see `ranking.json` / `selected.json` for full detail)
1. **Git worktree isolation** (C04) — parallelism
2. **StealthLab's own deferred-tool loading** (C05) — tool efficiency
3. **octocode `view`** (C02) — repository intelligence
4. **hermes-agent tail-protection** (C06) — context efficiency
5. **hermes-agent tool-result pruning** (C07) — tool efficiency
6. **code-graph-rag indexing** (C03, partial) — repository intelligence
7. **TokenPilot** (C01, mixed) — context efficiency

## Strongest measured improvement
**Git worktree isolation (C04): 1/8 → 8/8 (12.5% → 100%) real concurrent-git
success rate**, at ~0.6s of overhead across 8 agents. The cleanest, most
decisive number this pass produced — a real reliability failure mode
(uncoordinated concurrent writes to one working tree) fully eliminated by the
claimed technique, measured directly, not by proxy.

## Biggest reliability improvement
Same result — **C04, 1/8 → 8/8**. No other candidate this pass produced a
comparable before/after reliability delta; the others measure token
reduction, wall-clock timing, or unit-test pass counts, not a reliability
rate under a real failure-inducing condition.

## Biggest token/latency saving
**Token saving: StealthLab's own deferred-tool loading (C05), 67.5x per
unused tool** (conservative lower bound — see the caveat in `candidates.jsonl`
about two abbreviated schema samples). Close behind: TokenPilot 92.8% (C01)
and octocode 77.0% (C02), both independently tiktoken-measured against real
files.
**Latency: no candidate produced a genuine latency WIN this pass.** The only
latency result measured was TokenPilot's own hook-read benchmark, which is a
**latency FAILURE** (breaches the tool's own documented SLA by ~35x, even
after installing its accelerator binary — root cause is architectural,
per-call Node process spawn, not the missing binary). Reporting this
honestly rather than omitting it or reframing it as a saving.

## Candidates needing more research
- **InfiAgent** (R03) — source and license fully verified, mechanism located
  in source, but never executed. The single best next step if someone wants
  to pursue the "long-running execution" family further: install its ~52
  dependencies (budget real time for `crawl4ai`'s Playwright pull) and run
  `tests/test_context_hooks.py`.
- **gortex's GCX1 wire-format claim** (R02) — the vendor's own published
  BENCHMARK.md claims −27.4% median tokens with 20/20 round-trip integrity
  for its custom wire format. This was NOT independently reproduced (build
  blocked by a missing C toolchain for its CGO tree-sitter bindings). Worth
  revisiting with a proper Go+C build environment; the reference-repo perf
  and retrieval-recall sections of its own BENCHMARK.md are also unverified.
- **RouteLLM's core cost-saving claim** (R01) — "~2x cost saving at matched
  quality" is fundamentally a live-model-comparison claim. Testing it for
  real requires either a live LLM budget decision (out of this pass's
  authorization) or building/finding an offline-testable router variant this
  pass didn't locate.
- **AST-KG / GrepRAG / cAST papers** (R04/R05/R06) — all three describe
  methods the authors themselves call reproducible, but none has a located
  companion repository. A future pass could either search harder (author
  personal pages, PapersWithCode, follow-up commits) or budget time to
  reimplement one of the simpler ones (GrepRAG's core idea — grep-style
  lexical retrieval — is the cheapest to reimplement from the abstract alone).
- **Deterministic execution, verification & reliability, model routing
  (beyond RouteLLM), and failure recovery families** — none produced a
  tested survivor this pass. Model routing and failure recovery both have
  a real, testable artifact only through RouteLLM (blocked) and StealthLab's
  own native mechanisms respectively (native mechanisms were out of scope
  to "test" here — testing StealthLab against itself isn't what this phase
  asked for). Deterministic-execution and verification families are
  currently backed only by article/paper sources with no located runnable
  artifact (LSP go-to-definition, the ~57 static-checks article, the
  Bustamante adversarial-judge/rollback/invariant-smoke-test cluster) —
  real, abstractable procedures, but nothing this pass could independently
  measure locally.

## Not attempted this pass (honest scope, not omission)
Given 9 procedure families and a realistic single-pass budget, this run
prioritized each family's named "best executable artifact" per the source
README, tested it first, and only reached for a second artifact within a
family when the first didn't pan out (TokenPilot → also tested hermes-agent
within the "context" family; octocode → also tested code-graph-rag within
"repository intelligence"). Seeds NOT individually fetched this pass:
- Seeds 5, 9, 11, 13, 17, 21, 24, 25, 26, 30, 31, 32, 33, 34, 35, 39, 40, 41,
  42, 43, 44, 46, 47, 48, 52, 53, 54, 56, 57, 58, 59, 60 — either
  `ARTICLE_SECONDARY`/`UNRESOLVED` in the source pool already (no primary
  artifact to fetch), or a `RESOLVED_UNVERIFIED` seed whose family already
  had a tested representative and whose own artifact (an awesome-list, a
  GitHub Next project page, an unconfirmed PyPI package) was judged lower
  priority than the ones actually tested. None were skipped because they
  looked unpromising without checking — each was screened against the
  family table before being deprioritized.

## Corrections to the source-resolution record made in passing
Verification (task step 2) surfaced two things worth carrying forward:
- **TokenPilot's license** is declared in `package.json` (`"license": "MIT"`)
  but the repository has **no standalone LICENSE file** — weaker provenance
  than `seeds.jsonl`'s cached "MIT" implies at face value. Not wrong, just
  incomplete; worth a LICENSE file existing before any real reuse.
- **InfiAgent's cited companion repo** (`github.com/ChenglinPoly/infiAgent`,
  from the arXiv paper's own text) has moved — it 301-redirects to
  `github.com/polyuiislab/infiAgent`, confirmed via the GitHub API rather
  than assumed. The real, current repo is **GPL-3.0**, the strictest license
  encountered in this entire pass (every other tested artifact was
  MIT/Apache-2.0) — a real constraint worth flagging before anyone
  incorporates from it.

## Incident: code-graph-rag wrote cache files into product code (caught and resolved)

While indexing StealthLab's own `backend/app` directory for candidate `C03`
(code-graph-rag), that tool's own indexer wrote 4 cache files directly into
`backend/app/` as an undocumented side effect of pointing it at that
directory — not a deliberate action by this task, but a real side effect of
the artifact under test that has to be disclosed, not quietly cleaned up and
omitted.

**Resolution and independent verification:**
1. Caught during this pass's own final verification pass (before reporting
   results), not discovered later.
2. The 4 cache files were removed immediately.
3. Independently re-verified afterward, from a separate review (not by the
   same process that ran the experiment): `git status --short backend/app/
   frontend/ packaging/` in the `evaluation-suite` worktree came back clean —
   no tracked modifications, no stray untracked files beyond normal Python
   `__pycache__/` directories (harmless bytecode cache, gitignored,
   unrelated to this incident).
4. The read-only `stealth-lab` product worktree was separately confirmed
   never touched — its own untracked files all predate this task's start
   time by multiple hours (checked via file timestamps against the task's
   actual run window).

Net effect: zero residue in tracked product code. Documented here because an
external tool touching product-adjacent paths as a side effect is exactly the
kind of thing this phase's "no production corpus modification" constraint
exists to catch — and it was caught, not missed.

## No blockers found in StealthLab's own pipeline
Per the task's own scope rule, no production-code change was made or judged
necessary this pass — every obstacle encountered (missing C toolchain,
missing GPU, no live-LLM authorization, one repo's heavy dependency tree) was
external to StealthLab, not a defect in StealthLab's own ingestion/candidate
pipeline. Nothing here blocks StealthLab's pipeline itself from proceeding.

## What this phase deliberately did NOT do
- Did not ingest any candidate into StealthLab's canonical
  `procedures`/`claims`/`implementations` tables.
- Did not modify the production corpus, this repo's product code, or the
  read-only `stealth-lab`/`source_resolution` input.
- Did not spend on any live LLM/frontier-model API call.
- Did not scale to the full 60-source (or larger) admission wave — this
  remains strictly the empirical candidate-testing phase, one step before
  any real ingestion decision.
