# Draft experiment: does Claude Code with Kel get cheaper *and* better on real repository work?

Status: **draft for review, not preregistered.** Choices marked **Decide** need the user before this becomes a preregistration.

## The claim it would earn

> "With Kel installed, Claude Code resolves at least as many real repository issues as plain Claude Code, at substantially lower cost: Kel routes the work to cheaper models, checks each result, and escalates only when the check fails. Kel's knowledge makes the cheap models succeed more often."

## What we know going in (DS-1000)

- **Knowledge helps weaker models, not Sonnet.**
  - Open-model agents: +8.5 points with the hook (round 5, p = 0.004).
  - Sonnet: +1.2 [−5.0, +7.7] (round 7). Sonnet already solves 82%, and Kel's DS-1000 knowledge is public-library knowledge Sonnet already has.
- **Routing with a check is where the large gain is, in simulation.** A ladder over the real per-task results of rounds 5 and 7 (82 transfer tasks): the cheap model answers first, and the next model is tried only if the answer fails the check.

  | Setup | Solved | Cost vs Sonnet alone |
  |---|---|---|
  | Sonnet alone | 67 (81.7%) | — |
  | gpt-oss → Sonnet, no Kel | 70 (85.4%) | −54% |
  | gpt-oss → Sonnet, with Kel's hook | 73 (89.0%) | −55% |
  | gpt-oss → deepseek → Sonnet, no Kel | 75 (91.5%) | −48% |
  | gpt-oss → deepseek → Sonnet, with Kel's hook | 76 (92.7%) | −45% |

  - **Upper bound:** the check was DS-1000's hidden tests, a perfect check. With no check at all, the ladder is just gpt-oss: 54/82 = 66%.
  - **Kel's share:** most of the gain comes from routing plus the check; Kel adds 1–3 tasks by making the cheap rung succeed more often (gpt-oss 50 → 54, deepseek 47 → 55).
  - **Caveats:** open-model prices are placeholders; this replays separate runs rather than a real routed system.

So the experiment has to answer two questions:
1. With a **real, imperfect** check, does routing keep Sonnet's success at a fraction of the cost?
2. On repository work, where Kel knows things no model can (this repo's conventions, the fix from an earlier issue), does Kel's knowledge help the cheap rung and Sonnet?

## Design

**Tasks.** SWE-bench, where Kel learns from earlier issues in the same repository, so its knowledge is not public. Held-out issues of SWE-bench Verified with the frozen `kel_swebench` (train pool only), as in `experiments/swebench/`: about 190 instances, 8 repositories, official harness grading. **Decide:** Verified (ready) or SWE-rebench post-cutoff (cleaner, needs its Kel built first).

**Host.** Real Claude Code, headless (`claude -p "<issue>" --output-format json`), a fresh worktree per instance. The JSON result gives real token usage and dollar cost. The same version, settings and permission mode in every arm.

**The check (the core of the design).** Each rung's patch is accepted only if a check passes:
- **Recommended:** the repository's own existing test suite, run by the agent (the tests present at the base commit, never the hidden FAIL_TO_PASS tests), plus the agent's own reproduction of the issue: a small test it writes from the issue text, which fails before the fix and passes after.
- **Fallback if execution is not allowed:** Sonnet reviews the cheap model's diff against the issue. Cheaper, but a much weaker check.
- **Decide:** allow execution. Recommended: routing without a real check cannot keep accuracy.

**Arms** (every instance runs every arm; paired):

| Arm | What runs | Role |
|---|---|---|
| **Plain** | Sonnet does everything, no Kel | the baseline |
| **Routing** | a cheap model first; the check; escalate to Sonnet if the check fails. No Kel knowledge | what routing plus a check is worth alone |
| **Routing + Kel** (core) | the same ladder, with Kel's hook delivering knowledge to every rung, and Kel's `recommend_models` choosing the ladder | the product |
| **Knowledge** | Sonnet does everything, with Kel's hook | does repository knowledge help a frontier model? |

- **Ladder:**
  - (a) Claude family only: Haiku 4.5 → Sonnet, natively supported as Claude Code subagent models;
  - (b) open models: gpt-oss / deepseek through a command that runs `coding_agent.Agent` → Sonnet, where the DS-1000 savings came from.
  - **Decide:** (a) first for simplicity, or (b) for the bigger saving.

**Measured per instance:**
- resolved (official harness);
- dollar cost (Claude Code's `total_cost_usd`, plus open-model tokens at real provider prices);
- tokens and wall time;
- the rung that answered, the check's verdict at each rung, and how often the check was wrong: it accepted a patch that the harness rejects (false accept), or rejected one the harness accepts (false reject);
- Kel's hook content and calls.

## Analysis (to be fixed at preregistration)

- **Primary, two endpoints, both required for the claim** (Routing + Kel vs Plain):
  1. **Cost:** at least 30% lower mean dollars per instance (paired bootstrap, stratified by repository, 95% CI entirely below −30%).
  2. **Success:** non-inferior, lower 95% CI bound of the resolved-rate difference above −3 points (McNemar reported).
- **Secondary:**
  - Routing + Kel − Routing: Kel's contribution, as resolved rate, the share answered at the cheap rung, and cost;
  - Knowledge − Plain: repository knowledge on a frontier model;
  - the check's false-accept and false-reject rates;
  - per repository.
- **Power, stated in advance:** a −3-point margin on about 190 pairs is tight. The preregistration fixes the margin after a power check, or adds instances.

## Cost and time (rough)

- Sonnet in Claude Code on SWE-bench-sized issues: about $0.5–2 per instance.
- 4 arms × 190 instances: about **$300–1,000**. The routing arms cost less than Plain by design. Add open-model tokens for ladder (b).
- Grading: the existing Modal / Compute Engine pipeline.

## Prerequisites

1. The npm package with the hook installed from the local checkout, pointing at a local MCP server on `kel_swebench`. No publish is needed.
2. The routing instruction for Claude Code: a prompt or skill that calls `recommend_models`, runs the rung, runs the check, and escalates. For ladder (b), the open-model command.
3. A harness per instance: worktree → `claude -p` with the arm's settings → the diff as a SWE-bench prediction, plus the JSON usage and the per-rung log → `experiments/swebench/grade.py`.
4. The SWE-bench KH arm's result (Chaitanya), which says whether repository knowledge helps at all before we build on it.

## What each outcome would let us say

| Outcome | Claim |
|---|---|
| Routing + Kel: cost −30% or more, success non-inferior | the headline claim |
| Routing alone does as well as Routing + Kel | "routing with checks makes Claude Code cheaper"; Kel's knowledge adds nothing measurable here |
| Routing + Kel beats Routing | Kel's knowledge is what lets cheap models carry the work: the network-effect story |
| Knowledge − Plain > 0 | Kel's repository knowledge helps even frontier models, which DS-1000 could not show |
| Cost down, success lower | a measured trade-off ("X% cheaper at Y points"), not the headline |
