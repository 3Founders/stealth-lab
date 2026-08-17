# Episode assembly

Type: prototype
Status:
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
