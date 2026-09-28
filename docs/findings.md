# Findings

As of 2026-09-28. Across DS-1000 rounds 1–7 (round 6 still running), BigCodeBench routing, and a routing simulation. Details and statistics: `experiments/ds1000/PREREGISTRATION*.md`.

## Headlines

Kel's knowledge makes open-model coding agents measurably better when it is delivered automatically. It does not measurably help Sonnet on public-library tasks. The biggest cost win comes from routing to cheaper models behind a reliable check. All results are on the DS-1000 benchmark (pandas, numpy and similar tasks), rounds 1 to 7, unless stated otherwise.

1. **Routing is much cheaper at equal or better accuracy.** On single-shot tasks, routing to open models cost 32–62% less than Sonnet at equal or better accuracy (BigCodeBench and DS-1000 rounds 1–3).
2. **Knowledge helps open-model agents, but only when it is delivered for them.** Kel's hook looks the task up before the agent starts: 52.0% to 60.6% solved, +8.5 points (95% confidence interval +2.7 to +14.4, p = 0.004). Pasting the same knowledge by hand gave the same +8.5.
3. **Letting the model decide when to ask Kel does not work reliably.** +3.3 and +4.1 points, neither significant; one model never asked at all.
4. **Sonnet gains nothing measurable from the same knowledge.** It already solves 81.7% of these tasks; with the hook, 82.9% (+1.2, interval −5.0 to +7.7).
5. **The largest opportunity is routing with a check.** Replaying our real results as a ladder (cheap model first, escalate to Sonnet only when a check fails) gives 89–93% solved versus Sonnet's 82%, at 45–55% lower cost. That assumes a perfect check; real checks are weaker, so this is an upper bound. Kel's knowledge adds 1–3 tasks on top of routing.

## What helps the most

Three things move results: deciding for the model when knowledge arrives, giving knowledge to models that lack it, and routing work to cheap models behind a check.

| What | Result | Why it works |
| --- | --- | --- |
| **Delivering knowledge automatically** (the Claude Code hook runs Kel's lookup before the agent starts) | +8.5 points for open models, p = 0.004; +11.4 where the hook found something, 0.0 where it found nothing | Knowledge arrives every time, whether or not the model would have asked; no workflow overhead |
| **Knowledge for weaker models** | gemma +11.0, deepseek +9.8, gpt-oss +4.9 points with the hook | Worked examples show the right library call and answer format; one model stopped writing throw-away scripts once it saw examples |
| **Knowledge that costs little** | the hook costs 0.7 to 1.5 times the agent's own tokens per solved task, and fewer for gemma | One lookup of about 800 characters, added once to the prompt |
| **Routing to open models** | 32–62% cheaper than Sonnet at equal or better accuracy (single-shot tasks) | Most tasks do not need the most expensive model |
| **Routing with a check** (simulated on real results) | 89–93% solved vs Sonnet's 82%, at 45–55% lower cost | Cheap models solve most tasks; the check sends the rest to Sonnet; different models fail on different tasks (12 of Sonnet's 15 failures were solved by some open model) |

Routing numbers use placeholder open-model prices; the ladder simulation assumes a perfect check.

## What does not help

Knowledge the model must fetch for itself, knowledge a strong model already has, and knowledge for one-shot answers all fail to show a significant gain.

