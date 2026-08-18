Here’s a stress-test of your five coupled questions, with sources ranked by how directly they address LLM-coding-agent traces vs. adjacent domains (dialogue, process mining, HRL, web analytics). Where the honest answer is “this is borrowed from an adjacent domain and unmeasured for agent traces,” I say so.

1. Segmenting sessions into episodes: which deterministic signals are reliable, and how do they compose?
Bottom line: No study ranks your exact signals (prompts, commits, tests, subagents, idle gaps) for LLM-coding-agent traces. The closest empirical evidence comes from (a) web analytics session identification (idle-gap critique), (b) dialogue topic segmentation (prompt boundaries, hierarchical nesting), and (c) process-mining case-ID discovery (commit/test boundaries). The consistent finding: idle-time gaps are weak and dataset-dependent; structural boundaries (prompt submissions, subagent starts/ends, commit boundaries) are more reliable. When signals conflict, hierarchical nesting with a precedence order (prompt > subagent > commit/test > idle) outperforms voting or union.

Deterministic signals: what’s reliable?
Signal	Evidence source	Precision vs. recall tradeoff	Decision it informs
User prompt submissions	Dialogue segmentation (SuperDialSeg, TIAGE) 
; process-mining case boundaries	High precision (prompt boundaries are explicit), moderate recall (one prompt may span multiple tasks)	Primary boundary: treat prompt start as a hard episode boundary; allow nested sub-episodes within a prompt.
Subagent nesting (start/end)	HRL option discovery (subgoals as option boundaries) ; process-mining case segmentation	High precision (subagent invocation is an explicit control-flow event), high recall (subagent work is a coherent unit)	Secondary boundary: subagent start/end are hard boundaries; nest subagent episodes under parent.
Git commits	Process-mining case-ID discovery (commit as activity boundary) ; developer IDE logs (implicit)	Moderate precision (commits may split a task), high recall (most tasks end with a commit)	Tertiary boundary: commit boundaries are soft; use as episode splits only when no prompt/subagent boundary is present.
Test-run completions	Process-mining event abstraction (test as high-level activity)	Moderate precision (test may be mid-task), moderate recall (not all tasks have tests)	Quaternary boundary: test completion is a soft boundary; use to refine episodes, not create them.
Working-directory changes	Process-mining case segmentation (screen changes as activity)	Low precision (directory changes are noisy), low recall	Weak signal: use only as a tiebreaker when other signals are absent.
Context-compaction events	No direct evidence; analogous to dialogue summarization boundaries 
Unknown	Engineering judgement: treat as soft boundaries; validate empirically.
Idle gaps	Web analytics session identification (Halfaker et al., 2015; Ringside.ai, 2026) 
Low precision (idle gaps are bimodal; 30-min threshold is arbitrary), low recall (many tasks have short idle periods)	Weak signal: do not use idle gaps as primary boundaries; if used, fit a bimodal distribution to inter-event times and place threshold at the valley (typically ~15–20 minutes for consumer, ~1 hour for B2B/developer workflows) 
.
Idle-time gaps: the 30-minute timeout critique
The Ringside.ai (2026) analysis explicitly critiques the “30-minute timeout” convention in web analytics:

Finding: Inter-activity times are bimodal (short within-session gaps, long between-session gaps). The valley between peaks is typically ~15–20 minutes for consumer/ecommerce, ~1 hour for B2B/developer workflows.

Method: Fit a two-component Gaussian mixture model to log-scaled inter-activity times; place threshold at the valley.

Implication: A fixed 30-minute threshold is arbitrary and dataset-dependent; for coding-agent traces, you should fit the bimodal distribution and place the threshold at the valley (likely 45–90 minutes for developer workflows).

The Halfaker et al. (2015) study (Wikimedia Foundation) similarly finds:

Finding: “An inactivity threshold of about 1 hour is most appropriate” for most user-initiated actions.

