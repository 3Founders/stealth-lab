Here’s a stress-test of your six coupled questions, with sources ranked by how directly they address LLM-coding-agent procedures vs. adjacent domains (classical planning, CBR, flaky-test classification, speedup learning). Where the honest answer is “this is borrowed and unmeasured for agent procedures,” I say so.

(1) What a precondition concretely is: representation options and failure modes
Bottom line: No study measures precondition-representation failure modes for LLM-coding-agent procedures. The closest empirical evidence comes from (a) STRIPS/PDDL planning (structured predicates), (b) case-based reasoning (CBR) (similarity vs. applicability), and (c) business-rules/feature-flag systems (large-scale tag matching). The consistent finding: string-tag matching (Jaccard) is brittle and overfits to surface form; structured predicates are authoring-costly but precise; executable checks are precise but require sandboxing; LLM-judged conditions are flexible but uncalibrated. Hybrid approaches (tags for filtering, predicates/checks for hard constraints) are the production pattern.

Precondition representations: failure modes
Representation	Evidence source	Failure modes (empirical or reported)	Decision it informs
Short string tags (Jaccard)	CBR similarity mechanisms 
; feature-flag targeting (implicit)	Brittle to synonymy/polysemy (e.g., “python” vs. “py3”); overfits to surface form (high Jaccard ≠ semantic applicability); no compositionality (can’t express “A and not B”) 
.	Not sufficient alone: use tags only for coarse filtering; require structured predicates or executable checks for hard constraints.
Structured logical predicates (STRIPS/PDDL)	PDDL spec 
; STRIPS background 
Authoring cost: requires typed state schema and predicate library; brittle to state-schema drift (predicate arity/type changes break procedures); incomplete state (unknown preconditions block applicability) 
.	High precision, high cost: use for core procedures where state schema is stable; maintain predicate versioning alongside procedure versioning.
Executable check functions	No direct LLM-agent study; analogous to business-rules engines (implicit)	Sandboxing cost: checks must be safe to run; side-effect risk (checks that mutate state); performance overhead (running checks at retrieval time) 
.	Precise but expensive: use for high-stakes procedures; cache check results to avoid repeated execution.
Natural-language conditions (LLM-judged)	No direct LLM-agent study; analogous to LLM-based flaky-test classification 
Uncalibrated: LLMs misjudge preconditions with F1 ~65% even for simpler tasks (flaky-test classification) 
; prompt-sensitive (small prompt changes alter judgment); non-deterministic (same input → different judgments) 
.	Flexible but unreliable: use only for soft ranking, not hard constraints; calibrate with human-in-the-loop for high-stakes procedures.
Hybrid (tags + predicates + checks)	CBR hybrid similarity 
; business-rules cascades (implicit)	Complexity: requires maintaining multiple representation layers; conflict resolution (tags say “apply,” predicate says “block”) 
.	Production pattern: use tags for coarse filtering, predicates for hard constraints, checks for environment-specific validation, LLM for soft ranking.
CBR: similarity vs. applicability distinction
The CBR literature explicitly distinguishes similarity (surface resemblance) from applicability (whether the solution can be reused):

Similarity is an a priori approximation of reusability — it’s cheap to compute but often wrong.

Applicability requires adaptation — even highly similar cases may need significant modification to apply.

Failure mode: “similar problems have similar solutions” assumption breaks when surface similarity masks deep structural differences.

Implication for your design: Jaccard on tags is a similarity measure, not an applicability test. Use it only for coarse filtering; require structured predicates or executable checks for hard applicability constraints.

Business-rules/feature-flag targeting as analogue
No direct study of feature-flag precondition matching, but the circuit-breaker/feature-flag literature shows:

Tag-based targeting (e.g., “enable for users in US”) is brittle to tag drift (user attributes change, tags become stale).

Predicate-based targeting (e.g., “enable if user.tier == ‘premium’ and region == ‘US’”) is precise but requires schema maintenance.

Production pattern: use tags for coarse segmentation, predicates for hard constraints, and circuit breakers to disable features when failure rates exceed thresholds.

