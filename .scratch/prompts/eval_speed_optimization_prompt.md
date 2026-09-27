# Optimization prompt: make the SWE-bench knowledge-transfer experiment run end to end in about 5 hours

You are optimizing the test setup of the StealthLab knowledge-transfer experiment so that **one full benchmark
run (every stage, every arm, graded) completes in about 5 hours of wall time**, as cheaply as possible,
**without weakening the protocol**. Read everything below, then produce: (1) a quantified plan, (2) the code and
infrastructure changes, (3) `experiments/OPTIMIZATION_SUMMARY.md`. Do not launch anything that spends money
beyond the smoke tests in section 8 until the plan is written and the user approves it.

---

## 0. Read first

- `CLAUDE.md`: hard rules (the lane rule, proving tests, numbers not narrative, never force-push, keys stay in `backend/.env`).
- `docs/knowledge_side_improvements.md`, section "Next experiment: SWE-bench": the **frozen protocol** and
  operator steps 0-9. The protocol is the constraint, and speed never changes what is measured.
- `experiments/swebench/`: `experiment.json`, `swe_env.py`, `check_env.py` (environment pinning), `design.py`,
  `generate.py` (the agent runner; `--workers` = thread pool; git worktrees in `cache/`), `grade.py` (the official
  harness; `grading.backend` docker|modal), `calibrate.py`, `learn.py`, `notes.py`, `kprod.py`, `analyze.py`,
  `modal_compat.py`, `DEVIATIONS.md`.
- `experiments/swebench_rebench/`: `experiment.json` (selected with `KEL_SWEBENCH_CONFIG`), `design.py`,
  `render_grading_dataset.py`, `gce_grade_startup.sh` (a Compute Engine VM that runs the official harness with
  native Docker and uploads to GCS), `DEVIATIONS.md`.
- `backend/app/execution/coding_agent.py`: the agent loop, retry/backoff policy, tool output caps (these drive
  tokens per step).
- `.scratch/eval_infra_brief.md`: GCP quotas and the infrastructure options.

## 1. The workload (per benchmark)