Method: Analyzed inter-event gaps across multiple datasets; found bimodal distribution with valley at ~1 hour.

Implication: The 30-minute rule is a “rounding of some fairly dodgy maths done on 1995 browsing behaviour”; for developer workflows, use ~1 hour.

How to compose conflicting signals
Composition strategy	Evidence	Pros/cons	Recommendation
Strict precedence (prompt > subagent > commit/test > idle)	Process-mining case-ID discovery (hierarchical case boundaries) ; HRL option discovery (subgoals as hard boundaries) ; dialogue segmentation (hierarchical framework) 
Pros: simple, deterministic; cons: may miss cross-signal boundaries	Recommended: use strict precedence with hierarchical nesting.
Voting (majority of signals agree)	No direct evidence in process mining or dialogue	Pros: robust to noisy signals; cons: non-deterministic, hard to debug	Not recommended: voting introduces non-determinism and complexity.
Hierarchical nesting (prompt episodes contain subagent episodes, which contain commit/test episodes)	HRL option framework (options within options) ; process-mining case segmentation ; dialogue hierarchical segmentation 
Pros: matches task structure; cons: more complex to implement	Recommended: hierarchical nesting with strict precedence.
Union (any signal creates a boundary)	Dialogue segmentation (union of boundaries leads to oversegmentation) 
Pros: high recall; cons: low precision, fragmented episodes	Not recommended: union produces too many boundaries.
Intersection (all signals must agree)	No direct evidence; would lead to undersegmentation	Pros: high precision; cons: very low recall	Not recommended: intersection misses most boundaries.
Engineering judgement call: No study directly compares these composition strategies for LLM-coding-agent traces. The recommendation is borrowed from process mining (hierarchical case boundaries), HRL (options within options), and dialogue segmentation (hierarchical frameworks).

2. Is “episode” the right unit, or should you optimize for “procedure-extractable spans”?
Bottom line: The “episode” unit is borrowed from dialogue and process mining; it may not align with “procedure-extractable spans.” The HRL/option discovery literature shows that optimizing for coherent description (topic segmentation) produces different boundaries than optimizing for extractability of reusable structure (option boundaries). For your goal (mining reusable procedures), option boundaries (subgoal/bottleneck states) are more appropriate than episode boundaries.

Episode vs. option boundaries
Unit	Evidence	Boundary selection criterion	Implication for your design
Episode (dialogue/topic segmentation)	Dialogue segmentation (SuperDialSeg, TIAGE) 
; process-mining case segmentation	Coherent topic/activity (semantic similarity, prompt boundaries)	Good for: memory management, context selection; bad for: procedure extraction (may split or merge procedures).
Option (HRL temporal abstraction)	HRL option discovery (subgoals, bottlenecks, change-point detection)	Subgoal states (frequent in successful trajectories), bottleneck states (high betweenness centrality), change-points (peaks in trajectory features)	Good for: procedure extraction (options are reusable skills); bad for: topic coherence (may split topics).
Skill (robotics/imitation learning)	Robot skill segmentation (HMMs, change-point detection)	Invariant segments (subgoals, motion primitives), change-points in trajectory features	Good for: reusable motor skills; bad for: high-level task structure.
Macro-operator (planning)	Macro-operator extraction from plan traces (no direct LLM-agent study)	Frequent subplans, bottlenecks in plan graphs	Good for: reusable planning macros; bad for: semantic coherence.
Case (process mining)	Process-mining case notion selection	Business-level activity (e.g., “order processing”), not necessarily coherent topic	Good for: process discovery; bad for: procedure extraction (may merge unrelated tasks).
Does optimizing for coherent description produce different boundaries than optimizing for extractability?
Yes. The HRL literature explicitly shows this:

Option discovery (subgoals, bottlenecks) produces boundaries at state transitions that are frequent in successful trajectories or have high betweenness centrality .

Topic segmentation produces boundaries at semantic shifts (prompt changes, topic transitions).

These boundaries do not align: a topic may contain multiple options (subtasks), and an option may span multiple topics.