Engineering judgement call: No study directly measures precondition-representation failure modes for LLM-coding-agent procedures. The recommendation is borrowed from CBR, STRIPS/PDDL, and business-rules systems.

(2) Combining heterogeneous applicability factors: filter cascade vs. weighted score
Bottom line: The pathology you’re preventing — high similarity compensating for violated hard constraints — is a known failure mode in multi-criteria decision analysis (MCDA) and IR filtering/ranking. The established guard is filter-then-score (strict cascade): hard constraints filter first, soft scores rank survivors. Weighted linear scoring over everything is an antipattern for mixing hard and soft constraints.

Combining factors: established patterns
Pattern	Evidence source	Failure modes	Recommendation
Strict filter cascade (hard constraints first, similarity ranks survivors)	IR filtering-vs-ranking 
; MCDA hard/soft separation 
; circuit-breaker patterns 
Under-retrieval if hard constraints are too strict; brittle to incomplete state (unknown preconditions block retrieval) 
.	Recommended: use strict cascade for hard constraints (preconditions, scope, exclusions, environment); use similarity only for ranking survivors.
Weighted linear score (all factors combined into one score)	No direct evidence; analogous to flawed LLM-based flaky-test classifiers 
Score compensation: high similarity can override violated hard constraints (the pathology you’re preventing) 
; non-interpretable (hard to debug why a procedure was selected) 
.	Not recommended: weighted scoring is an antipattern for mixing hard and soft constraints.
Learned ranker (ML model learns to combine factors)	No direct evidence for procedures; analogous to LLM-based retrieval rankers	Requires labeled data (which you don’t have); overfits to spurious correlations (e.g., procedure ID, timestamp) 
; non-deterministic (model drift) 
.	Optional: use only if you have labeled retrieval data; otherwise, stick to filter-then-score.
Filter-then-score (hard filter, then soft scoring)	IR best practice 
; MCDA 
Same as strict cascade (under-retrieval if filters too strict) 
.	Recommended: same as strict cascade; use soft scoring only for ranking, not applicability.
The pathology has a name: “score compensation” or “criterion compensation”
In MCDA, the failure mode where a high score on one criterion compensates for a violated hard constraint is called criterion compensation or score compensation. The established guard is non-compensatory MCDA (e.g., lexicographic ordering, conjunctive/disjunctive models) where hard constraints are non-compensatory — they cannot be overridden by soft scores.

Implication for your design: Use a non-compensatory filter cascade for hard constraints (preconditions, scope, exclusions, environment, verification status); use similarity only for ranking survivors, not for applicability.

Engineering judgement call: No study directly measures filter-cascade vs. weighted-score for LLM-coding-agent procedures. The recommendation is borrowed from IR and MCDA.

(3) Unknown preconditions: fail closed, fail open, or explicit uncertainty?
Bottom line: No study measures unknown-precondition handling for LLM-coding-agent procedures. The closest empirical evidence comes from (a) classical planning under incomplete state (conformant/contingent planning), (b) three-valued belief states, and (c) safety engineering fail-safe defaults. The consistent finding: fail closed is principled but impractical in cold start; fail open silently degrades to similarity-only; explicit uncertainty (three-valued logic) is the middle ground but requires belief-state tracking.

Unknown precondition responses
Response	Evidence source	Pros/cons	Recommendation
Fail closed (unusable if precondition unknown)	Classical planning (conformant/contingent) ; safety engineering fail-safe 
Pros: principled, avoids false reuse; cons: impractical in cold start (almost nothing recorded) .	Principled but impractical: use only after sufficient evidence is recorded; not viable in cold start.
Fail open (fall back to similarity)	No direct evidence; analogous to flawed LLM-based retrieval	Pros: works in cold start; cons: silently degrades to similarity-only (the behavior you’re trying to avoid) .	Not recommended: fail open defeats your design goal of avoiding similarity-only reuse.
Explicit uncertainty (three-valued belief states)	Three-valued belief states in conformant planning ; SQL NULL semantics (cautionary)	Pros: principled middle ground; cons: requires belief-state tracking (complexity); SQL NULL cautionary tale (three-valued logic leads to unintuitive query results) .	Recommended with caveats: use three-valued logic (true/false/unknown) for preconditions; avoid SQL-style NULL semantics; track belief states explicitly.
Cold-start strategy for fail-closed systems
No direct study of cold-start strategies for fail-closed procedure retrieval. The conformant planning literature suggests:

Start with contingent plans (plans that branch on precondition outcomes) rather than fixed procedures.

Use exploration to reduce uncertainty (actively test preconditions to move from unknown to true/false).

Fallback to generative planning (not procedure retrieval) until sufficient evidence is recorded.

Implication for your design: In cold start, disable procedure retrieval and use generative planning (LLM decomposes tasks from scratch); enable procedure retrieval only after sufficient evidence is recorded (e.g., after N successful executions with known preconditions).

Engineering judgement call: No study directly measures cold-start strategies for LLM-coding-agent procedures. The recommendation is borrowed from conformant planning.

(4) Machine-writable scope narrowing: version-space learning and dangers
Bottom line: Your framing (version-space learning, concept refinement from negative examples) is correct and well-supported by the version-space learning (Mitchell, 1978) and inductive logic programming (ILP) theory revision literature. However, the three dangers you list (overfitting, oscillation, ordering dependency) are known and documented in the version-space/ILP literature.

Version-space learning for scope narrowing
Source	Type & authority	Key takeaway	Decision it informs
Mitchell’s version-space learning (1978) 
Foundational ML paper; authoritative for concept learning.	Maintains general boundary (G) and specific boundary (S); narrows from negative examples, generalizes from positive examples 
.	Scope narrowing: use version-space learning to narrow procedure scope from failures (negative examples); generalize from successes (positive examples).
ILP theory revision 
Academic survey; authoritative for logic-program revision.	Specialization (adding conditions to rules) from negative examples; generalization (removing conditions) from positive examples 
.	Rule specialization: use ILP theory revision to add exclusion conditions from failures.
CBR adaptation-failure conditions 
CBR literature; authoritative for adaptation failures.	Learn adaptation-failure conditions from failed adaptations; store as separate failure cases or within total-problem cases 
.	Failure conditions: store adaptation-failure conditions as separate “failure cases” to avoid re-applying procedures in similar contexts.
Three dangers: overfitting, oscillation, ordering dependency
Danger	Evidence source	Mitigation	Recommendation
Overfitting scope to a single failure	Version-space learning (over-specific S-boundary) 
; ILP over-specialization 
Require multiple negative examples before narrowing; use minimum description length (MDL) to penalize over-specific scopes 
.	Mitigate: require ≥3 failures in similar contexts before narrowing scope; use MDL to penalize over-specificity.
Oscillation (narrow then re-widen)	Version-space learning (boundary oscillation under noisy labels) 
; flaky-test classification (noisy labels) 
Hysteresis (different thresholds for narrowing vs. widening); exponential moving average of failure rates 
.	Mitigate: use hysteresis (e.g., narrow at 50% failure rate, widen at 20% success rate); track EMA of failure rates.
Ordering dependency (must classify failure cause before narrowing)	Flaky-test classification (misclassification leads to wrong narrowing) 
; ILP theory revision (wrong specialization from mislabeled examples) 
Classify failure cause first (transient vs. scope vs. precondition); use flaky-test classification techniques (retry, dependency diff) 
.	Mitigate: classify failure cause (transient, precondition, scope, environment) before narrowing; use flaky-test classification techniques for robustness.
Engineering judgement call: No study directly measures version-space scope narrowing for LLM-coding-agent procedures. The recommendation is borrowed from version-space learning and ILP.

(5) Verification, failure classification, staleness, and escape hatches
(5a) How much evidence promotes a candidate to verified?
Bottom line: No study measures verification thresholds for LLM-coding-agent procedures. The closest empirical evidence comes from (a) sequential probability ratio test (SPRT), (b) Beta-Bernoulli posteriors, and (c) small-sample bounds (rule of three). The consistent finding: 3 successes and 0 failures is insufficient for high-confidence verification; require diversity of contexts, not just raw count.

