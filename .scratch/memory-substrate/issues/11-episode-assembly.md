# Episode assembly

Type: prototype
Status: resolved
Blocked by: 07

## Question

What episode boundaries actually fall out of a real Claude Code session?

This is a prototype ticket because the question is empirical. Arguing about boundary rules in the abstract is cheap and wrong; running a rough segmenter over real transcript data and reacting to the output is the point.

spec.md's requirements: an episode is a coherent bounded experience/task with intent, state, events, actions, artifacts and outcome. Boundaries may use explicit task lifecycle, user prompt, agent task, subagent hierarchy, git commit, PR, test completion, session boundaries, temporal proximity, shared repository/files/entities, or semantic goal similarity. It explicitly forbids one simplistic timeout rule, and requires deterministic boundaries now with semantic segmentation possible later.

The relevant existing facts:

- **No segmentation logic exists anywhere.** There is no episode boundary detector, no session id, no turn grouping.
- `episodes` has one writer (whole debate transcripts) and zero readers.

Build a throwaway segmenter and run it over real session transcripts from this machine — this repo's own Claude Code history is the obvious corpus, and it is real agentic-coding data of exactly the target shape.

Produce, to react to:

- A handful of real sessions segmented under **two or three candidate deterministic rule sets** (e.g. user-prompt-delimited; user-prompt plus subagent nesting; commit/test-completion delimited), shown side by side on the same sessions.
- The pathological cases, which matter more than the clean ones: a session where the user changes topic mid-stream without a natural marker; a single prompt that spawns hours of work; several trivial prompts that are obviously one task; a subagent whose work belongs to the parent episode; a compaction event mid-task.
- Rough counts: episodes per session, events per episode, and how often the candidate rules disagree.

The output is a judgement to react to, not a shipped segmenter. The decision that comes out of it: which deterministic boundary signals milestone 1 uses, how they compose when they conflict, and where the seam is for adding semantic segmentation later.

Grill afterwards: is an episode even the right unit, or is the useful unit "the span of work a procedure could be mined from" — which may be finer than a session and coarser than a tool call, and may not align with any transcript boundary at all?

## Research findings (Brief 2 — [answers2.md](../research/answers2.md))

Not an answer; evidence for the prototype. All borrowed from dialogue segmentation, process
mining, HRL and web analytics — **no study segments LLM-coding-agent traces specifically**.

**11.1 — signal reliability, ranked.** Structural boundaries beat temporal ones:

| Signal | Precision / recall | Use as |
|---|---|---|
| User prompt submission | high P, moderate R | **hard** boundary |
| Subagent start/end | high P, high R | **hard** boundary, nested under parent |
| Git commit | moderate P, high R | soft — split only where no prompt/subagent boundary |
| Test-run completion | moderate P, moderate R | soft — refines episodes, doesn't create them |
| Working-directory change | low P, low R | tiebreaker only |
| Context compaction | unknown (no evidence) | soft; validate empirically |
| Idle gap | low P, low R | weak — see below |

**Idle gaps are the trap.** The 30-minute convention is described in the source literature as "a
rounding of some fairly dodgy maths done on 1995 browsing behaviour." Inter-activity times are
**bimodal**; the correct method is to fit a two-component Gaussian mixture to log-scaled
inter-event times and put the threshold at the valley — empirically **~1 hour for developer/B2B
workflows**, not 30 minutes. This independently vindicates spec.md's refusal of a single timeout
rule. If the prototype uses idle gaps at all, fitting that distribution is a concrete, cheap
first experiment.

**11.2 — composition.** Strict precedence with **hierarchical nesting**
(prompt > subagent > commit/test > idle). Voting is rejected (non-deterministic, hard to debug),
union is rejected (oversegmentation), intersection is rejected (undersegmentation). Nesting
matches the actual structure: a prompt episode contains subagent episodes, which contain
commit/test spans — so subagent work is genuinely both its own unit *and* part of its parent's,
and nesting represents that rather than forcing a choice.

**11.4 — the finding that changes the architecture, not just this ticket.** Optimising for
*coherent description* and optimising for *extractability of reusable structure* produce
**different boundaries**, and the HRL option-discovery literature shows this explicitly. Episode
boundaries fall at semantic shifts; option boundaries fall at subgoal states, bottleneck states
(high betweenness centrality), and change-points in trajectory features. A topic may contain
several options; an option may span several topics.

Recommendation from the source: **organise memory by episodes, but extract procedures from option
spans** — i.e. this ticket's segmentation is not automatically the segmentation ticket 05's
procedure mining should consume. That is a second, different segmenter, and it is currently
unowned by any ticket. Flag it when resolving rather than silently assuming one boundary set
serves both.

