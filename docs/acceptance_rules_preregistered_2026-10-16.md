# Acceptance rule for a cheap model's answer: pre-registered 2026-10-16

Written BEFORE the rule results on any data (the survey-s3-ch2 and survey-s4-ch1 results, and the rules table on
survey-s3-ch1, had not been looked at). Committed so that the date is on record.

The question: when a cheap model returns a patch, accept it (deliver it, no Opus) or escalate to Opus?
Ground truth for judging a rule: the hidden FAIL_TO_PASS tests. They grade the rules and are never a signal.

## Signals: only those a real project has when the task is handed out
- E: the project's own existing tests still pass, judged against the unchanged code (no NEW failure)
- T: our written tests pass (written before the fix; they fail on the unchanged code by assertion)
- T*: T, except that tests NO candidate answer passes count as no evidence (they likely guessed the interface:
  SWEPatchTest, Lu et al. 2026; Otter/e-Otter++). T* = T, or every candidate fails T.
- S: the answer changes source code, not only tests, docs or config
- F: the answer is focused: at most 3 source files and at most 60 changed lines (limits from the APCA literature on
  patch fragmentation, not from our data)
- X: the answer adds no catch-all exception swallow (`except:` / `except Exception:` with pass, return or continue
  in the added lines). A known overfitting pattern (Invalidator; Ahmed et al. FSE 2026)
- A: two independent cheap answers agree: both change the same set of source files (offline: the two stage 3
  agents' answers; "pass each other's tests" cannot be judged offline, so it is not part of A)
- R: a cheap reviewer says "fixes" (one call; lib/review.mjs REVIEW_SYSTEM as committed in c607fe5; gpt-oss-120b;
  temperature 0). "Unsure" is not "fixes".
- Rno: the reviewer says "does_not_fix" (used as a VETO: LLM judges falsely accept ~17-22%, so a "fixes" verdict
  alone should not accept, while a concrete "does not fix" is worth acting on; Zhan et al. ESEM 2026, Guo et al. 2026)

## Candidate rules (fixed now; no thresholds tuned on the data)
1. T                                  (today's veto, the baseline)
2. E and S
3. E and S and (T or R)
4. E and S and R
5. E and S and A
6. E and S and (A or R)
7. E and S and F and X and not Rno    (cheap static + regression gate, reviewer as veto)
8. E and S and F and X and T* and not Rno
9. E and S and T* and (A or R)

## How one is chosen
- For each rule: accepted, accepted-and-wrong, right-but-rejected, and the cost estimate
  (accepted -> cheap cost; rejected -> cheap cost + Opus ~$1.13).
- Choose: the most right answers accepted SUBJECT TO wrong-accepted <= 10% of accepted
  (each wrong accept costs a solved task vs Opus). Ties -> the simpler rule (fewer signals).
- Design set: survey-s3-ch1 + survey-s4-ch1 (17 issues). Check set: survey-s3-ch2 (15 issues, not looked at
  while choosing). The chosen rule must hold on the check set, or the conservative option (rule 7) is used.
- Report with the counts, not only percentages; n is small (~35 answers per set).
- Final confirmation on different repositories: the main series (sqlglot, jsonargparse).

## Clarifications, added before the rules table was looked at (after survey-s4-ch1 showed two measurement problems)
- E unknown (the existing tests could not be run or judged for that issue): counts as "no new failure", as the
  product's baseline rule treats tests that cannot run on the unchanged code. How many answers this applies to is
  reported with the table.
- An answer whose hidden-test verdict differs between two runs on the same saved patch (stage 3 vs stage 4) has an
  unknown label: it is left out of the scoring and counted separately.
- E is measured with the existing-test run fixed (by file, read back per test id; it had stopped whole when one id was
  not found). Stage 4 is re-run for all judged issues with that fix before the table is made.

## Product consequence, decided now from the literature (not the data)
A cheap agent must not iterate against our written tests: refining code until generated tests pass raises false
accepts to ~60% (Ahmed et al., FSE 2026). In the chosen rule's product form, the cheap agent's own check is the
project's check (E); our written tests and the reviewer judge the finished answer afterwards.
