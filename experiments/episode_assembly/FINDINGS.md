# Episode assembly prototype — findings (ticket 11, step 3)

Ticket 11 is a **prototype** ticket: "the deliverable is a judgement to react to, not a shipped
segmenter." This is that judgement, from running 3 candidate rule sets over **36 real Claude Code
sessions of this project — 28,969 lines, 107 MB, Aug 1–19**, plus 69 subagent transcripts.

Reproduce:

```bash
cd experiments/episode_assembly
python3 sniff_schema.py --json schema_report.json     # what's actually in the files
python3 segment.py --json segmentation_report.json    # the three rule sets
```

Privacy: both scripts report schema, counts and timing only. No message text, tool input/output,
or file contents is read into any output. Corpus is this project's own sessions only, per the
repo owner's decision.

---

## Headline: two of ticket 11's inherited assumptions do not survive contact with real data

### 1. The idle-gap bimodality does not exist. Drop the signal, don't tune it.

Ticket 11 prescribes: fit a two-component Gaussian mixture to **log-scaled inter-event times**,
place the threshold at the valley, expect **~1 hour** for developer workflows, and explicitly
reject the 30-minute convention as "dodgy maths done on 1995 browsing behaviour."

We implemented exactly that. **It does not produce a usable threshold**, at either granularity:

| gap population | n | low component | high component | separation | fitted "valley" |
|---|---|---|---|---|---|
| raw inter-event | 22,911 | 0.6 s | 3.7 s | 6.4× | **0.8 s** |
| human prompt→prompt | 701 | 136 s | 579 s | 4.2× | **136 s (2.3 min)** |

Raw gap distribution: **p50 = 2.3 s, p90 = 23.4 s, p99 = 903 s, max = 147 h.**

Neither population is bimodal. The fitted valleys (0.8 s and 2.3 min) are three orders of
magnitude below the ~1 h the ticket anticipated, and both are artifacts of EM splitting a single
skewed mode rather than finding a real trough.

**Why the inherited finding didn't transfer.** The web-analytics literature it came from measures
*human-paced* click streams, where within-session gaps (seconds) and between-session gaps (hours)
genuinely form two modes. An agent transcript is *machine-paced* inside a task — tool calls land
2 seconds apart — and human idleness shows up as a **long tail, not a second mode**. A long tail
has no valley to place a threshold in.

**Recommendation: drop idle gaps as a boundary signal entirely.** Ticket 11 kept it as "the
weakest signal" with a fitted threshold; the honest reading of this data is that there is nothing
to fit. If a temporal rule is ever wanted, use a fixed high percentile (p99 ≈ 15 min) as an
explicit, arbitrary cutoff and label it as such — not a threshold dressed up as fitted.

### 2. Commit/test boundaries are ~97% disjoint from prompt boundaries — and unusable alone

Jaccard overlap of boundary positions:

| comparison | Jaccard |
|---|---|
| A (prompt) vs B (prompt + subagent) | **0.893** |
| A (prompt) vs C (commit/test) | **0.028** |
| B vs C | **0.028** |

Rule C produces a **median of 1 episode per session** — i.e. most sessions contain no commit or
test-completion at all, so the rule degenerates to "one episode = one session."

This *validates* ticket 11's precedence decision (`prompt > subagent > commit/test > idle`) from
the opposite direction than expected: commit/test isn't a weaker version of the prompt signal, it
is measuring a different thing almost entirely. It should be **metadata attached to an episode, or
a sub-boundary within one — never a top-level cut.**

---

## The three rule sets, measured

| rule | episodes | median/session | mean events/episode |
|---|---|---|---|
| **A** prompt only | 823 | 5.0 | 35.2 |
| **B** prompt + subagent | 888 | 6.5 | 32.7 |
| **C** commit/test | 332 | 1.0 | 87.4 |

Rule A is the only viable primary boundary. Rule B adds 65 boundaries (+7.9%) — subagent spawns
are a real but minor refinement. Rule C is not a boundary rule.