**11.5 — evaluation, and how much precision is worth chasing.** Pk/WindowDiff need labels and
don't apply. Without gold: purity (detects over-segmentation) and coverage (detects
under-segmentation) on a large set, **500–1000 episodes**; human adjudication on **50–100**;
stability under perturbation to detect noise-fitting; inter-rule agreement to find conflicts;
downstream-task evaluation as the real proxy.

The calibrating fact: **inter-annotator agreement on task boundaries is low** — often <0.6 Kappa
in dialogue, ~70% expert agreement in process mining. If humans disagree on ~30% of boundaries,
targeting >90% precision is wasted effort. **Aim 70–80% and let downstream task performance
decide.**

## Answer

**Boundaries compose by strict precedence with hierarchical nesting**:
`prompt > subagent > commit/test > idle`. Prompt submissions and subagent start/end are **hard**
boundaries (both high precision, subagent also high recall); commits and test completions are
**soft** — they split only where no hard boundary is present, and otherwise refine within one;
working-directory changes are a tiebreaker; idle gaps are the weakest signal.

Nesting rather than a flat sequence, because the alternative loses something real: a subagent's
work is genuinely both its own coherent unit *and* part of its parent's episode, and a flat
segmentation forces a false choice between those. The cost is a `parent_episode_id` self-reference
and recursive queries for "all events in this episode including children" — accepted. Voting is
rejected (non-deterministic and hard to debug for no measured gain), union is rejected
(oversegmentation), intersection is rejected (undersegmentation).

**Idle gaps stay, as the weakest signal, with the threshold fitted rather than assumed.** The
30-minute convention that spec.md warns against is described in the source literature as "a
rounding of some fairly dodgy maths done on 1995 browsing behaviour." Inter-activity times are
**bimodal**; the correct method is to fit a two-component Gaussian mixture to log-scaled
inter-event times and place the threshold at the valley, which lands near **1 hour for
developer/B2B workflows**. This independently vindicates spec.md's refusal of a single timeout
rule, and fitting that distribution is the cheapest first experiment this prototype can run.

**Semantic segmentation seam**: deterministic boundaries are hard edges; semantic segmentation
subdivides *within* them, never across. That keeps later semantic work additive and prevents a
model from overriding a boundary that a git commit or a subagent invocation established as fact.

**"Episode" is the right unit for memory, and the wrong unit for procedure mining — and milestone
1 only builds the first.** This is the finding that reaches past this ticket. HRL option-discovery
work shows that optimising for *coherent description* and optimising for *extractability of
reusable structure* produce genuinely different boundaries: episode boundaries fall at semantic
shifts, option boundaries fall at subgoal states, bottleneck states, and change-points. A topic
can contain several options; an option can span several topics.

Three ways to respond. Use one segmentation for both — simple, but procedure quality silently
degrades and nobody would notice. Build both segmenters now — correct, but that is machinery for
procedure *mining*, which milestone 1 does not include (ticket 05 settled procedure
representation, not extraction). Build episodes now and record the gap — chosen. **Procedure
mining will need its own option-span segmenter, and no ticket currently owns it**; that goes to
the map's fog explicitly rather than being absorbed silently into an assumption that one boundary
set serves both purposes.

**Evaluation is bounded by what can actually be validated.** Pk and WindowDiff need gold
boundaries and do not apply. Available without labels: purity (detects oversegmentation) and
coverage (detects undersegmentation), stability under perturbation, inter-rule agreement, and
downstream-task performance as the real arbiter. The published protocol wants purity/coverage over
500–1000 episodes and human adjudication over 50–100 — likely more real transcripts than exist
today, so this prototype reports **boundary counts and inter-rule disagreement rates on whatever
real transcripts exist**, and does not claim a validation it cannot run.

The calibrating fact that sets the target: **human inter-annotator agreement on task boundaries is
low** — often below 0.6 Kappa in dialogue segmentation, ~70% expert agreement in process mining.
If people disagree on roughly a third of boundaries, chasing above 90% precision is wasted effort.
**Target 70–80% and let downstream task performance arbitrate.**

**Provenance of this answer.** All of it is transferred from adjacent domains — dialogue
segmentation, process-mining case-ID discovery, HRL option discovery, web-analytics session
identification. **No study segments LLM-coding-agent traces**, and every recommendation above
inherits that caveat. The class-C part of this ticket — which signals actually fire on real
sessions and how often the rules disagree — is precisely why this is a `prototype` and not a
`grilling` ticket, and it remains unanswered until the segmenter runs.
