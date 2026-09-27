# Deviations from the frozen protocol

Each entry: date, what changed, why. Written before continuing (docs/knowledge_side_improvements.md).

## 2026-09-27 -- before calibration (nothing scored yet)

1. **Platform: Windows 11 x86_64 (Python 3.13.0), not Linux.** The operator machine has ~52 GB free disk, far
   below the 200 GB the local SWE-bench images need.
2. **Grading on Modal, not local Docker.** `experiment.json` `grading.backend = "modal"`; `grade.py` passes the
   official harness's own `--modal true` (harness unmodified; reports land in the same
   `runs/logs/run_evaluation/<run_id>/` tree). `check_env.py` no longer requires Docker in that mode (it requires
   modal + credentials) and pins `grading_backend`; `docker_server` is pinned as `"n/a (graded on modal)"`.
   `grade.py --run-id` added (reports are cached per run id).
3. **Agent endpoint: General Compute** (`api.generalcompute.com`, `gpt-oss-120b`), keys read from `backend/.env`
   (`swe_env.py` loads only KEL_SWEBENCH_DSN / EXPERIMENT_BASE_URL / EXPERIMENT_API_KEY from it).
4. **`environmental_failure` = `stop_reason == "api_error"`** (was `error and not patch`). An episode cut short
   by a provider failure is infrastructure, not the arm: it is retried and never scored, even if it left a
   partial patch. Otherwise rate limits (General Compute 429s are frequent) would count against whichever arm
   hit them.
5. **`--cache_level` dropped from the harness call**: swebench 5.0.2 (the pinned version) no longer accepts it
   (`unrecognized arguments: --cache_level env`). `grading.cache_level` in experiment.json is now unused.
