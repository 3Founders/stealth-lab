You are running StealthLab's evaluation on MY (Chaitanya's) Modal account and MY Claude subscription. Anuj and his Claude
built and tested everything; you set it up, run it, watch it, and send the results back. Do not change product code
unless a run is blocked, and tell me before you do.

## 0. Rules (read first)
- Never print, echo, log or commit an API key, token or DSN. Load keys BY NAME through the scripts below; do not open
  or print `backend/.env` (not even the variable names).
- Stage files by name (no `git add -A`), never force-push, never push to `main`.
- Launch Modal runs with `--detach`, in the background, and NEVER wrap the launch in `timeout`. Killing the local
  launcher cancelled a whole run once. Finished issues are kept on the volume, so re-running the same command with
  the same `--run` name resumes where it stopped.
- **The main series (step 4) runs only after Anuj or I explicitly say "go".** Smoke tests and stage 3 may run freely.
- Do not waste resources: if a run shows a broken setup (every issue failing the same way, auth errors, quota errors),
  stop it (`python -m modal app stop --yes <app id>`), find the cause, and fix it before rerunning.

## 1. Setup
```
git clone https://github.com/3Founders/stealth-lab && cd stealth-lab
git checkout feat/ct-integration-5        # the harness, with the product (feat/stealth-locate) merged in
pip install modal && python -m modal token new        # my Modal account
```
Needs: Python 3.11+, Node 20+, `uv`, and Claude Code logged in to my subscription.

Secrets (two Modal secrets; the scripts read values by name, nothing is printed):
1. `claude setup-token` -> set its token as CLAUDE_CODE_OAUTH_TOKEN in your shell (not in any file), then
   `python experiments/abtest/modal_series.py secrets --claude`        (creates "claude-code-auth")
2. Put my keys in `backend/.env` (GENERAL_COMPUTE_API_KEY and/or general_compute_api_key1, Z_AI_KEY, plus any of
   JEV_*, OPENROUTER_API_KEY, GEMINI_API_KEY, VOYAGE_API_KEY I have), then
   `python experiments/abtest/modal_series.py secrets --keys --env-file backend/.env`   (creates "stealthlab-keys")
   It prints the key NAMES it found; tell me which are missing.

On Windows, set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` before any `modal run` (otherwise collecting results
crashes on a check-mark character).

## 2. Smoke test (about 10 min, a few cents)
```
python -m modal run experiments/abtest/modal_survey.py --stage 3 --n 2 --seed 1 --run survey-s3-smoke --workers 2
```
Pass: 2 issues reported, each with agents `claude` and `stealth`, no "auth"/"quota"/"credit" errors. If the test writer
reports a usage-limit error, that is my Claude limit: say so and stop.

## 3. Stage 3 of the pipeline survey (48 issues; about 2-3 h)
What it measures: a cheap model (glm-5.3-flash), driven by lean Claude Code and by StealthLab's own loop, on 48 real
SWE-rebench issues. Each answer is judged by StealthLab's own tests AND by the hidden real tests. Every answer's patch is
kept, so later acceptance rules can be judged offline without paying again.
```
python -m modal run --detach experiments/abtest/modal_survey.py --stage 3 --n 48 --seed 1 --run survey-s3-ch1 --workers 4 > stage3_launch.log 2>&1 &
```
Watch: `python -m modal volume ls stealthlab-series survey/survey-s3-ch1/stage3.parts | grep -c .json` (count done),
`python -m modal app list` (still running?). Check every ~10 min; do not poll faster.
When it ends, collect:
```
python -m modal run experiments/abtest/modal_survey.py --stage 3 --run survey-s3-ch1 --collect
```
That prints the table and writes `experiments/abtest/.local/survey/survey-s3-ch1-stage3.jsonl`.

## 4. Main series: ONLY after an explicit "go"
```
python -m modal run --detach experiments/abtest/modal_series.py --repos sqlglot,jsonargparse --max-issues 8 --turns 4 --arms A,C,C0 --run ct-series-v4 --sequential-arms --testfirst-model glm-5.3 --gc-key-name general_compute_api_key1 > series_launch.log 2>&1 &
```
(Use `--gc-key-name GENERAL_COMPUTE_API_KEY` if that is the only General Compute key in my secret.) First run
`python experiments/abtest/modal_series.py plan --repos sqlglot,jsonargparse --max-issues 8` and show me the plan. A
series stops by itself on auth/quota errors (exit 5); report it, do not just relaunch.

## 5. Send the results back
Commit ONLY the result files, on a new branch, and push:
```
git checkout -b results/chaitanya-2026-10-16
git add experiments/abtest/.local/survey/survey-s3-ch1-stage3.jsonl     # plus the series' result files, by name
git commit -m "results: stage 3 (survey-s3-ch1) on Chaitanya's Modal" && git push -u origin results/chaitanya-2026-10-16
```
(`.local` may be git-ignored: use `git add -f` for exactly these files.) Then paste me the printed tables, the total
time, and any errors.
