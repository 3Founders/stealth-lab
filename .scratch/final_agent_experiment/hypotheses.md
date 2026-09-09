# Pre-registered hypotheses -- written BEFORE any trial runs

Written against `tasks.jsonl`'s 8 tasks and `environment.json`'s two Stealth
configs (`B_default`, `B_unverified`). These predictions are frozen alongside
`protocol.md`; the analysis in `final_report.md` (once trials exist) must
compare observed results against these, not rationalize after the fact.

## The central, load-bearing prediction

**`B_default` (production-default `allow_unverified=False`) is predicted to
show NO measurable difference from arm A on most tasks.** This is not a weak
hypothesis dressed up as strong -- it follows directly from a real,
independently-confirmed fact: all 3 procedures admitted this session are
`verification_state='candidate'`, and `require_verified=True` (the real
default `LocalAgentRunner.run()` and `find_best_way` both use) excludes
candidate-state procedures from retrieval. The existing corpus's only
`verified` procedures (~7, per this session's own earlier characterization)
are confirmed synthetic/demo content unrelated to any of these 8 tasks. So
`B_default` retrieval is expected to find nothing relevant, fall through to
the same ad-hoc path arm A takes, and differ from A only by the (small,
real) overhead of the retrieval attempt itself.

**A null result on `B_default` is therefore the EXPECTED, CORRECT outcome
given current corpus content -- not evidence the mechanism is broken.** If
`B_default` instead shows a LARGE difference from A, that itself is a
finding worth investigating (either an unexpected retrieval match, or a
confound in trial isolation).

## Question-by-question predictions (task section 17's 11 questions)

1. **Did Stealth increase task success?** Predicted: no measurable difference
   on `B_default` (both arms should succeed or fail based on raw agent
   capability, since Stealth surfaces nothing). On `B_unverified`, predicted
   POSSIBLE small positive effect on T1/T3/T5/T7 (tasks where the
   structural-summary-before-full-read procedure could plausibly be
   retrieved and matter) -- but the procedure is untested at Stealth-context
   scale (only n=1 measurements per source in Better-Ways testing), so this
   is a weak, not confident, prediction.
2. **Did Stealth reduce tokens?** Predicted: `B_unverified` may show a
   measurable reduction specifically on T1/T5/T7 (large-file/broad-search
   tasks) if the structural-summary procedure is actually retrieved and
   used -- this is literally the mechanism's own claimed effect
   (77-92.8% reduction on the underlying primitive, per Better-Ways
   testing). `B_default` predicted flat.
3. **Did Stealth reduce model calls?** Weak prediction, same direction as
   tokens, likely smaller effect size (retrieval itself costs one extra
   call on B that A never makes).
4. **Did Stealth reduce tool calls?** Ambiguous a priori: retrieval adds
   tool calls (search/check_applicability) even if it changes NOTHING about
   subsequent execution when it finds no match (`B_default`'s expected
   case) -- so `B_default` may show SLIGHTLY MORE tool calls than A, not
   fewer, purely from the overhead of asking. This would not be a Stealth
   failure; it is the honest cost of checking.
5. **Did Stealth reduce latency?** Predicted: `B_default` slightly WORSE
   (retrieval round-trip adds wall-clock time with no compensating benefit
   when nothing matches). `B_unverified` uncertain -- could go either way
   depending on whether retrieved-procedure execution is faster than
   ad-hoc exploration.
6. **Did Stealth reduce retries?** No strong prior; retries are mostly a
   function of the underlying model's raw competence and the injected task
   difficulty (T2's fault), not obviously coupled to any of the 3 admitted
   procedures. Predicted: no consistent effect either arm/config.
7. **Did Stealth reduce cost?** Follows directly from tokens/model-calls
   predictions above -- flat-to-slightly-worse on `B_default`, possibly
   better on `B_unverified` for T1/T5/T7 specifically, if and only if
   retrieval actually surfaces and the agent actually uses the retrieved
   procedure (not guaranteed even when it's found -- the agent must still
   choose to follow it).
8. **Did Stealth preserve correctness/verification?** This is the hard
   requirement, not optional: predicted YES on both configs -- neither
   `check_applicability` nor the ad-hoc fallback should ever cause a task
   that A would have solved correctly to be solved incorrectly under B. Any
   observed regression here is a release-relevant finding regardless of any
   efficiency gain (task section 10's dominance rule).
9. **Which Stealth capabilities actually mattered?** Predicted, if any:
   structural-summary-before-full-read (T1/T5/T7). Predicted NOT to show up:
   parallel-agent-git-worktree-isolation (T4 is a weak single-agent proxy
   for a fundamentally multi-agent technique -- see T4's own confound note)
   and defer-tool-schema-loading-until-needed (this procedure's own
   admission record already carries the caveat that it was measured on a
   proxy system, not StealthLab's own MCP surface -- no strong reason to
   expect it changes THIS agent's behavior at all, since nothing in this
   experiment's own tool-calling setup varies whether tool schemas are
   deferred).
10. **Is there evidence of a meaningful composition effect?** T7 is the
    designed test for this. Prediction: WEAK, if any -- 8 tasks x up to 3
    trials each is not enough statistical power to confidently detect a
    composition effect distinct from a single-mechanism effect; expect this
    question to remain genuinely open after this pilot, not resolved by it.
11. **What remains unproven?** Predicted, going in: the entire premise of
    `B_unverified` itself (whether admitting a `candidate`-state procedure
    into an agent's real retrieved-and-used path, not just measuring the
    underlying primitive in isolation, produces a genuine end-to-end
    benefit) is UNTESTED before this experiment and will likely remain only
    PARTIALLY tested after one small pilot -- 3 trials per task is not
    enough to distinguish a real effect from noise for anything but a large,
    consistent difference.

## Falsification conditions (stated up front, not invented after seeing data)

- If `B_default` shows a consistent, non-trivial IMPROVEMENT over A on
  multiple tasks, the "candidate procedures are invisible to default
  retrieval" premise is wrong somewhere (either this analysis's
  understanding of `require_verified`'s default, or the corpus/DB state at
  trial time differs from what was characterized here) -- investigate before
  trusting the result.
- If `B_unverified` shows WORSE correctness than A on any task, that is a
  genuine negative finding for the admitted procedures' real-world
  reliability, not something to explain away.
- If neither config shows ANY measurable difference from A on ANY task
  across all 8, the honest conclusion is C (NO MEASURABLE ADVANTAGE) for
  this pilot's scope, not "the experiment was flawed" -- unless a specific,
  identified confound explains it (e.g. the smoke-test in environment.json
  reveals LocalAgentRunner's live path is actually broken, which would be a
  different, infrastructure-level finding, not a product-value finding).
