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
6. **Grading dataset `SWE-bench/SWE-bench_Verified`** (`grading.dataset`, revision 78f471bf) instead of
   `princeton-nlp/SWE-bench_Verified`: swebench 5.x builds each test from the dataset's `image` column, which only
   the maintained copy has (`KeyError: 'image'` otherwise). Checked field by field: same 500 ids; identical
   base_commit, patch, test_patch, FAIL_TO_PASS, created_at, problem statement, env commit, version. Only
   PASS_TO_PASS differs, on 2 instances, both in the TRAIN pool: astropy__astropy-7606 (1 test removed),
   django__django-10097 (5 removed) -- the maintainers' removal of broken/flaky tests. The design, the prompts
   and every held-out instance are unaffected.
7. **swebench pinned to 4.1.0; entries 5 and 6 reverted.** swebench 5.0.2's Modal path is broken upstream
   (`AttributeError: 'TestSpec' object has no attribute 'setup_env_script'`; its own TODO at
   `modal_eval/run_evaluation_modal.py:164`), so the gold check errored on 12/12. 4.1.0 grades
   `princeton-nlp/SWE-bench_Verified` directly and accepts `--cache_level`, so grading is back on the frozen
   dataset. 4.x imports the POSIX-only `resource` module at import time; on Windows a no-op stand-in
   (`winshim/resource.py`, only on the path on Windows) replaces it -- it is used only to raise the local
   open-file limit, which Modal grading never needs. The harness itself is unmodified.
8. **Back to swebench 5.0.2 + modal 1.5.5, with a transport patch (`modal_compat.py`); entry 7 superseded.**
   The 4.1.0 gold check errored 12/12 with `FAILED_PRECONDITION: The legacy Sandbox filesystem API is no longer
   supported`: Modal removed `sandbox.open` server-side, and every released swebench still calls it. So the
   official Modal path cannot run unpatched in any version. `modal_compat.py` changes two functions in
   `harness/modal_eval/run_evaluation_modal.py` and nothing else: `write_file` uses
   `sandbox.filesystem.write_text`, and `get_instance_image` starts from the instance's official prebuilt eval
   image (`test_spec.image`, the same image the Docker harness runs) instead of 5.0.2's unfinished rebuild.
   The pylint cgroup write is made best-effort. Patch applied, eval script, log parser and report are the
   harness's own. `check_env.py` pins the patched file's sha256 (`modal_compat`). Entries 5 (`--cache_level`
   dropped) and 6 (grading dataset `SWE-bench/SWE-bench_Verified`) apply again.
   Third fix, found in the one-instance smoke test (psf__requests-1724 resolved remotely, but no local report):
   the remote result carried `log_dir` as a Linux PosixPath that a Windows client cannot unpickle, so the result
   was dropped. The remote side returns it as a str; the client saves under its own `get_log_dir` path.
   `grade.py` runs the harness with PYTHONUTF8=1 on Windows (it writes logs in the locale encoding otherwise).
   Smoke test after the fix: report.json saved locally, resolved: true.
