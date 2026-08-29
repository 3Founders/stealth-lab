# Founding loop, hand-audited on real dogfooding data — ROADMAP Band 2 exit bullet 1

**Date:** 2026-08-29 · **Lane:** infra · **DB:** `localhost:5432/postgres` (dev)
**References:** `.scratch/research/band2-founding-loop-exit-criterion-review.md`,
`BAND2_CLOSURE_REVIEW.md`, `ROADMAP.md` § "Band 2 exit criteria" bullet 1.

## Verdict

**Bullet 1 now closes — but only after this session fixed the input, and the chain
is not yet self-sustaining.** Read both halves before treating it as done.

ROADMAP bullet 1 asks for: *"a live session flows trace → episode → observation →
claim → procedure candidate, hand-audited at each hop."* That chain now exists with
real ids on real dogfooding data, reproduced below. It did **not** exist when this
audit started, and the reason it did not is more useful than the fact that it now does.

## Part A — what the founder's question actually found (state at audit start)

The board logged this as *"real Claude Code hook traces + Terminal-Bench tasks have
been running since 2026-08-27."* Traces were indeed being **collected**. None of them
had ever been **ingested**.

| Table | Rows at audit start | What they actually were |
|---|---|---|
| `trace_events` | 1,390 | **Not dogfooding data.** Two synthetic script runs: `precond-populate-20260826-000810` (700 events, `tool_name=populate_preconditions`) and `distill-banking-20260824-150601` (690, `distill_policy`). Every event in each run carries one identical timestamp — batch-written by a script, not a live session. `project_id` NULL on both. |
| `episodes` | 11 | 6 real ones from session `0afa5712-…` (genuine `trace_worker/episode_assembly.v1` output) + 5 hand-seeded `visitor-local` rows. |
| `observations` | **0** | — |
| `observation_events` | **0** | — |
| `knowledge_nodes` `node_type='claim'` | **0** | — |
| `episode_links` | **0** | — |
| `claim_sources` | **0** | — |
| `ingestion_jobs` | 62, all `done` | **Test fixtures from 2026-08-20**, payloads `dedup_key: "ingjob-test-dedup-1"`, `"nonexistent"`. Zero of their `trace_event_id`s exist in `trace_events` — these 62 "done" jobs processed nothing. |
| `procedures` | 700 | 698 `tau3 banking_knowledge corpus seed (synthetic verification)` + 2 null-provenance candidates. |

The decisive number: **`episodes.session_id ∩ trace_events.session_id = 0`.** The two
halves of the pipeline were reading disjoint session sets and had never met.

This is exactly the mistake the exit-criterion review warned about — 1,390 rows and
11 episodes look like a working loop at a glance. They were not one. **"Lots of real
trace volume" was not "the loop is exercised."**

Meanwhile the real data was sitting on disk untouched: 14 hook-trace files in
`.claude/traces/` (~14.8 MB, sessions through 2026-08-29) and 47 Claude Code
transcripts (~122 MB) in `~/.claude/projects/C--Users-chait-Prog-3Found-Stealth-StealthLab/`.

## Part B — the fix was to run the pipeline, not to change code

No production code was modified to close this. Two existing scripts were run against
the real data.

**Module 5 — `run_ingestion.py --once --trace-dir ..\.claude\traces`**

```
files_processed 14
collector  records_seen 4333 · inserted 4333 · skipped_duplicate 0 · quarantined 0
```
Drained to completion over repeated passes (final pass `claimed: 0`). Idempotency
confirmed on re-run: `inserted 0 · skipped_duplicate 4341`.

Result: `trace_events` 1,390 → **5,731** across **16 real sessions**. Real event mix,
not batch-written — `PreToolUse/Bash` 1345, `PostToolUse/Bash` 1295, `Read` 402,
`Edit` 336, `Grep` 196, `Write` 130, spanning 2026-08-20 → 2026-08-29.

`observations` 0 → **3,106**, all `deterministic_v1@1`:
`command_executed` 2484 · `file_touched` 462 · `test_run` 152 · `commit_made` 8.

**Module 6 — `ingest_transcripts.py`**

```
run 1:  parents_inserted 106 · children_inserted 0 · skipped_existing 0   (largest session)
run 2:  parents_inserted 0   · children_inserted 0 · skipped_existing 106
```
Replay contract holds. `episodes` 11 → **1,187** across 48 sessions.
Session overlap 0 → **10**.

