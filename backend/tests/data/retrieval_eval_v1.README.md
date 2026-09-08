# retrieval_eval_v1 — labelled retrieval-quality set

Purpose: derive the relevance-gate cutoff in
`app/services/relevance_gate.py` **from measurement**, and regression-test
retrieval precision. Consumed by `scripts/eval_retrieval_quality.py`.

## Files

| file | what |
|---|---|
| `retrieval_eval_v1.jsonl` | one query per line: `{query, bucket, note}` and, after labelling, `candidates: [{name, procedure_id, display_name, similarity_score, label}]` |
| `retrieval_eval_v1.candidates.jsonl` | scratch output of `--generate` (retrieved hits with `label` still to fill) |
| `retrieval_eval_v1.report.json` | output of `--measure`: the cutoff sweep + the selected cutoff |

## Relevance rubric (0–3, per retrieved procedure per query)

| label | meaning |
|---|---|
| 0 | irrelevant — wrong task or wrong domain |
| 1 | related but not useful — same area, would not actually help this query |
| 2 | useful / relevant — a reasonable answer to the query |
| 3 | excellent / direct match — this procedure is exactly what the query asks for |

`relevant := label >= 2` for precision / recall.

## Buckets

`exact_match`, `paraphrase`, `vocab_mismatch`,
`technically_related_irrelevant`, `neighboring_domain`, `generic`,
`overlapping_terms_wrong_intent`, `no_match`.

`no_match` queries have **no** good answer in the corpus; a correct
system returns zero results for them after the gate.

## Provenance of the labels

**Labels in this file were assigned by the model (Claude) acting as
annotator against the rubric above, then frozen. They are flagged for
human review.** A reviewer editing a `label` and re-running
`scripts/eval_retrieval_quality.py --measure` is how the cutoff is
revised — never by taste. Queries were written against the real ingested
procedure corpus (agent/coding skills: Azure & Twilio SDKs, React
patterns, TDD, code review, LSP setup, CodeQL, worktree isolation, lazy
tool-schema loading, …).