## Pathological cases (ticket 11 asked for these specifically)

| count | case |
|---|---|
| **147** | trivial prompt (≤2 events) — **18% of all Rule-A episodes** |
| 39 | compaction event mid-session |
| 22 | one prompt spawning >200 events |
| 5 | subagent work with no commit/test signal |
| 5 | session is a forest (>1 `parentUuid: null` root) |
| 1 | session with zero human prompts |

**The 18% trivial-prompt rate is the most actionable number here.** Ticket 11 anticipated "several
trivial prompts that are one task" as a pathology; it is not an edge case, it is nearly a fifth of
all episodes. Prompt-only segmentation **over-segments**, and needs a merge rule — e.g. fold an
episode of ≤2 events into its successor — before it is usable. The mirror pathology (22 prompts
spawning >200 events each) says a single prompt can also *under*-segment badly, so a within-episode
subdivision rule is wanted too. Those two together are the real design work, and neither is a
boundary-signal question.

---

## Schema facts that contradict what the tickets assumed

From `sniff_schema.py` over the full corpus — several of these would have been silent bugs:

- **16 distinct line types, 65 top-level keys, 11 CLI versions** in one project's history. A
  single-file sample showed only 8 types / 39 keys / 3 versions. Building against a sample would
  have missed `ai-title`, `file-history-delta`, `permission-mode`, `agent-name`, `agent-setting`,
  `relocated`, `worktree-state`, `agent-color` entirely.
- **9 of the 16 line types carry no `timestamp`** (`last-prompt`, `mode`, `ai-title`,
  `file-history-snapshot`, `permission-mode`, `agent-name`, `agent-setting`, `relocated`,
  `worktree-state`, `agent-color`). Any temporal rule must impute or skip.
- **Schema drift is intra-file**: 11 CLI versions across the corpus, multiple within single files.
  Ticket 07 warned it was unstable *across releases*; it is unstable *within one transcript*.
- **A session is a forest.** 46 `parentUuid: null` roots across 36 sessions. Compaction and resume
  create new roots — a single-chain walk truncates silently.
- **Subagent work is in sibling files**, `<session>/subagents/agent-<hex>.jsonl`, joined by
  `sourceToolAssistantUUID` → the parent assistant line's `uuid`. Ticket 11 treats subagent
  start/end as an in-session signal; it requires a cross-file join.
- **`tool_use` ≠ `tool_result`**: 6,077 vs 6,026. Interrupted and denied calls leave unmatched
  pairs; do not assume pairing.
- `cwd` and `gitBranch` are on every conversational line — directly usable for the `project_id`
  column (ticket 09's deferred decision). Observed branches: `main`, `research/claude-code-hooks`,
  `worktree-readme-vision`.

## Prior art we reused rather than rebuilt

`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/session-report/skills/session-report/analyze-sessions.mjs`
(875 lines) already solves the "is this line a genuine human prompt" predicate, and it is subtler
than it looks. Beyond the obvious `isMeta`/`isCompactSummary`/`isSidechain`/`tool_result`
exclusions, it also drops lines beginning `<task-notification`, `<scheduled-wakeup`,
`<background-task`, and `[Request interrupted`.

**Those four prefixes matter enormously here.** Background-agent notifications arrive as
`type: "user"` lines. Without that filter every one of them would open a spurious episode — and
this project's sessions are full of them. We adopted the predicate verbatim.

---

## What this prototype does *not* claim

Per ticket 11: no precision/recall claim, no Pk or WindowDiff (both need gold boundaries that do
not exist here), and no validation against the published 500–1000-episode protocol — the corpus is
36 sessions. Target was 70–80% agreement, not >90%, because human inter-annotator agreement on
task boundaries is itself only ~70–80%. The output above is boundary counts and inter-rule
disagreement, which is exactly what the ticket asked for and nothing more.

**Still open (unchanged):** procedure mining needs its own option-span segmenter over
subgoal/bottleneck boundaries — a different segmentation from this one, currently owned by no
ticket, and not part of milestone 1.
