# Findings

As of 2026-09-28. Across DS-1000 rounds 1–7 (round 6 still running), BigCodeBench routing, and a routing simulation. Details and statistics: `experiments/ds1000/PREREGISTRATION*.md`.

Contents: [Headlines](#headlines) · [What helps the most](#what-helps-the-most) · [What does not help](#what-does-not-help) · [What hurts, and why](#what-hurts-and-why) · [Evidence behind each claim](#evidence-behind-each-claim) · [Still running or open](#still-running-or-open) · [What can be done](#what-can-be-done) · [Making the knowledge side useful](#making-the-knowledge-side-useful-what-exists-today) · [Methodology](#methodology)

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

## Making the knowledge side useful: what exists today

Kel's knowledge pays off where it gives a model something it lacks, delivered without the model having to ask. Of six ideas that would make it more useful, two largely exist, three exist in part, and one is missing. Most have never been measured.

| Idea | In the system now | Status | Measured? |
| --- | --- | --- | --- |
| **Store what frontier models can't know** (repository conventions, internal APIs, failed approaches) | `report_discovery` (fix, missing step, precondition, better way, correction; with proof; optionally repository-scoped; private claims returned through `stealth://procedures/<id>/claims`); `survey_repo` repository facts that `find_ways` judges candidates against; pitfalls in Procedures | Partly: capture depends on the agent choosing to call `report_discovery` | No: knowledge was frozen and `report_discovery` off in every round |
| **Knowledge serving routing** | `recommend_models` ladder, `report_model_run` outcomes feeding a per-Goal recommender, check kinds (tests, procedure check, judge, self-report), per-step proof in `plan_and_run` | Partly: no automated check-and-escalate loop inside Claude Code; check recipes are not stored or retrieved as their own kind of knowledge | Only on recorded single-shot attempts and in simulation |
| **Delivery adapted to model strength** | hook labels ("not verified to apply", "not the same as this request"); per-client call statistics recorded | Missing: the same text for every model, no model input to `find_ways`, no confidence gating | Indirectly: round 7 shows the need |
| **Automatic, small delivery** | the Claude Code hook (at most 8,000 characters), the call governor, the small-task fast path | Largely exists; the npm package is not published; no proxy for hosts without hooks | Yes: +8.5 (round 5) |
| **Closing the coverage gap** | related examples, suggested candidate, Goal abstraction placement, ingestion sources, method library, contributor profiles, leaderboard and economy APIs | Partly: a third of DS-1000 lookups still find nothing | Network effect not measured |
| **Learning which knowledge helps** | evidence-aware selection (a lower bound on each Procedure's success rate) and demotion on negative evidence | Largely exists for Procedures | No: frozen in experiments |

**The cross-cutting gap:** reading knowledge is automatic now, through the hook. Writing it (discoveries) and reporting outcomes (did the fix pass, which model succeeded) still depend on the agent choosing to call a tool, which round 4 showed models don't reliably do. So in real use the learning loop may barely turn.

**Build order:**
1. Publish the hook.
2. Add an automatic capture hook: when the agent finishes or runs tests, send the diff, the test commands and their results to Kel as an outcome and a candidate discovery.
3. Add model-aware delivery: code for weak models, pitfalls and repository facts for strong ones.
4. Automate routing as a check-and-escalate loop.
5. Measure capture and learning on SWE-bench with knowledge growing during the run, and the network effect with the contributor-scaling curve.

## Methodology

Every scored experiment follows the same discipline. What changes from round to round is the question, the arms, and how knowledge reaches the model.

### Rules every round follows

- **Preregistered.** Before the first scored run, the hypothesis, arms, primary endpoint and decision rule are written in `experiments/ds1000/PREREGISTRATION*.md`. That file is hashed together with the sample and the runner code (`runsN/design.sha256`), and committed so its timestamp precedes the runs. Nothing about the code, prompts or parameters changes afterwards. Anything unexpected is logged under Deviations, with the date and the reason.
- **Paired comparisons.** Every arm runs on the same tasks with the same models, so each task yields a pair (with vs without). The effect is the difference in solve rate.
- **Statistics.**
  - Exact McNemar test on the pairs that disagree.
  - 95% confidence interval from a bootstrap clustered by problem family (10,000 resamples), because variants of one problem are correlated.
  - Holm correction when a claim is made per model.
  - **Confirmed** only if the interval excludes 0 **and** p < 0.05.
- **Held-out knowledge.** Kel learns only from "fit" problems. Scored "test" problems are variants of those (transfer tasks), plus unrelated problems (control tasks, where Kel should have nothing to offer). Test problems are never imported into Kel.
- **Grading.** DS-1000's own unchanged test harness decides pass or fail (the "gold" grade). Generated code is screened and run in an isolated subprocess with a timeout and no secrets.
- **Isolation.** Experiments use only local Postgres databases (`127.0.0.1:55432`, `kel_*`). Production is never touched.
- **Re-runs.** Only infrastructure failures (API errors, rate limits) are re-run. A graded attempt is never re-run.
- **Models.**
  - Open models on General Compute: gemma-4-31B-it, gpt-oss-120b, deepseek-v3.2, temperature 0.
  - Claude Sonnet through fresh Claude Code subagents.
  - Open-model prices are placeholders, so cost results are indicative.

### Rounds 1–3: knowledge as a note pasted into one prompt (single-shot)

The model answers in one reply, with no tools. A script calls `find_ways` once and pastes what Kel returns above the problem, under a neutral "may or may not apply" header. Every round uses fresh problem families.

| Round | Question | Knowledge base and tasks | Arms (notes) | Primary rule |
| --- | --- | --- | --- | --- |
| 1 (pilot) | Do Kel's Procedures help, beyond routing? | 60 fit problems (40 with variants, 20 distractors); about 80 transfer tasks and 20 controls | A none · B Kel's Procedure (steps, APIs, pitfalls; no code) · C oracle (the task's own family Procedure) · D placebo (another family's Procedure) · E plain retrieval (nearest past problem plus its code, BM25) · A′ repeat of A | per model B − A, Holm-corrected |
| 2 | Does returning the verified code with the Procedure help? | 60 new fit families; about 96 transfer tasks per model | A · B · **Bc** (Procedure + verified code) · Cc (oracle + code) · E · Bw (Kel's retrieval shown as a worked example) | per model Bc − A significant **and** pooled interval above 0 |
| 3 | Do the knowledge-side improvements help (verified examples, related examples)? | a fresh production-like database: 150 fit problems, real ingestion and judging; all 82 variants of 30 families plus 7 controls | A · B (flags off) · **K** (flags on: Procedure + verified example + up to 3 related examples) · E | pooled K − A |

**Routing in rounds 1–3.**
- **Setups:** "full Kel" (routing over the arm-B attempts) and "routing without knowledge" (over the arm-A attempts), both computed from the recorded attempts. They use the real `recommend_models` ladder over the 3 open models and Sonnet.
- **The check:** a step is accepted when a realistic check passes (the first test case, or a smoke run when a problem has one test), never the hidden grading tests. The delivered answer is then graded by the full tests.
- **Targets:** reliability targets 0.5 to 0.9.

This is where the 32–62% saving comes from. The earlier BigCodeBench demo ran the same loop on a small sample: 40 fit and 20 held-out tasks.

### Rounds 4–7: knowledge delivered to an agent working in a loop

The model works as an agent: it can list, read, search and edit files, with a budget of 20 tool calls and no code execution, and writes its answer to `solution.py`. All four rounds reuse round 3's 89 tasks (82 transfer, 7 control) and knowledge base, so they compare only how knowledge is delivered.

| Round | Question | Arms | Primary |
| --- | --- | --- | --- |
| 4 | Does Kel help when used the product's way? | **AG** agent alone · **KN** round 3's note pasted into the prompt · **KP** the product: MCP instructions in the system prompt, the `plan_and_run` workflow as the user message, `find_ways` as a tool the agent may call, repo claims from `survey_repo` | KP − AG pooled (not confirmed, +3.3) |
| 5 | Do the round-4 fixes and the Claude Code hook recover what KP lost? | **AG5** fresh baseline · **KP5** KP with the fixes (related examples, suggested candidate, lighter workflow, call governor) · **KH** the hook: Kel's lookup runs on the task before the agent starts, and the shipped hook's own formatter (`hook.mjs`) appends its text to the prompt; no workflow | KH − AG5 pooled (confirmed, +8.5) |
| 6 (running) | Which knowledge features does the hook need? | **KH0** both new features off · **KHnR** related examples off · **KHnS** suggested candidate off · **KHr** round-5 hook re-run (replication and Kel's run-to-run noise). Round-5 KH is the comparator, justified by a replay: 234 of 267 lookups identical | KH − KH0 pooled |
| 7 | Does the hook help a frontier model? | Sonnet through Claude Code subagents, in batches of 6 tasks from different families: **AG7** Sonnet alone · **KH7** Sonnet with the MCP instructions and the hook's text per task. Both arms run side by side, because Sonnet is not deterministic | KH7 − AG7 (not confirmed, +1.2) |

**Recorded every round:**
- every `find_ways` call and its outcome (resolved, ambiguous, no match);
- what the hook delivered (procedures, related examples, suggested candidate, text length);
- steps, tokens, and tokens and dollars per solved task.

### Routing simulation (after round 7, descriptive)

Replays the real per-task results of rounds 5 and 7 as a ladder: cheap model first, the next model only if the answer fails, Sonnet last. The check is DS-1000's hidden tests, a perfect check, so the result is an upper bound. It is not preregistered and not a routed system.

### Next: real repositories (prepared, not run)

- **SWE-bench Verified** (`experiments/swebench/`, run by Chaitanya).
  - **Split:** issues are split per repository by date. Kel learns only from the earlier 60% (the train pool) and is frozen. Held-out later issues are graded by the official SWE-bench harness.
  - **Model:** gpt-oss-120b, temperature 0; the step budget is chosen by calibration and then frozen.
  - **Arms:** A0 none · K Kel's note · E plain retrieval · C1/C2 placebos · A0r repeat of A0 (noise and time-matched baseline) · KP product workflow · **KH hook** (added 2026-09-28, before calibration).
  - **Primary:** K − A0, with a five-part decision rule: beats A0, beats the placebo, no repository regresses, cost at most 1.2 times, beats the time-matched repeat. KP and KH get their own product verdicts under the same rules.
- **Claude Code with Kel** ([experiment_claude_code_with_kel.md](experiment_claude_code_with_kel.md), draft).
  - **Arms:** plain Claude Code vs routing with a check vs routing with a check plus Kel's knowledge vs knowledge alone, run through real headless Claude Code on SWE-bench issues.
  - **Primary:** two endpoints, both required: at least 30% lower cost, and success non-inferior within 3 points.