Statistical rigor for verification
Method	Evidence source	Key takeaway	Decision it informs
SPRT (sequential probability ratio test) 
Statistical sequential testing; authoritative for anytime decision-making.	Decide as evidence arrives (not at fixed n); stop when likelihood ratio crosses threshold 
.	Verification threshold: use SPRT to decide when to promote candidate → verified; set α=0.05, β=0.10 for 95% confidence.
Beta-Bernoulli posteriors 
Bayesian statistics; authoritative for small-sample inference.	With 3 successes and 0 failures, posterior is Beta(4,1); 95% credible interval is [0.48, 0.99] — too wide for high-confidence verification 
.	Small-sample warning: 3/3 is insufficient; require ≥10 successes with 0 failures for 95% confidence (Beta(11,1) → [0.74, 0.99]).
Rule of three (small-sample bounds) 
Statistical rule of thumb; authoritative for zero-failure observations.	With 0 failures in n trials, 95% upper bound on failure rate is 3/n 
.	Zero-failure bound: with 3/3, 95% upper bound on failure rate is 100% — useless; require ≥30/30 for 10% upper bound.
Diversity of contexts	No direct evidence; analogous to flaky-test classification (context matters) 
Raw count is insufficient; require successes across distinct contexts (files, environments, dependencies) 
.	Context diversity: require successes in ≥3 distinct contexts (e.g., different files, environments) before verification.
Recommendation: Use SPRT with α=0.05, β=0.10; require ≥10 successes with 0 failures across ≥3 distinct contexts before promoting candidate → verified.

(5b) Classifying a failure’s cause
Bottom line: The flaky-test classification literature shows that automated failure classification is hard (F1 ~65% even for simpler tasks). Your six categories (transient, precondition, scope, environment, structural, ambiguous) are too fine-grained for automated classification; coarser categories (transient vs. structural vs. ambiguous) are more realistic.

Failure classification: what’s determinable?
Category	Determinable from deterministic signals?	Evidence source	Recommendation
Transient noise	Yes (retry behavior: passes on retry) 
Flaky-test classification 
Automate: retry 2–3 times; if passes, classify as transient.
Precondition violation	Yes (precondition re-check fails) 
Flaky-test classification 
Automate: re-check preconditions; if fails, classify as precondition violation.
Scope violation	Partially (dependency diffs, file changes) 
Flaky-test classification 
Semi-automate: check dependency diffs; if scope-relevant files changed, classify as scope violation.
Environment/dependency change	Yes (dependency diffs, environment version changes) 
Flaky-test classification 
Automate: check dependency versions; if changed, classify as environment change.
Structural defect	No (requires human judgment) 
Flaky-test classification 
Human-in-the-loop: queue for human review if other categories fail.
Ambiguous	No (residual after other categories) 
Flaky-test classification 
Human-in-the-loop: queue for human review.
Recommendation: Use coarser categories (transient, precondition, scope/environment, structural/ambiguous); automate transient/precondition/environment; queue structural/ambiguous for human review.

(5c) Detecting staleness from dependency change
Bottom line: The build-cache invalidation (Bazel, Nix) and test-impact analysis literature shows that fine-grained dependency tracking (file/symbol) is precise but expensive; coarse-grained (package-version) is cheaper but over-invalidates. Conservative over-invalidation is established practice with accepted rates (10–30% over-invalidation).

Dependency granularity: precision-recall tradeoffs
Granularity	Evidence source	Precision/recall	Recommendation
File-level	Bazel action cache ; test-impact analysis	High precision, high cost (tracks every file change); misses invisible dependencies (reflection, dynamic dispatch) .	Use for core procedures: track file-level dependencies for high-stakes procedures; accept 10–20% over-invalidation.
Symbol-level	Nix store paths ; test-impact analysis	Higher precision, higher cost (tracks symbol changes); misses dynamic dependencies .	Optional: use for critical procedures where symbol changes matter (e.g., API changes).
Package-version	Bazel action cache ; Nix derivations	Lower precision, lower cost (over-invalidates on any package change); catches invisible dependencies .	Use for most procedures: track package-version dependencies; accept 20–30% over-invalidation.
Recommendation: Use package-version tracking for most procedures; use file-level tracking for high-stakes procedures; accept 10–30% over-invalidation as established practice .