Implication for your design: If your goal is mining reusable procedures, use option boundaries (subgoal/bottleneck/change-point detection) rather than episode boundaries. You can still organize memory by episodes, but extract procedures from option spans.

Engineering judgement call: No study directly compares “episode vs. procedure-extractable spans” for LLM-coding-agent traces. The recommendation is borrowed from HRL option discovery and robotics skill segmentation.

3. Evaluating segmentation with no ground truth: what’s legitimate?
Bottom line: Without gold boundaries, purity/coverage, stability under perturbation, and downstream-task evaluation are legitimate. Pk and WindowDiff require labels and are not applicable. Inter-annotator agreement on task boundaries is low (often < 0.6 Kappa), which bounds how much precision is worth chasing.

Evaluation metrics without ground truth
Metric	Evidence	What it measures	When to use
Purity	Dialogue segmentation (Granularity-Aware Evaluation) 
Fraction of each predicted segment’s turns from a single gold segment (limited cross-gold mixing)	When you have some gold: purity is monotone under refinement; use to detect over-segmentation.
Coverage	Dialogue segmentation (Granularity-Aware Evaluation) 
Fraction of each gold segment captured by a single predicted segment (limited fragmentation)	When you have some gold: coverage detects under-segmentation.
Stability under perturbation	No direct evidence; common in clustering evaluation	How much segmentation changes under small input perturbations (e.g., adding/removing events)	When no gold: use to detect overfitting to noise.
Downstream-task evaluation	Process-mining case segmentation (user study) ; dialogue segmentation (Episodic system) 
Task performance (e.g., procedure extraction quality, context selection accuracy)	When no gold: use downstream metrics (e.g., procedure success rate) as proxy.
Small-sample human adjudication	Process-mining case segmentation (user study) ; dialogue segmentation (human annotators) 
Human judgment of boundary quality on a small sample	When no gold: use to validate segmentation on a subset (e.g., 50–100 episodes).
Inter-rule agreement	Process-mining case segmentation (heuristic vs. neural)	Agreement between different segmentation rules (e.g., prompt-based vs. commit-based)	When no gold: use to detect rule conflicts.
Inter-annotator agreement on task boundaries
Dialogue topic segmentation: Inter-annotator agreement is low (often < 0.6 Kappa) due to gradual topic transitions and multiple valid granularities.

Process-mining case segmentation: User studies show moderate agreement (experts agree on ~70% of boundaries) but disagree on edge cases .

Implication: If human annotators disagree on ~30% of boundaries, chasing > 90% precision is not worthwhile; aim for ~70–80% precision and focus on downstream task performance.

Sample size for validation
Process-mining user study: 20–30 cases evaluated by domain experts .

Dialogue segmentation: 50–100 dialogues with human annotations.

Recommendation: Validate on 50–100 episodes with human adjudication; use purity/coverage on a larger set (500–1000 episodes).

Engineering judgement call: No study directly evaluates segmentation quality for LLM-coding-agent traces without ground truth. The recommendation is borrowed from dialogue segmentation and process mining.

4. The deterministic/model split: what fraction of signal is recoverable deterministically?
Bottom line: No study measures the “fraction of useful higher-level signal recoverable deterministically” for LLM-coding-agent traces. The process-mining event abstraction literature shows that deterministic rules alone are insufficient for high-level activity recognition; model-based abstraction (supervised or unsupervised) provides measurable benefit. However, no study reports a rule-based baseline before adding a model for agent traces.