**Privacy check (Module 6's own requirement):** exactly 5 episodes have
`content IS NOT NULL`, and all 5 are the pre-existing hand-seeded `visitor-local`
rows. **Every one of the 1,182 assembled episodes has `content IS NULL`** — only
`content_ref` locators and structural metadata. Passes.

**Metadata double-encode check:** unwrap-depth histogram across all 1,187 episodes is
`{1: 1181, 2: 6}`. Depth 1 is correct. The 6 depth-2 rows are the stale `0afa5712-…`
episodes from 2026-08-15/16 that predate the fix. **No newly written episode is
double-encoded** — the fix holds on live data.

## Part C — the hand-audited chain, real ids

Every hop below was executed against the dev DB and read back. Nothing seeded.

**Hop 0 — raw collector file**
`.claude/traces/5dc0e89c-d2ae-4f8c-9212-1931d2907b0d.jsonl` (this session's own hook output)

**Hop 1 — `trace_events`**
```
id          86f38820-29a4-42a3-abf5-60700872982a
trace_id    5dc0e89c-d2ae-4f8c-9212-1931d2907b0d
session_id  5dc0e89c-d2ae-4f8c-9212-1931d2907b0d
sequence    135
event_type  PostToolUse      tool_name  Write
timestamp   2026-08-29 07:23:02.145170+00:00
dedup_key   6560e07f5d81df64fd433ce0ec4fa306e5e20c32b7447f8d23742c918ad7e839
```

**Hop 2 — `episodes`, via `assemble_episodes()`** — resolved by calling the real
`resolve_justification_episode(pool, observation_id)`, not by hand-picking:
```
id                 a08548b1-4fdc-427f-b912-c3c7ced852be
session_id         5dc0e89c-d2ae-4f8c-9212-1931d2907b0d
parent_episode_id  None
content IS NULL    True                     <-- privacy holds
content_ref        5dc0e89c-….jsonl#main:1875:2031
span               2026-08-29 07:18:32.411+00 .. 07:24:22.379+00
metadata.segmenter "trace_worker/episode_assembly.v1"    <-- real pipeline, not seeded
metadata.n_events  156
metadata.assembly_fingerprint 0f844a631c419b8121873b6337a67c18cf52092d2aafa009bde183d3ae1d54b2
```
The `segmenter` + `assembly_fingerprint` + `rules` block is what distinguishes a
genuinely assembled episode from a seeded row. This is the hop the previous
"founding loop" tests skipped entirely (they used `str(uuid.uuid4())`).

**Hop 3 — `observations`**
```
id                42bbf38c-7716-4bc8-8970-4cad62dfd4e6
observation_type  file_touched
extractor         deterministic_v1  code_version 1
label             Modified C:\Users\chait\.claude\jobs\5dc0e89c\tmp\check3.py
```

**Hop 4 — `knowledge_nodes` claim**, via the real
`handle_promote_observation_to_claim()` (claims 0 → 1):
```
id          182d6278-0f03-4550-a1e5-f46cff671300
provenance  company_ingested
t_valid     2026-08-29 07:26:47.154582+00:00   t_invalid  None
properties  {"statement": "Modified C:\\…\\check3.py",
             "claim_type": "file_touched",
             "promoted_by": "claim_promotion@1",
             "truth_state": "IN",
             "epistemic_status": "observed",
             "extraction_version": "deterministic_v1:1"}
claim_sources  observation_id 42bbf38c-7716-4bc8-8970-4cad62dfd4e6
episode_links  episode_id a08548b1-4fdc-427f-b912-c3c7ced852be (target_table knowledge_nodes)
```
Both provenance links exist — the claim is traceable back to its observation *and*
its justifying episode.

**Hop 5 — `procedures` candidate**, via the real `extract_procedure()` with
`SessionEvidenceSource` over the same session and episode:
```
evidence: 52 observations, 139-tool sequence, outcome success
validators: [] (no failures)  extracted_by deterministic_v1@1

id                  01a04c6a-edcb-7e21-b74f-eb3ef2efc63f
procedure_id        7be517ca-084c-4f5f-af8e-9f533cbd9818
verification_state  candidate
approval_status     proposed
provenance          system_pending_review
extracted_by        deterministic_v1@1
source_episode_ids  [a08548b1-4fdc-427f-b912-c3c7ced852be]    <-- the SAME episode
steps               39
preconditions       0
```

`source_episode_ids` closes the loop back to hop 2 by id. **Chain complete.**

## Part D — what this does NOT close. Read before citing it.

1. **The chain is not automatic. Hops 4 and 5 were invoked by hand.**
   There is an **ordering dependency with no recovery path**: trace ingestion ran
   before episode assembly, so all 3,106 `promote_observation_to_claim` jobs resolved
   `justification_episode_id = NULL`, completed as `done`, and produced **zero**
   claims. Once assembly ran and the episodes existed, nothing re-enqueued them.
   That is why the DB shows **1 claim, not ~3,106** — the one I promoted manually.
   *There is no backfill or re-enqueue path for observations whose episode arrived
   late.* This is the single highest-value follow-up.

2. **`extract_procedure()` has no caller in the ingestion pipeline.** Hop 5 exists
   and works, but nothing in `run_ingestion.py` or the job handlers ever calls it.
   It runs from `solve_task` and from a human. Claim → procedure is not wired as a job.

3. **The claim's semantic content is the observation label verbatim.** `statement`
   is `"Modified C:\…\check3.py"`. Structurally a real claim with real provenance;
   semantically near-worthless. The chain is proven; the *value* of what flows
   through it is not.

4. **Hop 5 passed V4 only because I chose a goal naming no file or command.**
   `capability_statement` is set to `goal_text` verbatim by `deterministic_v1`, and
   V4 rejects any evidence token appearing there. This is the known, documented,
   unfixed gap (`procedure_extraction/**`, CORE-B). A goal that names a file still
   fails. Do not read "validators: []" as that gap being fixed.

5. **Not a fresh DB.** ROADMAP's framing is "the database currently contains zero
   inhabitants." This ran against the long-lived dev instance alongside 698
   pre-existing seeded procedures. The chain is real; the *cold-start* framing of
   bullet 1 is still better served by `bootstrap_demo.py` on a fresh volume.

6. **The procedure is `candidate` with zero evidence** — correct and expected, but it
   means the outcome→capability half (bullet 2) is untouched by this.

## Recommendation

- Tick ROADMAP Band 2 exit bullet 1 as **closed with the caveats in Part D**, citing
  the ids above. Amend `BAND2_CLOSURE_REVIEW.md` per the review's option 1 so the
  scorecard stops implying it was covered all along.
- Open a follow-up for Part D item 1 (re-enqueue after late episode assembly) — that
  is what stands between "one hand-audited chain" and "the loop runs itself."
- Do **not** cite this as evidence the substrate produces *useful* knowledge. It
  proves the plumbing carries real data end to end. That is exactly what bullet 1
  asked for, and no more.