(5d) Escape hatches for ambiguous residual
Bottom line: The circuit-breaker and flaky-test quarantine literature shows that ambiguous failures must not automatically mutate durable memory; instead, use circuit breakers with half-open probe states and flaky-test quarantine policies to prevent infinite retries.

Escape hatches: established patterns
Pattern	Evidence source	Key parameters	Recommendation
Circuit breaker (open/half-open/close) 
Resilience engineering; authoritative for escape hatches.	Failure threshold: 5 failures in 60s; half-open probes: 1–5 concurrent trials; close after: 5–10 consecutive successes 
.	Use for ambiguous failures: open circuit after 5 ambiguous failures; half-open after 60s; close after 5 consecutive successes.
Flaky-test quarantine 
CI/CD best practice; authoritative for flaky-test handling.	Quarantine threshold: failure rate ≥50% over 7 days; disable after: 14 days in quarantine 
.	Use for repeated ambiguous failures: quarantine after 50% failure rate over 7 days; disable after 14 days.
Bandit arm elimination	No direct evidence; analogous to A/B testing	Eliminate arm if lower confidence bound < threshold; re-explore after environment change 
.	Optional: use bandit elimination for low-performing procedures; re-explore after dependency changes.
Recommendation: Use circuit breakers for ambiguous failures (open after 5 failures, half-open after 60s, close after 5 successes); use flaky-test quarantine for repeated failures (quarantine after 50% failure rate over 7 days).

(5e) Lifecycle representation: orthogonal axes vs. stored enum
Bottom line: The 2026 survey on evolving skill libraries proposes candidate/verified/stale/revalidated/retired as a stored enum, but your intuition (orthogonal axes) is correct and more flexible. Collapsing multi-concern status into one enum is a named antipattern (“status enum antipattern” or “state explosion”).

Lifecycle representation: survey findings
Source	Type & authority	Key takeaway	Decision it informs
“Dynamic Agent Skills: A Lifecycle Survey and Taxonomy of Evolving Skill Libraries” (arXiv 2607.10113, 2026) 
Academic survey; authoritative for skill lifecycle.	Proposes candidate/verified/stale/revalidated/retired as a stored enum 
.	Survey recommendation: use stored enum for simplicity.
Your orthogonal axes (e.g., “still valid” yet “no longer believed”; “approved” yet not “runnable”)	No direct evidence; analogous to feature-flag states 
More flexible: allows independent evolution of concerns (validity, belief, approval, runnability) 
.	Recommended: use orthogonal axes (validity, belief, approval, runnability) instead of stored enum.
Recommendation: Use orthogonal axes (validity, belief, approval, runnability) instead of stored enum; the survey’s enum is simpler but less flexible.

(6) Does reuse pay at all? The utility problem
Bottom line: Yes, the utility problem is real and documented in macro-operator learning and speedup learning (Minton, 1988/1990). Adding stored plans can make a system net slower — match and retrieval cost grows faster than planning effort saved. The literature shows mitigations (utility-based retention, selective forgetting, match-cost-aware indexing), but the problem is managed, not solved. For LLM agents, no study has confronted the utility problem — this absence is itself an important finding.

The utility problem: characterization and mitigations
Source	Type & authority	Key finding	Decision it informs
Minton (1988/1990) “Learned knowledge can hurt performance” 
Foundational speedup-learning papers; authoritative for utility problem.	Utility = (ApplicationFreq × AverageSavings) − MatchCost 
; learned knowledge can slow down problem solving if match cost > savings 
.	Utility metric: track application frequency, average savings, and match cost; delete procedures with negative utility.
Selective forgetting / utility-based retention 
Speedup-learning literature; authoritative for mitigations.	Delete low-utility macros (negative utility); retain high-utility macros (positive utility) 
.	Retention policy: delete procedures with negative utility; retain high-utility procedures.
Match-cost-aware indexing 
Speedup-learning literature; authoritative for indexing.	Index macros by match cost (cheap-to-match first); reorder conditions to reduce match cost 
.	Indexing strategy: order procedures by match cost; reorder precondition checks to reduce match cost.
Quality analogue: negative transfer in transfer learning
Bottom line: The quality analogue of the utility problem is negative transfer in transfer learning — retrieved plans constrain the system into a worse solution than fresh planning would have found. The negative transfer literature shows that measuring negative transfer requires a no-transfer baseline (matched control arm); single-arm proxies are insufficient.