| Stage | What runs | Count | Depends on |
|---|---|---|---|
| Calibration | agent episodes at max_steps 40 and 60 (unscored) | 2 x 12 = 24 | nothing |
| Freeze | pick max_steps (calibrate.py), commit, pin env | 1 | calibration |
| Train pool | agent episodes, arm A0 | Verified 286 / SWE-rebench 317 | freeze |
| Grade train | official harness | same | train episodes |
| Kel learns | `learn.py`: ingest the train outcomes into Kel (LLM judge/extraction calls) | 1 pass | graded train |
| Freeze Kel + notes | `notes.py`, `kprod.py survey` | 1 pass | learn |
| Test arms | A0, A0r, KP (always fresh) + K, E, C1, C2 (fresh only where notes exist; otherwise they reuse A0's attempt byte-for-byte) | 193 x (3 + up to 4) | notes |
| Grade test | official harness | up to about 1,350 (identical reused patches can copy A0's grade) | test episodes |
| Analyze | `analyze.py` (McNemar, repo-stratified bootstrap) | 1 | all graded |

Two benchmarks exist: SWE-bench Verified (`experiments/swebench`) and SWE-rebench (`experiments/swebench_rebench`).
They are independent: they can run concurrently if resources allow.

## 2. Measurements you must use (not guesses)

**Generation** (calibration, gpt-oss-120b on General Compute, 10 clean episodes; heavily throttled, so wall time is inflated):
- median wall **896 s** per episode (max 1,444 s), median **28 steps**;
- median **187,139 prompt tokens** and **2,615 completion tokens** per episode (the whole history is resent every step);
- stop reasons: `step_budget` 5, `finished` 3, `no_tool_call` 2;
- General Compute limits on this key: **200,000 tokens/min, 100 requests/min, 10,000,000 tokens/day**. Over
  28 attempts failed as provider errors (429). A 15k-token request was refused with 194k TPM headroom
  left, because the **daily** cap was the one binding (the error text says "per minute").

**Grading:**
- GCE n2-standard-8 (8 vCPU/32 GB, us-central1), official harness 5.0.2 + native Docker, 3 SWE-rebench gold tasks,
  `max_workers=3`: **completed in 100 s including pulling 12.4 GB of images**, and no Docker Hub rate limiting seen
  on 3 pulls (anonymous).
- Modal: Verified gold 12/12 cost cents; an uncapped 522-task fan-out spent **$30.47** and saved 7 reports, because
  of a per-image build layer and submission-order result saving (both since fixed or avoidable).
- Local PC: 16 GB RAM, 14 cores / 20 threads, 42 GB free disk, Docker Desktop 29.7.2.

**GCP quotas** (project `project-cbf120d5-4a54-4b36-968`): Compute Engine **12 vCPU global** (32 per region,
N2 32, C3 8), 2 TB disk/region (250 GB SSD), 8 instances/region, preemptible 0. Cloud Run: 20 vCPU + 40 GiB per
region, 3 regions, 1,000 jobs, 10 GiB disk per instance. A quota increase can be requested.

## 3. The arithmetic you must beat (show your version of it)

- Tokens per benchmark is about 1,800 episodes x 190k, **roughly 340M tokens**. In 5 hours that is **about 68M
  tokens/hour, about 1.1M tokens/minute** sustained. General Compute's key allows 0.2M/min and 10M/day, which is
  **roughly 34 days**. So the model endpoint is the critical path, not grading.
- Episodes in parallel needed: 1,800 episodes x (unthrottled wall per episode) / 5 h. At 5 min per episode that is
  **about 30 concurrent episodes**.
- Grading: about 1,650 gradings. At about 1-3 min each (measured 100 s for 3 in parallel including pulls), about
  **10-30 parallel graders** keep up if grading streams alongside generation.

## 4. Levers to evaluate (quantify each: time saved, cost, protocol risk)

**A. Model throughput (the critical path)**
1. A provider whose limits sustain at least 1.1M TPM for **one fixed, pinned model**: measure actual TPM/RPM/day
   limits via response headers (see `backend/.env` for the configured providers; never print keys).
   Candidates: General Compute (ask for a raise), Gemini Flash (a pinned dated version), others. **One model for
   every arm and stage** (the protocol pins model id and host).
2. Multiple keys or providers for the same model **only if the provider's terms explicitly allow it**, the
   model's weights and version are provably identical (the same served build), and decoding is identical.
   Otherwise it is a confound. Document it.
3. Cut tokens per episode **without changing the agent's behaviour across arms**: prompt caching (if the provider
   supports cached input at the same outputs), tool-output caps, history compaction (`coding_agent.py` has a
   compactor). Any change applies to **all arms identically** and is decided before calibration, or is logged as
   a deviation and forces re-running calibration.
4. Retry and backoff tuning so 429s don't burn wall time (`backoff_seconds` in `coding_agent.py`).

**B. Generation runtime**
5. Where episodes run: the PC (git worktrees are CPU-light) vs a cloud VM near the model endpoint. Measure
   per-step latency both ways.
6. `generate.py` concurrency: one process per arm vs one scheduler across arms; worktree creation cost (partial
   clones cached in `cache/`); disk use per concurrent worktree.

**C. Grading**
7. **Stream grading**: grade each patch as soon as its episode ends (a queue), so grading adds minutes, not
   hours, to the critical path.
8. **Deduplicate**: an arm that reuses A0's attempt has a byte-identical patch, so copy A0's grade and don't
   re-grade (record the copy). Also skip empty patches (already `empty_patch`).
9. **Group by image**: grade every arm's patch for one instance back to back on the same machine, so each image
   is pulled once. Pre-pull a stage's images before its patches arrive.
10. Backend choice: GCE VM(s) with native Docker (official harness unmodified, local image cache), Cloud Run Jobs,
    or Modal (capped concurrency, results saved as they complete). Size machines: CPU/RAM per concurrent test
    run (measure: `docker stats` during the smoke).
11. Docker Hub: 1,000+ pulls. Use an authenticated pull, an Artifact Registry remote (pull-through) repository,
    or pre-mirror, and quantify the rate-limit risk.
12. The SWE-rebench gold check (522 tasks) runs **before or during** calibration, since it doesn't depend on
    generation, and only test + calibration tasks block the test stage.

**D. Stage overlap (within the protocol)**
13. Run both calibration budgets concurrently (they're independent).
14. Run Verified and SWE-rebench concurrently if throughput allows (separate DBs, configs and runs dirs already exist).
15. `learn.py` / `notes.py` speed: they call LLM judges. Measure, parallelize, and check which provider they use
    (they must not share the agent's rate limit if that slows the critical path).
16. Anything that **cannot** overlap because the protocol forbids it (e.g. test arms before Kel is frozen): state
    it explicitly.

## 5. Hard constraints (breaking any invalidates the experiment)

- The **design is frozen** (`runs/design.json`), and so are the protocol and the pinned environment
  (`check_env.py`, `runs/pinned.json` after the first scored step). The same model, decoding, tools and budget for
  every arm; only the memory block differs (KP by design).
- The official harness's grading logic is unchanged. Transport changes (Modal, a VM) are allowed and logged in `DEVIATIONS.md`.
- A0r stays a fresh repeat run concurrently with the other arms (it measures noise and time matching). Don't
  schedule it all at the start or the end.
- Provider failures are `environmental_failure` and get retried, never scored.
- No secrets in logs, commits or the summary. Budget guardrails on every cloud resource: auto-shutdown, a
  concurrency cap, and a budget alert.

## 6. What to deliver

1. **The plan:** a timeline (Gantt-style table) for one benchmark and for both, with the critical path marked;
   per-stage concurrency; the chosen model endpoint with its measured limits; the grading backend and machine
   sizes; the estimated total cost with its assumptions. If 5 h is infeasible under the constraints, give the
   minimum feasible time and exactly which external change (e.g. a provider TPM raise to X) reaches 5 h.
2. **Code changes** (lane `measure:`, proving tests, test counts in commits):
   - a streaming grade queue (generation writes a patch; a grader picks it up), with dedup and group-by-image;
   - reuse of A0 grades for byte-identical patches, recorded;
   - `gce_grade_startup.sh` generalized to N workers/VMs, pre-pull, and resume;
   - `generate.py` concurrency and backoff changes that are protocol-neutral;
   - `check_env.py` pinning that stays correct when grading runs on a VM and generation on the PC.
3. **Smoke tests** proving each change (section 8).
4. `experiments/OPTIMIZATION_SUMMARY.md`: the plan, the measurements you took (with commands), each lever's
   measured effect, the changes made (files), deviations logged, costs spent, remaining risks, and the exact
   commands to launch the full run.

## 7. What not to do

Don't change the design, the arms, the prompts, the agent's tools or the step budget after calibration. Don't
run the full experiment. Don't use a different model for some arms. Don't pool keys against a provider's terms.
Don't remove the A0r arm or the gold check to save time.

## 8. Smoke tests before any full run (cap spend; report each)

1. Model endpoint: 50 real agent steps across 10 parallel episodes on the chosen endpoint: measured tokens/min,
   429 count, p50/p95 step latency.
2. Streaming grade: 10 calibration patches graded as they are produced; time from patch to report.
3. Grading throughput: 30 SWE-rebench gold tasks on the chosen backend at the planned concurrency: tasks/min,
   pull time, peak RAM per task, cost.
4. Dedup: an arm with reused A0 attempts produces zero extra gradings and identical grades.
