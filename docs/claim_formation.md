# Claim formation: ingestion, retrieval, and the planner's `.stealth/claims.md`

Status: design proposal (2026-09-29). Derived from the leaderboard-extraction line of work researched in
`reports/Goal dedup and benchmark normalization.md` and
`research_notes/Goal dedup and benchmark normalization/leaderboard_paper_lineage.md`: Singh et al.,
"Automated Early Leaderboard Generation From Comparative Tables" (arXiv:1802.04538, ECIR 2019), then TDMS-IE,
AxCell, SciREX, ORKG leaderboards and LLM result extraction. A wider literature review on claim formation is
**postponed** (`research_notes/Goal dedup and benchmark normalization/followups.md`). Items marked
*(inference)* are our reasoning, not a finding from those papers.

## What that line of work learned, in one list

1. **A value is only reusable with its full condition.** A result is a tuple: task, dataset *and its
   version/split*, metric *and its direction*, value, and the conditions under which it was obtained.
   A number without its tuple cannot be compared or re-checked.
2. **Binding the value to the right tuple is the hardest step.** TDMS-IE, AxCell and the LLM-based
   extractors (SciLead) all find entity identification easier than attaching the correct value to the correct
   entities.
3. **Names are open-vocabulary and must be normalized.** Singh et al. met 14,947 distinct metric strings.
   AxCell's closed taxonomy plus abbreviation expansion raised top-1 linking accuracy from 42% to 56%.
4. **Classify the source before extracting.** AxCell separates leaderboard tables from ablation tables and
   irrelevant ones. What a statement *is* (a main result, an ablation, an example) decides whether it is a claim.
5. **Prune implausible values.** Singh et al. drop comparisons claiming a relative improvement above 100%
   (their REI outlier rule) before ranking.
6. **"Own result" vs "reproduced" matters.** Separating first-party numbers from re-implementations remains
   an open problem, and mixing them corrupts comparisons.
7. **Comparative evidence is sparse and biased.** Singh et al. found comparison graphs disconnected and
   tournament rankings dominated by sink nodes (more than half their top-ranked papers were sinks). Random-walk
   ranking beat tournament MLE, and later work argues for reporting *indistinguishable groups* instead of forced
   total orders when evidence is thin.
8. **Condition identity is versioned.** "SWE-bench" vs "Verified" vs "Pro"; datasets change after release. A
   claim must say which version it is about, and validity is re-checked, not permanent.

---

## A. Ingestion: which claims to keep, and how to form them

The extraction prompt (`backend/app/services/claim_extraction.py::_EXTRACTION_SYSTEM_PROMPT`) already requires a
verbatim quote, keeps hedges as conditions, and keeps repo-local facts scoped. Add these rules and fields.

### A1. Every kept claim is a bound tuple, not a sentence
Extract into slots, and keep the sentence as a rendering of the slots:

| Slot | Meaning | Example |
|---|---|---|
| `subject` | the entity the claim is about, **linked** (see A3) | `pkg:npm/docx@9`, `repo:owner/name`, `file:src/export.ts` |
| `predicate` | from a closed verb list with a synonym map | `requires`, `breaks_with`, `defaults_to`, `supports`, `measured_as`, `caused_by` |
| `object` / `value` | the other entity or the value; for a number, value **+ unit + direction** (higher/lower is better) | `node>=18`; `p95 latency 120 ms, lower is better` |
| `conditions` | every qualifier that changes truth: version, platform, config, size, environment | `["docx>=9", "Node 20", "tables > 1k rows"]` |
| `claim_kind` | see A2 | `measured` |
| `provenance` | `first_party` (the project's own docs/code), `third_party`, `reproduced` (we or a run re-established it) | `first_party` |

**Rule (lesson 2):** reject a claim whose value or pronoun cannot be bound to a named subject from the same
block ("it is faster", "this fixes the issue"). Keep it only if the subject is explicit in the quote.

### A2. Classify the statement before keeping it (lesson 4)
`claim_kind` is one of:
- `stated`: the source asserts it as true (documentation, changelog, error text);
- `measured`: a number obtained by running something, with its condition;
- `observed`: seen in code or config (a pin, a default, a signature);
- `comparative`: "A is better/faster than B under C". Store it as **two measured claims** where values exist;
  otherwise as a comparison edge with its condition (see B3);
- **not kept:** examples and illustrations, hypotheticals ("you could…"), marketing ("blazing fast"),
  ablation-style side remarks unless they state a condition, and anything that only describes the document.

### A3. Normalize names before storing (lesson 3)
- Link packages to registry ids (`pkg:npm/…`, `pkg:pypi/…`) with the alias table shared with goal dedup (the
  same entity-linking stage the dedup report recommends). Link files to repo-relative paths, and repos to
  `owner/name`.
- Map predicates and metric names onto the closed list; keep the raw string in `raw_predicate` for audit.
- Record units and direction explicitly; never infer direction from wording at retrieval time.

### A4. Sanity-check values (lesson 5)
Quarantine rather than store when:
- a number is out of range for its unit or metric (negative latency, accuracy > 100%);
- a comparative claim implies an implausible gain (reuse the >100% relative-improvement flag as a first
  heuristic *(inference: tune it on our own data)*);
- it contradicts an existing claim about the same subject + predicate + conditions. Contradictions are **kept
  as a relation** (`contradicts`) for review; they are never silently overwritten.

### A5. Keep provenance and independence (lesson 6)
- `provenance` is required. A `reproduced` claim links to the run or evidence that reproduced it.
- Evidence from the same origin shares an `independence_group` (the evidence table already has one), so ten
  copies of one blog post count once.

### A6. Keep criteria (what is worth storing)
Keep a claim only if all hold:
1. it is bound (A1) and classified as `stated`, `measured`, `observed` or `comparative` (A2);
2. it could change a decision: a precondition, applicability condition, failure mode, expected effect or
   verification (the existing `suggested_procedure_role`);
3. it is checkable: a later run, a file, or a command could support or contradict it. Store how
   (`check_hint`, e.g. `node --version`, `grep "docx" package.json`).

*(inference)* Rank what is kept by (decision relevance × checkability × provenance strength), not by how often
the sentence appears.

---

## B. Retrieval: using claims so they help

### B1. Match on condition compatibility, not just text
When `find_ways` compares a Procedure's claims with the repo's facts (`repo_claims`):
- compare **subject + predicate** after normalization, then check **conditions**: a claim about `docx>=9` is
  irrelevant (not contradicted) for a repo on `docx@8`;
- report three outcomes per claim: **holds here**, **contradicted here** (a repo fact with the same subject and
  predicate and an incompatible value), **unknown here** (no repo fact covers it). Unknown is not "fine".

### B2. Prefer claims that were checked
Order supporting claims by `provenance` (`reproduced` > `observed` > `first_party stated` > `third_party
stated`), then by independent evidence count (distinct `independence_group`s), then by recency of the last check.

### B3. Do not force rankings from thin comparisons (lesson 7)
- Comparative claims form a sparse graph. Don't rank alternatives by counting "wins": a rarely compared option
  (a sink) looks best by accident.
- Where the recommender has outcomes, use it (its hierarchical model pools evidence, which is the principled fix
  for sparsity). Where it doesn't, return alternatives as **indistinguishable** with the reason ("no comparison
  under these conditions") rather than a made-up order.

### B4. Show the condition with the claim
Every claim returned to an agent carries its conditions and `check_hint`, so the agent can see *why* it
applies and verify it cheaply. This matches the finding that knowledge helps most when it arrives ready to use
(`docs/findings.md`: the hook result) and that confident but ill-fitting snippets mislead.

### B5. Freshness
A claim whose source changed (version bump, file sha changed) is `stale` until re-checked. Stale claims are
shown as stale, never as holding.

---

## C. Guidelines for the planner writing `.stealth/claims.md`

The current line format is parsed by `find_ways` (`repo_facts.parse_repo_claims`):

```
CLAIM|<id>|<status>|<topic>|repository|<statement>|source=<path>:<line>#sha=<sha7>|version=<n>
```

Proposed additions are **extra `key=value` fields at the end of the line**, so existing parsing keeps working.
(Checked 2026-09-29: `parse_repo_claims` parses a line with these trailing fields to the same statement as
the plain line. Pin this with a test when the fields ship.)

```
CLAIM|R-014|current|deps|repository|docx is pinned to 9.x|source=package.json:23#sha=9f2c1ab|version=1|subject=pkg:npm/docx|pred=pinned_to|value=^9.1.0|kind=observed|check=grep '"docx"' package.json
```

### Rules for the planner (to go into the `survey_repo` and `plan_and_run` prompts)
1. **One checkable fact per line.** No "and"; no opinions; nothing a stranger couldn't verify from the cited
   line or the `check`.
2. **Name the subject explicitly** (`subject=`): the package id, file path or tool. Never "it" or "the
   library".
3. **Say what kind of fact it is** (`kind=`):
   - `observed`: read from a file (pins, config, scripts);
   - `measured`: produced by running a command (put the command in `check=`, and the result and unit in `value=`);
   - `stated`: said by the repo's own docs (README, AGENTS.md);
   - `absent`: searched and not found (keep the existing `source=search:<what>`).
4. **Keep conditions** that change truth (`cond=`): OS, Node or Python version, a feature flag, "in CI only".
5. **Record direction for numbers** ("lower is better") in the statement or `value`, with the unit.
6. **Never overwrite a fact that changed.** Mark the old line `stale` (as today) and append the new fact with a
   new id. If two facts conflict, keep both and add `conflicts=R-00x`, so the next agent sees the disagreement.
7. **When a step's result contradicts a fact**, update `claims.md` in the same step (mark it stale, add the
   new fact with `kind=measured` and the command), and, if it matters beyond this repo, call `report_discovery`.
