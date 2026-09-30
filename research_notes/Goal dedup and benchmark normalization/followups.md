# Follow-ups (postponed 2026-09-28)

## Claim formation at ingestion + `.stealth/claims.md` (literature review needed)

Take from arXiv:1802.04538 (Singh et al., ECIR 2019) and its lineage (TDMS-IE, AxCell, SciREX, ORKG
leaderboards, LLM result extraction) **how claims should be formed during ingestion so they stay useful
later**. Their core lesson: a result is only reusable when it is stored as a normalized tuple with its full
condition (task, dataset/version/split, metric + direction, score, harness/k, source and "own vs reproduced"),
and attaching the value to the right tuple is the hardest step.

Questions for the review:
- What structure should an ingested Claim have (subject/predicate/object vs a condition-bearing result tuple),
  which fields make it comparable and re-checkable later, and how are such claims validated and deduplicated?
- How should the repo-facts file `.stealth/claims.md` (written by the `survey_repo` prompt) be shaped so its
  facts link to those Claims (stable ids, `source=file:line#sha`, topics), survive code changes, and feed
  applicability judging?
- Prior work on claim/fact extraction for knowledge bases, scientific claim verification, and agent memory
  that stores verifiable facts.

Status: postponed by the user. Not started.