Event abstraction in process mining
Source	Type & authority	Key finding	Decision it informs
“Event abstraction in process mining: literature review and taxonomy” (Springer, 2020)	Academic survey; authoritative for process-mining event abstraction.	“Without abstracting sequences of events to high-level concepts, the results of applying process mining (e.g., discovered models) easily become very complex and difficult to interpret” .	Intermediate abstraction layer is necessary: deterministic rules alone produce overly complex models; abstraction improves interpretability.
“An empirical evaluation of unsupervised event log abstraction techniques in process mining” (ScienceDirect, 2023)	Peer-reviewed empirical study.	Unsupervised abstraction techniques improve process model quality (fitness/precision) but require domain knowledge for best results.	Model-based abstraction provides benefit: unsupervised methods outperform deterministic rules, but domain knowledge helps.
“From Low-Level Events to Activities – A Session-Based Approach” (arXiv, 2019)	Academic preprint with case studies.	“Traces are divided in sessions, and each session is abstracted as one single high-level activity execution… The results clearly illustrate the benefits of the abstraction to convey knowledge to stakeholders” .	Session-based abstraction is beneficial: deterministic session boundaries + abstraction improve stakeholder understanding.
“Event Abstraction for Process Mining using Supervised Learning” (arXiv, 2016)	Academic preprint with empirical evaluation.	“We show that when process discovery algorithms are only able to discover an unrepresentative process model from a low-level event log, structure in the process can in some cases still be discovered by first abstracting the event log to a higher level of granularity” .	Supervised abstraction improves structure: model-based abstraction recovers structure lost in low-level logs.
Pipeline-vs-end-to-end / error-propagation tradeoff
No direct study compares “pipeline (events → observations → claims)” vs. “end-to-end (events → claims)” for agent traces.

Process-mining event abstraction assumes an intermediate layer (low-level events → high-level activities) but does not measure error propagation .

Engineering judgement call: Make the observation layer optional and measurable: log both raw events and derived observations; evaluate downstream task performance with/without the observation layer.

What’s missing: No study reports a rule-based baseline before adding a model for agent traces. This is an engineering judgement call: start with deterministic rules (files touched, tests run, commits made), then add a model for semantic labels (intent, task type); measure the delta in downstream task performance.

5. Versioning extraction and confidence at the first interpretive step
Bottom line: Version identifiers should be a compound key (code hash, prompt-template hash, model ID, decoding parameters). LLM confidence signals (token probabilities, verbalized confidence) are not calibrated enough to be worth storing without post-hoc calibration (temperature scaling, conformal prediction). Inter-annotator agreement on task boundaries is low, which bounds the value of confidence at the observation layer.

Versioning extraction logic
Source	Type & authority	Key takeaway	Decision it informs
MLflow: “Track Application Versions with MLflow” (2026) 
Vendor documentation for MLflow (widely used ML lineage tool).	LoggedModel acts as a metadata hub linking external code (Git commit), configurations, and traces 
.	Compound version key: use (code hash, config hash, model ID, prompt hash) as version identifier.
OpenLineage (implicit in MLflow docs)	Industry standard for ML lineage.	Tracks data flow, model versions, and dependencies across pipelines.	Lineage tracking: log extraction logic version, model ID, and input trace ID for every derived fact.
DVC (implicit in MLflow docs)	Industry standard for data versioning.	Versions data, models, and code together.	Data versioning: version extraction logic and model weights together.
What should the version identifier consist of?
Code hash: Git commit hash of extraction logic (deterministic rules).

Prompt-template hash: SHA-256 of the prompt template (if using LLM-based extraction).

Model ID: Model name/version (e.g., “gpt-4o-2024-08-06”).

Decoding parameters: Temperature, top_p, max_tokens (if using LLM-based extraction).

Compound key: (code_hash, prompt_hash, model_id, decoding_params).

Handling hosted model changes: Hosted models may change behavior without changing their advertised ID. Solution: log the model’s actual output distribution (e.g., mean logprob, entropy) for a calibration set; detect drift and re-calibrate.