8. **Prefer facts that change how work is done** (the existing 200-fact cap stays). A fact nobody's decision
   depends on is noise.
9. **No secrets, ever**: names only, as today.

---

## Proposed changes (not yet made)

| # | Change | Where | Proof it works |
|---|---|---|---|
| 1 | Extraction output gains `subject`, `predicate`, `value` (unit, direction), `claim_kind`, `provenance`, `check_hint`; reject unbound claims; add the kind classification | `claim_extraction.py` prompt + parser; additive migration for the new columns | offline tests with fixture blocks: unbound values rejected, examples and hypotheticals dropped, conditions kept |
| 2 | Name normalization: registry-linked subjects, closed predicate list + synonym map (shared alias table with goal dedup) | new normalization module | alias fixtures (docx-js → `pkg:npm/docx`) |
| 3 | Sanity and contradiction checks, with quarantine and a `contradicts` relation | ingestion pipeline | fixtures for out-of-range values and contradictions |
| 4 | Condition-aware matching with holds / contradicted / unknown per claim in `find_ways`' `repo_fit` | `repo_facts.py` and the judge input | tests: a version-conditioned claim vs repo facts on and off version |
| 5 | Retrieval ordering by provenance and independent evidence; "indistinguishable" alternatives when comparisons are thin | `find_ways` knowledge assembly | tests on a sparse comparison fixture |
| 6 | `claims.md`: optional trailing fields plus the planner rules above | `prompts.py` (`survey_repo`, `plan_and_run`), `repo_facts.parse_repo_claims` | parser accepts old and new lines; prompt tests updated |

Open questions for the postponed literature review: the claim structure (triple vs condition-bearing tuple),
validation of extracted claims, scientific claim verification methods, and how agent memories store checkable
facts.
