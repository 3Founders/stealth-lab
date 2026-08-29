# task_002 matched A/B, 10 trials per arm (2026-08-29)

## Result

| arm | retrieval | pass (reward 1.0) |
|---|---|---|
| S | `stealthlab_procedures` | **10/10 (100%)** |
| B | `bm25` over the same KB | **1/10 (10%)** |

Fisher exact, two-sided: **p = 0.0001**.

Matched: same task, same agent model (`gemma-4-31B-it`), same user model
(`gpt-oss-120b`), same document corpus, same max-steps/timeout.
Agent temp 0.7 (NOT phaseK's 0.0 -- at 0.0 ten trials collapse into ten
identical transcripts and measure nothing). User temp 0.0 so the persona
cannot randomly volunteer the $50k/month spend, which was the confound in
the earlier single-run comparison. Terminations: `user_stop` 10/10 in both
arms -- no timeouts, no error floors, the gap is not an infrastructure
artifact.

## Why bm25 loses

Consistent failure shape. bm25 top-10 returns a plausible-looking but
wrong chunk (e.g. `doc_credit_cards_ecocard_001`, "Eligibility and
Requirements") and never surfaces the doc_007 rebate rule. The agent then
reasons correctly over incomplete premises:

    "Platinum Rewards Card ... carries a $200 annual fee, which exceeds
     your specified limit. The Gold Rewards Card provides the maximum
     return while staying well within your budget."

That is right given what it was shown. The task needs three documents
composed -- doc_002 (10% rate), doc_001 ($200 fee), doc_007 ($150 rebate
if monthly spend >= $7,500) -- and bm25 retrieves them as separate chunks
across separate calls, never joined. bm25 made 4-5 retrieval calls in the
failing trials; the substrate arm needed 1-3.

## THREE CONFOUNDS -- this is not a clean substrate-vs-retrieval result

1. **Unequal top_k.** `create_bm25_retrieval_pipeline(top_k=10)` vs
   `SubstrateSpec.top_k = 18`. The substrate arm sees ~80% more candidate
   material per call. Not controlled.
2. **Different system prompt.** bm25 uses
   `prompts/classic_rag_bm25_no_grep.md`; the substrate arm uses
   `prompts/stealthlab_procedures.md`. Different instructions, not just
   different retrieval.
3. **The substrate corpus had an offline LLM pass that bm25 did not.**
   The 690 procedures were distilled from these same documents by an LLM.
   So part of what is being measured may be "LLM-preprocessed corpus beats
   raw BM25 chunks", which is a weaker and much less novel claim than
   "verified procedural memory beats retrieval". Distinguishing these needs
   `openai_embeddings` and `golden_retrieval` arms.

## THE RESULT IS ALSO INFLATED BY THE METRIC

`reward_basis: ["DB"]`. The only check is that `apply_for_credit_card` was
called with `card_type="Platinum Rewards Card"`. Nothing scores what the
agent *told the customer*. Auditing the assistant text:

**5 of the 10 winning substrate trials state the Platinum annual fee is
$150 and never mention $200.** Ground truth (confirmed in the task data:
"blocks Platinum Rewards ($200)") is a **$200 fee with a $150 rebate**
conditional on >= $7,500 monthly spend. The procedure block handed to the
agent said `Note $200.00 annual fee` correctly -- the agent collapsed the
rebate amount into the fee amount itself. In a bank, that is a
mis-statement of price to a customer. It scores 1.0.

So: 10/10 on the benchmark, ~5/10 on "would this answer be acceptable from
a real bank". The substrate got the agent to the right *action* far more
reliably; it did not make the agent's *explanation* reliable.

## Two known substrate defects visible in the live prompt

- `ELIGIBILITY: none recorded -- standard policy applies` -- the empty
  preconditions problem, in the text the agent actually reads.
- `(verified: 10 prior successes)` on a procedure with ZERO evidence rows.
  Seeded `verification_state` is presenting to the model as earned track
  record. We are asserting something untrue.

## What would make this publishable

- `openai_embeddings` arm (isolates confound 3)
- `golden_retrieval` arm (upper bound)
- top_k equalised at 18 for bm25 (removes confound 1)
- more than one task -- n=1 task, and phaseN across 71 tasks was 2.8%
- an `nl_assertions` or communicate-check reward so the $150 error costs
  something

## Raw

`data/simulations/ten_task002_substrate/`, `ten_task002_bm25/`.
Scorer: `.claude/jobs/5dc0e89c/tmp/score_ten.py`.