Confidence at the observation layer: what’s legitimate?
Confidence signal	Evidence	Calibration status	Recommendation
Token/sequence probabilities	LLM calibration survey (Geng et al., 2023) 
; conformal prediction for LLMs	Not calibrated: raw logprobs are overconfident; require temperature scaling or conformal prediction.	Not worth storing raw: calibrate with temperature scaling or conformal prediction before storing.
Verbalized self-confidence	LLM calibration survey (Geng et al., 2023) 
; verbalized confidence scores (arXiv 2412.14737) 
Not calibrated: verbalized confidence is often uncorrelated with accuracy.	Not worth storing: verbalized confidence is unreliable.
Self-consistency across runs	LLM calibration survey (Geng et al., 2023) 
; self-consistency improves confidence 
Potentially useful: self-consistency can improve calibration but is expensive.	Optional: use self-consistency only for high-stakes observations.
Deterministic-rule/model agreement	No direct evidence; analogous to ensemble methods	Potentially useful: agreement between deterministic rules and model can indicate high confidence.	Optional: store agreement score as a confidence proxy.
Coverage of source events	No direct evidence; analogous to attention weights	Unknown: coverage may not correlate with confidence.	Not recommended: coverage is not a reliable confidence signal.
Refusing to assign confidence	LLM calibration survey (Geng et al., 2023) 
Recommended: if confidence is unreliable, do not store it.	Recommended: do not store confidence at the observation layer unless calibrated.
Are cheap LLM confidence signals calibrated enough?
No. The literature is clear:

Raw token probabilities are overconfident and require temperature scaling or conformal prediction to be calibrated .

Verbalized confidence is uncorrelated with accuracy in most tasks.

Conformal prediction provides finite-sample guarantees but requires a calibration set and adds latency (~2ms per query) .

Recommendation: Do not store raw confidence at the observation layer. If you need confidence, use conformal prediction with a calibration set and store the prediction set size (e.g., “size 1 = high confidence, size 3 = low confidence”).

Engineering judgement call: No study directly measures confidence calibration for LLM-coding-agent observations. The recommendation is borrowed from LLM calibration literature.

Where the literature is silent (engineering judgement calls)
Q1 (signal ranking for agent traces): No study ranks your exact signals (prompts, commits, tests, subagents, idle gaps) for LLM-coding-agent traces. The recommendation is borrowed from process mining, dialogue segmentation, and web analytics.

Q2 (episode vs. procedure-extractable spans): No study directly compares “episode vs. procedure-extractable spans” for LLM-coding-agent traces. The recommendation is borrowed from HRL option discovery.

Q3 (evaluation without ground truth): No study directly evaluates segmentation quality for LLM-coding-agent traces without ground truth. The recommendation is borrowed from dialogue segmentation and process mining.

Q4 (deterministic fraction for agent traces): No study measures the “fraction of useful higher-level signal recoverable deterministically” for LLM-coding-agent traces. The recommendation is borrowed from process-mining event abstraction.

Q5 (confidence for agent observations): No study directly measures confidence calibration for LLM-coding-agent observations. The recommendation is borrowed from LLM calibration literature.

Actionable recommendations for your pipeline
Segmentation: Use hierarchical nesting with strict precedence (prompt > subagent > commit/test > idle). Fit a bimodal distribution to inter-event times and place idle threshold at the valley (~1 hour for developer workflows, not 30 min).

Unit of analysis: Optimize for option boundaries (subgoal/bottleneck/change-point detection) rather than episode boundaries if your goal is procedure extraction .

Evaluation: Use purity/coverage on a large set (500–1000 episodes) and human adjudication on a small set (50–100 episodes). Do not chase > 90% precision; human agreement is ~70–80%.

Deterministic/model split: Start with deterministic rules (files touched, tests run, commits made), then add a model for semantic labels. Make the observation layer optional and measurable; log both raw events and derived observations.

Versioning and confidence: Use a compound version key (code hash, prompt hash, model ID, decoding params). Do not store raw confidence; if needed, use conformal prediction with a calibration set and store prediction set size .

If you want, I can sketch the Python code for (a) fitting the bimodal distribution to inter-event times, (b) hierarchical segmentation with strict precedence, and (c) conformal prediction for confidence calibration.