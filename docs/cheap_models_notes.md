# Cheap models in kell: who does what, and what to revisit

Notes from the 2026-10-29/30 runs on kell-try (`docs/results_2026-10-30.md`). Written to come back to when changing
the model line-up (qwen, mimo and others are being considered).

## The line-up (as configured in `~/.stealthlab/exec.json` on the test machine)

| Model | Provider | Price in / out per M tokens | Cached input | Notes |
|---|---|---|---|---|
| glm-5.3-flash | z.ai (`api.z.ai`) | $0.15 / $0.50 | $0.03 | always thinks (cannot be turned off; `reasoning_effort: low` works); the only model on its own provider |
| gemma-4-31b-it | General Compute | $0.09 / $0.34 | none | needs `no_system_role` (a system turn gets "Invalid base64 image data"); leaks `<\|channel>thought<channel\|>` tokens |
| gpt-oss-120b | General Compute | $0.037 / $0.17 | none | `reasoning_effort: high`; sometimes replies with nothing (no text, no tool call) |
| minimax-m2-7 | General Compute | $0.21 / $0.84 | none | |

The three General Compute models share one key and its **~200k tokens-per-minute** quota.

## Who does what (defaults, and where each is set)

| Role | Model | Setting |
|---|---|---|
| Answering questions and chat | glm-5.3-flash | `STEALTHLAB_ANSWER_MODEL` (lib/answer.mjs) |
| Questions about the code (reads the repo, read-only) | the answer model | `STEALTHLAB_ANSWER_READING=off` to go back to one call |
| brief, explain, describe, locate (side helpers) | gemma first, glm if gemma is slow (6 s hedge) | `STEALTHLAB_BRIEF_MODEL`, `STEALTHLAB_EXPLAIN_MODEL`, `STEALTHLAB_WAY_DESCRIBE_MODEL`, `STEALTHLAB_LOCATE_MODEL`; fallback `STEALTHLAB_HELPER_FALLBACK_MODEL`, hedge `STEALTHLAB_HELPER_HEDGE_MS` (lib/helper_call.mjs) |
| Code changes | all four race (2 at a time per provider); the plan's ranking orders them | exec.json `executors.stealth.models`; the ranking: the routing prior + local outcomes |
| Review (judge of answers, changes, findings) | claude-sonnet-5-5 | `STEALTHLAB_REVIEW_STRONG_MODEL`; limit `STEALTHLAB_REVIEW_TIMEOUT_S` (240) |
| Session model when everything cheap fails | the user's own (Opus) | - |

## What happened in the runs

| Model | Changes won | Findings | Failures seen |
|---|---|---|---|
| glm-5.3-flash | 3 (missing example, error message, lint workflow) | 1 (typo) | none that cost a verdict; slow per step (~9 s) |
| gemma-4-31b-it | 2 (flatten.py twice) | 2 (typo, "yes do it") | stalls by re-reading (rule e); "Invalid base64" 400 twice; slow first token (7+ s) |
| gpt-oss-120b | 1 (flatten.py, 10-29) | 0 | an empty reply; rate limits; long searching without an edit |
| minimax-m2-7 | 0 | 0 | rate-limited, timed out; two answers rejected by the review (one made the message worse) |

Every answer and helper reply was glm's in the end: it is the answer model, and it answered the helpers whenever gemma
was slow, which was most of the night.

## Why glm dominates (not only the model)

- **Provider**: glm is alone on z.ai; the other three share one General Compute key and its tokens-per-minute quota, so
  two or three agents there throttle each other. General Compute was also slow and threw a provider bug.
- **Caching**: z.ai bills re-sent context at a fifth (glm: 428k of 455k input tokens cached on one run). General
  Compute caches nothing, so a long agent run there costs and throttles more.
- So tonight was not a fair comparison of the models.

## Risks

- One model, one provider for most turns: a z.ai outage or limit sends most turns to the session model.
- The helpers fall back to glm too: the same single point.

## To revisit

1. Spread the models across providers, or give each General Compute model its own key, then compare them fairly.
2. Add qwen / mimo (cheap, strong): a new model is ranked from its model card on day one
   (`backend/app/routing/catalog.py` live cards; `docs/routing_priors.md`, "models added or removed"), then from outcomes.
3. A second answer model on another provider, as glm's fallback for questions.
4. Per-model step budgets: glm makes its first edit late (call 69 on one solved issue); gemma loops on re-reads early.
5. minimax's place in the race: two rejections in two tries here; check on more tasks before demoting it.
6. gpt-oss: the empty-reply nudge (lib/agent/step_agent.mjs) is new; check whether it now finishes more runs.
