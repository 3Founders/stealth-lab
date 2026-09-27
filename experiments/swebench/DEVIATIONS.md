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

## 2026-09-27 -- speed, before calibration (protocol-neutral; nothing scored)

9. **Grading dedup:** an arm's attempt that generate.py REUSED from A0 (byte-identical prompt, attempt and
   patch) takes A0's grade (`copied_from`) instead of being graded again; copied only when the patch is identical
   and A0's grade is a real verdict (grade.py `reused_a0_grades`).
10. **Parallel pre-scoring stages:** learn.py `name` and notes.py `queries` (independent temperature-0 calls),
    notes.py `K` (find_ways against frozen Kel: each instance depends only on its own query) and kprod.py `survey`
    (one independent agent run per repo) run concurrently. learn.py `extract` runs repos concurrently but keeps
    each repo's order exactly as the sequential run did, because the identity/dedup judge may merge Procedures
    within a repo.
11. **GCE streaming grading backend** (gce_queue.py): the official harness on Compute Engine, fed per patch
    through a GCS queue while generation runs; sharded by instance so each image is pulled once per worker.

## 2026-09-28 -- arm KH and DS-1000 round 5's delivery fixes, before calibration (nothing scored yet)

12. **Arm KH (the Claude Code knowledge hook) added; `KNOWLEDGE_RELATED_EXAMPLES` and `KNOWLEDGE_SUGGESTED_CANDIDATE`
    on for every step.**
    - Why: DS-1000 round 5 (`experiments/ds1000/PREREGISTRATION_5.md`) confirmed the hook: +8.5 points, CI
      [+2.7, +14.4], p 0.004. The agent-driven product path was not confirmed (+4.1, p 0.25). SWE-bench is the
      repo-level test of the same claim.
    - What KH is: KP's system prompt (MCP instructions), tools, claims.md and budget. The user's message is the
      issue with the hook's own text appended; no `plan_and_run`. The hook text is `find_ways` on the issue, in
      its own MCP session, formatted by `packaging/npm/lib/hook.mjs` via `ds1000/hook_format.mjs`. KH always runs
      fresh and is never graded by copying A0.
    - Flags: set from `experiment.json` `kel_settings` (`related_examples`, `suggested_candidate`) by `swe_env.py`
      and checked in `verify_after_import`. They only add fields to `find_ways`'s reply; `notes.py K` reads
      outcome/procedures/candidates only, so K's notes are unchanged. They reach KP and KH.
    - KP and KH now run with `find_ways`'s governor (each episode its own MCP session), as in production and in
      round 5. The governor only refuses or caches repeated requests.
    - Analysis: `decision_KH` ("HOOK HELPS") under rules 1, 3, 4, 5 with KH in place of K. Survey tokens are charged
      to KH as to KP. Secondaries add KH − A0r, KH − K, KH − KP.
    - Requires Node.js 18.17+ on PATH; `generate.py --arm KH` refuses without it.
    - The SWE-rebench config (`../swebench_rebench/experiment.json`) is unchanged: no KH, flags off.
