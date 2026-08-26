# Observation/Event Labeling Techniques — Brief for MEASURE

**Lane:** research · **Date:** 2026-08-27 · **Task:** read STALE, TEPA,
Library-Drift+Ratchet, and AFTER specifically for observation/event labeling
technique (not just citation), and brief MEASURE on anything real to try.
**Method:** full-text read (arXiv HTML) of methodology/evaluation sections, not
abstracts alone. Pure literature work, no live model calls.
**Why this matters right now:** MEASURE's own board log already names the exact
disease this brief treats — the `semantic_label` layer of the error-floor
instrument sits at P 0/17→2/13, R 0/4→2/4 after the Task 1 terse-prompt fix, and
the residual-analysis note calls out *"gold 'continuous integration pipeline
configuration added' vs terse pred 'CI workflow configuration updated' is still
Jaccard 1/8=0.125 [synonym/abbreviation choice, not verbosity — terse-prompt
discipline cannot fix vocabulary divergence]"* (`build-board.md`, MEASURE fourth
wave). This brief is written directly against that finding, not in the abstract.

## TL;DR — one technique worth actually trying

`error_floor.py`'s `semantic_label` match rule is literal token-Jaccard ≥ 0.5
after stopword removal (`_rubric.md`:41-44) — a **lexical-overlap** metric. STALE
(arXiv:2605.06527) explicitly rejected exactly this class of metric for exactly
this reason: *"We employ an LLM judge to evaluate responses directly against the
foundational state logic **rather than against synthetic reference strings**"* —
and validated the swap, not just asserted it: *"Appendix E.3 confirms 95.8%
evaluation agreement with human judgments."* That's a real, cited number backing
a real methodological choice, not a hand-wave toward "use an LLM judge."

**Concrete, cheap adoption shape for this repo** (§1 below): keep the existing
Jaccard rubric as the default, offline-testable, zero-cost first pass (it already
works — `dep-sem-004`, `ef-sem-001` clear it fine); route only the Jaccard-failing
pairs to a single adjudicating LLM-judge call. This changes nothing about the
extractor's live cost profile (same 42-fixture pass) and adds a bounded, small
number of judge calls only on the disputed subset — not a rearchitecture.

## 1. STALE — LLM-as-judge over lexical reference-string matching (the real find)

**What they actually did, precisely:** STALE grades free-text agent responses
against ground truth by asking an LLM judge ("Gemini-3.1-flash-lite") whether the
response *demonstrates awareness of the conflict and the updated user state* —
not by string/token match against a canned reference answer. They validated this
choice against 400 human-labeled samples (95.8% agreement) rather than just
asserting an LLM judge is "close enough."

**Honest scope caveat:** STALE's judge grades *behavioral appropriateness of a
free-form response* (did the agent act on updated information), not
*label-equivalence between two short phrases* — that's a different judgment task
from ours. The transferable part is the **principle and the validation
discipline** (lexical reference-string matching produces false negatives on
correct paraphrases → swap to a judge → prove the swap didn't just trade false
negatives for false positives by checking it against humans), not a literal
prompt to copy.

**What this specifically fixes here:** the exact failure class MEASURE already
diagnosed — `ef-sem-003`'s "CI workflow configuration updated" vs gold
"continuous integration pipeline configuration added" is a *correct* label by any
reasonable human reading, scored wrong purely because Jaccard on an 8-token gold
string is brutally unforgiving of synonym choice ("CI" vs "continuous
integration", "workflow" vs "pipeline", "updated" vs "added"). No amount of
terse-prompt tuning fixes this — it's a matching-function problem, not an
extraction problem, exactly as MEASURE's own board note already concluded.

**Recommended adoption shape (concrete, not just "add an LLM judge"):**

1. Keep `error_floor.py`'s Jaccard rule exactly as-is for the first pass — it's
   free, deterministic, offline-testable, and it's already right on cases like
   `ef-sem-004` (verbatim match) and `ef-sem-001` (clears 0.5 exactly). Don't
   touch what isn't broken.
2. For gold/prediction pairs of the *same observation_type* that **fail** Jaccard
   but aren't obviously unrelated, route to ONE adjudicating judge call:
   `"Do these two short labels describe the same underlying change? A: {gold}
   B: {prediction}. Answer yes/no."` — a closed yes/no verdict, not open scoring,
   so it composes cleanly with the existing TP/FP/FN accounting (`_rubric.md`'s
   own reason-code discipline already expects a boolean match outcome per pair).
3. **Validate before trusting**, the way STALE did: hand-label a same-sized
   sample of the 42-fixture pack's semantic-layer pairs (or reuse the existing
   4-FN semantic set as a start) and check judge-vs-human agreement before this
   becomes the shipped grading standard, not just the dev-loop diagnostic.