| What | Result | Why |
| --- | --- | --- |
| **The model deciding when to ask Kel** (Kel as an MCP tool, with the product's plan_and_run workflow) | +3.3 points (p = 0.37), then +4.1 after fixes (p = 0.25) | gpt-oss never called the tool in 178 episodes; half the answers were "ambiguous" or "no match"; weaker models chose badly between candidates |
| **Knowledge for Sonnet on public-library tasks** | +3.7 as a tool (p = 0.65); +1.2 with the hook (interval −5.0 to +7.7) | Sonnet already solves 82%; Kel had an exact match for only 2 of its 15 failures; pandas and numpy knowledge is already in the model |
| **Knowledge in single-shot answers** (a note pasted into one prompt, no agent loop) | +2 to +4 points, never significant | Not diagnosed; the gain (63.0% to 66.3% in round 3) was within noise |
| **The fixes to the model-decides path** (related examples, a suggested candidate, a lighter workflow) | 55.3% before, 56.1% after | They improved what arrived, but not whether the model asked for it or used it well |
| **Kel's own lookup tool next to the hook** | the agents rarely called it: 0 calls by gemma, gpt-oss and Sonnet; 25 in 89 tasks by deepseek | The hook already delivered the answer, and its text says not to ask again |

## What hurts, and why

The product's step-by-step workflow is what hurts most: it costs 2.5 to 4.4 times the tokens and made one model worse.

| What hurts | Evidence | Cause |
| --- | --- | --- |
| **The plan_and_run workflow on small tasks** | tokens per solved task 2.5–4.4 times the agent alone; deepseek 15 steps vs 10 | Writing plan files and a run log costs steps and context that a five-line task does not need |
| **Large Kel replies kept in context** | deepseek with the workflow: −6.1 points and 190k tokens per solved task, vs 43k alone | Every step resends the whole conversation, including Kel's long replies; the model spends its budget on material instead of the task |
| **Confident snippets that do not fit** | the hook fixed 6 of Sonnet's failures but broke 5 of its successes, 2 where Kel had an exact match; 28 tasks lost by the workflow path vs 16 by the hook | A verified example for a neighbouring task can pull a model toward the wrong variant |
| **Knowledge a model does not need** | Sonnet: +7% tokens per solved task for +1.2 points | Extra context with nothing new in it is pure cost |
| **Wrong routing without a check** | with no check, the ladder is just the cheap model: 66% vs Sonnet's 82% | A cheap model's wrong answer ships unless something catches it |

Two things make results harder to read, though they are not harms. Sonnet through Claude Code is not deterministic: 12 of 82 outcomes flipped between identical runs. Kel's lookups vary slightly too: 88% of repeated lookups came back identical.

## Evidence behind each claim

Two claims are confirmed by preregistered tests; the frontier-model and combined cost-and-quality claims are not yet shown.

| Claim | Numbers | Status |
| --- | --- | --- |
| Knowledge delivered by the hook makes open-model agents solve more tasks | 52.0% → 60.6%, +8.5 points (+2.7 to +14.4), p = 0.004, 246 task–model pairs | Confirmed (preregistered) |
| The same gain when knowledge is pasted by hand | +8.5 points, p = 0.003, same tasks and models | Confirmed (a repeat, not independent) |
| The gain comes from the knowledge itself | +11.4 where knowledge arrived (184 pairs, p = 0.0005); 0.0 where none did (62 pairs) | Supporting (descriptive) |
| The hook does not raise cost | 0.7–1.5 times the agent's tokens per solved task | Measured |
| Routing is cheaper at equal or better accuracy | 32–62% cheaper than Sonnet, single-shot tasks | Replicated (placeholder prices, estimated Sonnet tokens) |
| Routing with a check beats Sonnet on cost and accuracy | 89–93% vs 82%, 45–55% cheaper | Simulation only, perfect check |
| The model deciding when to ask Kel helps | +3.3 (p = 0.37), +4.1 (p = 0.25) | Not confirmed |
| Kel's knowledge helps Sonnet | +3.7 (p = 0.65) as a tool; +1.2 (−5.0 to +7.7) with the hook | Not shown on DS-1000; a gain of +8.5 or more is unlikely there |
| Open models can replace Sonnet in agentic work | best open model with the hook 67.1% vs Sonnet 81.7% | Not true today |
| Claude Code with Kel is cheaper and better | — | Not measured; experiment drafted |

## Still running or open

Three questions are open. One is running now; two wait on other people or a decision.

- **Which knowledge features the hook needs (running).** DS-1000 round 6 turns off related examples, the suggested candidate, or both, and re-runs the full hook once. The arm with both off is complete: 159 of 267 solved across all tasks, against 163 with both on and 140 for the agent alone. That hints the hook works nearly as well without the new features, but the preregistered test waits for all arms. The rest runs on General Compute, which is slow today.
- **Does knowledge help on real repositories, including for strong models?** SWE-bench now has a hook arm; Kel learns from earlier issues in the same repository, so its knowledge is not public. Chaitanya runs it; it has not started.
- **Is Claude Code with Kel cheaper and better?** Drafted in [experiment_claude_code_with_kel.md](experiment_claude_code_with_kel.md), not run: routing with the repository's tests as the check, with and without Kel's knowledge, against plain Claude Code. It needs four decisions: which issue set, whether the agent may run tests, which models the ladder uses, and the error margin.

## What can be done

Ship the hook, stop relying on the model to ask, and build the product around routing with a check.

**Product**

1. **Ship knowledge through the hook, not the tool.** Publish the npm package with the hook. Keep the lookup tool for explicit use, but don't depend on models calling it.
2. **Lighten or drop the plan_and_run workflow for small tasks.** It is the main source of extra cost and of the deepseek loss.
3. **Make the hook quieter for strong models.** For example: send code only on a confident exact match, and otherwise pitfalls or nothing. This is untested; Sonnet showed confident snippets can mislead.
4. **Decide the knowledge features after round 6.** If the hook works as well without them, keep them off for other clients, where they added cost and hurt deepseek.
5. **Build routing around a check.** Cheap model first; accept only if the repository's tests and a reproduction test pass; otherwise escalate. Route only work that can be checked; send the rest straight to the frontier model.

**Experiments**

1. Finish round 6.
2. Run SWE-bench with the hook arm: the first test of repository knowledge, where Sonnet cannot already know the answer.
3. Preregister and run the combined Claude Code experiment. Report the check's false-accept rate, since it decides whether routing is safe.
4. Replace placeholder prices with real ones, and measure Sonnet's tokens instead of estimating them.
5. Measure the network effect: split contributors into 1, 2, 4 and 8 groups and chart cost and accuracy.