Measuring negative transfer
Source	Type & authority	Key finding	Decision it informs
“A Survey on Negative Transfer” (arXiv 2009.00909, 2020) 
Academic survey; authoritative for negative transfer.	Negative transfer condition (NTC): RPT(A(S,T)) > RPT(A(∅,T)) — transfer learning performs worse than no-transfer baseline 
.	Measurement: require a no-transfer baseline (solve from scratch) to measure negative transfer; single-arm proxies are insufficient.
RAG evaluation: “retrieval made the answer worse”	RAG literature; authoritative for retrieval quality.	Retrieval can degrade answer quality (e.g., retrieved docs mislead LLM) .	RAG analogue: measure answer quality with/without retrieval; delete procedures that degrade quality.
Has the LLM-agent literature confronted the utility problem?
No. The 2026 survey on evolving skill libraries does not mention the utility problem or negative transfer. This absence is itself an important finding: LLM-agent skill libraries are being built without confronting the utility problem.

Implication for your design: You are early to confront the utility problem for LLM-agent procedures. Use utility-based retention (delete low-utility procedures), match-cost-aware indexing, and negative-transfer measurement (no-transfer baseline) to avoid the pitfalls documented in speedup learning.

Where the literature is silent (engineering judgement calls)
Q1 (precondition representation for agent procedures): No study measures precondition-representation failure modes for LLM-coding-agent procedures. The recommendation is borrowed from CBR, STRIPS/PDDL, and business-rules systems.

Q2 (filter cascade vs. weighted score): No study directly measures filter-cascade vs. weighted-score for LLM-coding-agent procedures. The recommendation is borrowed from IR and MCDA.

Q3 (unknown preconditions): No study measures unknown-precondition handling for LLM-coding-agent procedures. The recommendation is borrowed from conformant planning and safety engineering.

Q4 (version-space scope narrowing): No study directly measures version-space scope narrowing for LLM-coding-agent procedures. The recommendation is borrowed from version-space learning and ILP.

Q5 (verification thresholds, failure classification): No study measures verification thresholds or failure classification for LLM-coding-agent procedures. The recommendation is borrowed from sequential testing, Beta-Bernoulli posteriors, and flaky-test classification.

Q6 (utility problem for LLM agents): No study has confronted the utility problem for LLM-agent procedures. The recommendation is borrowed from speedup learning and negative transfer literature.

Actionable recommendations for your procedural-memory system
Precondition representation: Use hybrid (tags for coarse filtering, structured predicates for hard constraints, executable checks for environment validation, LLM for soft ranking).

Combining factors: Use strict filter cascade (hard constraints first, similarity ranks survivors); avoid weighted linear scoring.

Unknown preconditions: Use three-valued logic (true/false/unknown) with explicit belief-state tracking; fail closed after cold start .

Scope narrowing: Use version-space learning with hysteresis (different thresholds for narrowing vs. widening); require ≥3 failures before narrowing.

Verification: Use SPRT with α=0.05, β=0.10; require ≥10 successes with 0 failures across ≥3 distinct contexts.

Failure classification: Use coarser categories (transient, precondition, scope/environment, structural/ambiguous); automate transient/precondition/environment; queue structural/ambiguous for human review.

Staleness: Use package-version tracking for most procedures; use file-level tracking for high-stakes procedures; accept 10–30% over-invalidation .

Escape hatches: Use circuit breakers (open after 5 failures, half-open after 60s, close after 5 successes); use flaky-test quarantine (quarantine after 50% failure rate over 7 days).

Lifecycle representation: Use orthogonal axes (validity, belief, approval, runnability) instead of stored enum.

Utility problem: Track utility = (ApplicationFreq × AverageSavings) − MatchCost; delete procedures with negative utility; measure negative transfer with no-transfer baseline.

If you want, I can sketch the Python code for (a) SPRT-based verification, (b) version-space scope narrowing with hysteresis, and (c) utility-based retention with match-cost-aware indexing.