4. **Ratchet's caution applies directly here** (arXiv:2605.22148): the paper's
   core finding is that judge error types are *asymmetric in consequence* — a
   judge that says "match" when it isn't (false positive) silently and
   irreparably corrupts the metric it's grading, while a judge that says
   "no match" when it is (false negative) just costs a little recall, recoverable
   by re-checking. That argues for over-indexing validation effort on the
   judge's **false-positive rate specifically** (does it wave through genuinely
   different labels as equivalent?), and for using a 3-call majority vote instead
   of one judge call if this ever backs a headline/public number rather than a
   dev-loop diagnostic — cheap insurance against exactly the failure mode Ratchet
   found actually corrupts a library-quality metric in production.

## 2. AFTER — a systematic refine-and-gate loop, buildable on infrastructure MEASURE already has

**What they actually did:** AFTER (arXiv:2606.23127) runs a
**Collect → Diagnose → Revise → Promote** cycle: a "reflector" LLM inspects
*failure traces*, aggregates them into named recurring error patterns ("missing
checks, brittle assumptions, incorrect tool use, incomplete output
requirements"), proposes a revised skill body, and the revision is **promoted
only if it improves held-out validation performance by a margin δ** — otherwise
kept as an inactive branch, never silently merged. One round of this measurably
moved their numbers (+3.7 to +6.7 points). The paper doesn't publish the actual
reflector prompt (checked directly — not in the main text or appendix), so this
is a *pattern to implement*, not a template to paste.

**Why this is directly actionable here, not just analogous:** `error_floor.py`'s
own rubric *already* produces exactly the input this pattern needs — every FP/FN
carries a typed reason code (`missing_no_candidate`, `key_mismatch_same_type`,
`type_mismatch_in_predictions`, `extra_no_gold`, `type_mismatch_vs_gold`,
`_rubric.md`:59-67). That's a ready-made "aggregated failure-pattern" input;
AFTER's contribution is building a *loop* around data MEASURE is already
generating, not a new data structure.

**Concrete recipe:**

1. After a live extraction pass, group the run's FP/FN rows by reason code
   (already computed by `run_error_floor.py`'s detail JSON).
2. One reflector call: feed the grouped failures (not raw traces — the reason
   codes already are the diagnosis) and ask for exactly ONE additive prompt
   change addressing the largest group, in the same spirit as the terse-label
   ruling MEASURE already did by hand for Task 1 — this automates that same
   judgment call instead of repeating it ad hoc each wave.
3. **Gate, don't merge:** re-run the full 42-fixture floor with the candidate
   prompt; require the targeted reason code's count to drop **and** no other
   type's numbers regress, mirroring AFTER's promote-by-margin rule exactly.
   Reject and keep the old prompt (an "inactive branch," in AFTER's words) if
   either condition fails.
4. This produces a citable audit trail per prompt revision (which reason codes
   triggered which change, what the before/after floor was) — matching this
   project's own evidence-trail discipline elsewhere in the codebase, applied to
   prompt engineering instead of procedure evidence.

## 3. TEPA — a real idea, but flagged as speculative, not a quick win

TEPA (arXiv:2608.07429) extracts memories as **separate key and value
functions**: `k = κ(x)`, `v = ν(x)` — a canonical identity key plus a content
value, matched by **exact key equality**, never fuzzy/embedding matching. Applied
to observation labeling, the idea would be: split `semantic_label` into a
closed-vocabulary **change-type key** (e.g. `added`/`modified`/`removed`/
`configured`/`started` × a subject noun) matched exactly and cheaply, plus a
free-text value that isn't strictly scored. This *could* sidestep the
Jaccard-vs-paraphrase problem entirely for the key half.

**Why this is NOT this brief's headline recommendation:** TEPA's own paper
admits the limit directly — exact key matching works for "preference slots,
entity attributes, and tool-regime records," and is explicitly named **"a harder
problem for open-ended memories whose conflict relation is implicit"** — which is
squarely what a free-form `semantic_label` over arbitrary SWE trace events is.
Building a closed change-type taxonomy that doesn't itself become another
under/over-fitting surface (like the FILE-family over-application MEASURE already
found) is real design work, not a prompt tweak. Worth keeping on the radar for a
later wave if the tier grows a formal taxonomy; not a next-sprint item.

## 4. Cite-only — real findings, wrong problem

- **Library-Drift** (arXiv:2605.19576) and **Ratchet** (arXiv:2605.22148) are
  about *skill-library retirement governance* (whether to keep or evict an
  already-extracted skill over time via a Critic-LLM "helped/hurt/neutral/
  inapplicable + confidence" verdict and an eviction-margin threshold) — a
  different problem from *labeling a single observation from a single trace
  event*, which is what was asked. Their one transferable idea (judge
  false-positive asymmetry) is folded into §1's caution above; the rest doesn't
  answer "how do you label an observation."
- **TEPA**'s headline system contribution is *revocation* (marking a precedent
  inactive on contradiction, preserving history for audit) — architecturally
  close to this project's own `truth_state=OUT`/tombstone spine (already noted
  in the competitive sweep), but that's a memory-lifecycle mechanism, not a
  labeling technique; only its key/value extraction split (§3) is
  labeling-adjacent.
- **AFTER**'s benchmark-construction side (382 enterprise tasks, 6 roles, 22
  skills) is evaluation-protocol content, not extraction methodology; its
  transferable contribution is entirely the refine-and-gate loop in §2.

## Source notes (what I could and couldn't verify from the paper text)

| Paper | Methodology detail confirmed from full text | What the paper does NOT show |
|---|---|---|
| STALE 2605.06527 | LLM-judge grading protocol + 95.8% human-agreement validation (Appendix E.3, per fetched text) | The judge's exact prompt wording |
| TEPA 2608.07429 | κ/ν key-value extraction formalism + exact-match conflict rule + explicit "harder for open-ended memories" limitation | Any fuzzy/embedding matching (paper states it does not use this) |
| Library-Drift 2605.19576 | Critic-LLM verdict schema (helped/hurt/neutral/inapplicable + confidence + pattern label) + contribution formula | The Critic's actual prompt text |
| Ratchet 2605.22148 | False-positive-vs-false-negative asymmetry result, eviction-margin math | — (this section was the target, fully found) |
| AFTER 2606.23127 | Collect-Diagnose-Revise-Promote cycle description + promote-by-margin rule + measured effect size | The reflector's actual diagnostic/revision prompt (checked directly — genuinely absent from the paper) |

## Links

- STALE: <https://arxiv.org/abs/2605.06527> · <https://arxiv.org/html/2605.06527v1>
- TEPA: <https://arxiv.org/abs/2608.07429> · <https://arxiv.org/html/2608.07429v2>
- Library Drift: <https://arxiv.org/abs/2605.19576> · <https://arxiv.org/html/2605.19576v3>
- Ratchet: <https://arxiv.org/abs/2605.22148> · <https://arxiv.org/html/2605.22148v3> · <https://github.com/amazon-science/Self-Evolving-Agents-Ratchet>
- AFTER: <https://arxiv.org/abs/2606.23127> · <https://arxiv.org/html/2606.23127v1>
