git pull   # feat/ct-integration-5 -> 37d4b53 or later. Same rules as before (no keys printed, files staged by name,
           # no force-push, launches with --detach and never wrapped in `timeout`; they exit at once and keep running on Modal).

Context: Anuj found and fixed two grading bugs in the survey; on the 25 valid issues the cheap model (glm-5.3-flash) via
lean Claude Code solves ~58%. This run measures what more time, more steps and a "context pack" add, and gives the
data that decides the final run's settings (docs/acceptance_rules_preregistered_2026-10-16.md, section "The final run's
settings, decided by the A/B"). No test writer runs (no Sonnet use); only GLM on z.ai. Nothing else may use the z.ai key.

IDS=$(cat experiments/abtest/.local/survey/valid26.txt)

Job 1 (R1: new settings, no context pack), about 1.5-2.5 h:
python -m modal run --detach experiments/abtest/modal_survey.py --stage 3 --run survey-ab-r1 --workers 3 --ids "$IDS" --set "SURVEY_AGENTS=claude,stealth,SURVEY_AGENT_TIMEOUT_S=1200,SURVEY_NO_WRITER=1"

Job 2 (R2: the same + context pack + the project's history), ONLY after job 1 has all 25:
python -m modal run --detach experiments/abtest/modal_survey.py --stage 3 --run survey-ab-r2 --workers 3 --ids "$IDS" --set "SURVEY_AGENTS=claude,stealth,SURVEY_AGENT_TIMEOUT_S=1200,SURVEY_NO_WRITER=1,SURVEY_CONTEXT_PACK=1,SURVEY_HISTORY=1"

Job 3 (grading + existing tests + cross-testing, CPU only, ~10 min each), after each of jobs 1 and 2:
python -m modal run --detach experiments/abtest/modal_survey.py --stage 4 --run survey-ab-r1-s4 --src-run survey-ab-r1 --workers 8 --ids "$IDS"
python -m modal run --detach experiments/abtest/modal_survey.py --stage 4 --run survey-ab-r2-s4 --src-run survey-ab-r2 --workers 8 --ids "$IDS"

Job 4 (a one-issue check of the final-run mechanics, arm C only, ~20-30 min, uses my Claude for the Opus session; run
it while job 2 runs or after; it does NOT start the main series):
python experiments/abtest/modal_series.py plan --repos jsonargparse --max-issues 1
python -m modal run --detach experiments/abtest/modal_series.py --repos jsonargparse --max-issues 1 --turns 4 --arms C --run ct-smoke-final --sequential-arms --testfirst-model glm-5.3 --gc-key-name general_compute_api_key1 --cheap-agent claude --context-pack --accept-rule "E S C Rs" --review-model claude-opus-5-5 --cheap-timeout-s 900
Pass: the issue runs to the end with no Python/Node error; the C arm's log shows dispatch lines ("dispatch: ... evidence:",
"accepted by the rule" or "not accepted by the rule"). Report what it shows; a wrong answer is fine here, a crash is not.

Progress: python -m modal volume ls stealthlab-series survey/<run>/stage<k>.parts | grep -c .json   (every ~10 min)
Stop rules: "the executor failed ... 429" on several runs -> stop that job (python -m modal app stop --yes <id>) and
report the error text. Same failure on ~5 issues in a row -> stop and report.

When all are done: collect each
  --stage 3 --run survey-ab-r1 --collect ; --stage 3 --run survey-ab-r2 --collect
  --stage 4 --run survey-ab-r1-s4 --collect ; --stage 4 --run survey-ab-r2-s4 --collect
(add --wait to a --collect if it asks), push the four jsonl files plus job 4's result/log files to
results/chaitanya-2026-10-16 (git add -f, by name), and paste the four reports and job 4's outcome. Do not start the main series.
