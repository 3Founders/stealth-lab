# Experiment speed optimization: summary (2026-09-27)

Goal: one full benchmark run of the frozen SWE-bench knowledge-transfer protocol in about 5 hours, as cheaply as
possible, without weakening the protocol. Brief: `.scratch/prompts/eval_speed_optimization_prompt.md`.
Nothing scored has run; no full run is launched without approval.

## Verdict

**About 3.4-4.9 h per benchmark is achievable. Grading is solved; the model endpoint is the one external blocker.**

| Constraint | Status |
|---|---|
| Grading throughput | **Solved.** One 12-vCPU GCE worker, official harness unmodified, streams grades behind generation (measured below). |
| Pre-scoring stages (learn, notes, survey) | **Parallelized**, with the same results as the sequential run (order kept where it matters). |
| **Model endpoint** | **Blocking.** The run needs about 2-3M tokens/min sustained for ONE pinned gpt-oss-120b route. The General Compute key allows 0.2M/min and 10M/day, which means 34+ days per benchmark. |

**The external change that reaches 5 h (pick one):**
1. **OpenRouter credits** (the key is on the free tier, with $0 credits): gpt-oss-120b pinned to one provider,
   `allow_fallbacks: false` (`experiment.json model.openrouter_provider_order`, already supported by
   `generate.py`). Listed prices are $0.03-0.05/M input and $0.17-0.25/M output, so **about $15-25 per benchmark**
   (about 457M input tokens). Throughput is not measured yet: step 1 of "What's next" measures it.
2. A General Compute limit raise to at least 3M tokens/min and at least 500M tokens/day for this key.

## Measurements (all 2026-09-27)

**Generation** (calibration, gpt-oss-120b on General Compute, clean episodes):
- median **28 steps**, max 56; median **187k prompt + 2.6k completion tokens** per episode (the history is resent every step);
- median wall 896 s per episode, **dominated by 429 backoff**: the daily cap binds, and the error text says "per minute";
- General Compute limits read from the response headers: 200k TPM, 100 RPM, 10M tokens/day. The daily remainder was 0 during the measurement window;
- git worktree per episode: **0.9-2.4 s** add, 0.4 s remove (pylint, about 1.5k files). Negligible.

**Grading** (SWE-rebench, 30 tasks spread over all 13 repos, official harness 5.0.2 + native Docker on GCE,
n2-custom-6-24576). Run A used 4 workers and no pre-pull; runs A2/B2 used 6 workers with a timed pre-pull.

| | A (pd-balanced) | A2 (pd-balanced) | B2 (**local NVMe**) |
|---|---|---|---|
| VM boot / setup | 23 s / 66 s | 59 s setup | 66 s setup |
| Pre-pull, 30 images (**54.6 GB** unpacked, 4 in parallel) | - | 556 s | **404 s (-27%)** |
| Pull per image, median / p90 | - | 56 / 103 s | **37 / 90 s** |
| Harness for 30 tasks (images cached) | 824 s (pulls included) | 234 s | 212 s |
| Per task, median / p90 / max | 89 / 157 / 215 s | 27 / 44 / 101 s | 26 / 35 / 91 s |
| Container memory, peak median / max | 41 / 276 MB | 89 / 270 MB | 79 / 300 MB |
| Host load (6 vCPU), median / max | 5.7 / 7.5 | 7.4 / 8.7 | 7.5 / 9.4 |
| Gold resolved | 28/30 | 28/30 | 28/30 |

Conclusions:
- **Grading is CPU-bound** (about 1 core per test, under 300 MB of memory): size machines by vCPU, with workers equal to vCPU.
- **Image pulls dominate**: about 1.8 GB per image, so about 950 GB for SWE-rebench's 522 images. Local NVMe unpacks 27% faster,
  and its quota is unlimited (pd-balanced counts against the **250 GB SSD quota**).
- **2 SWE-rebench tasks are broken** (their gold patch doesn't apply): `wemake-services__wemake-python-styleguide-3117`, `-3129`.
  They are excluded from every arm; the full gold check will list any others.
- Earlier failures, now fixed: SWE-rebench images need the editable-root link (eval-script prefix); Docker 29 stores
  images in `/var/lib/containerd` (the SSD must cover it); the default service account needed bucket access.

**Streaming queue (S2)**, SWE-bench Verified, `gce_queue.py workers up` then submit (worker n2-custom-12-24576,
12 harness workers, 2 striped local NVMe, Verified from HF on the worker). Patches: the 6 real non-empty
calibration patches plus the 12 calibration gold patches.
- **18/18 graded within 7 min of submission, from no VM at all** (create + boot 23 s + setup incl. RAID0 106 s + pulls).
- Harness calls: 2 patches in 70 s, 4 in 48 s, **12 gold in 88 s**.
- Gold 12/12 resolved on this backend; the real calibration patches resolved 2/6.
- The worker powers off after 20 idle minutes (cost guard).

**Dedup (S4):** proven offline (`tests/test_grade_dedup_offline.py`): a reused attempt is copied only when its
patch equals A0's and A0's grade is a real verdict; fresh arms (A0, A0r, KP) never copy.

**Endpoint (S1): not run.** General Compute had 0 daily tokens left; OpenRouter's gpt-oss-120b has no free route
and the key has no credits. See "What's next", step 1.

**GCP quotas** (project `project-cbf120d5-4a54-4b36-968`): **12 vCPU global** (binding), 250 GB SSD/region
(pd-balanced counts), local SSD unlimited, 8 instances/region, preemptible 0. Cloud Run: 20 vCPU per region.

## Timeline (plan_sim.py, measured episode profile)

Main scenario: 30 concurrent episodes at 5 s/step (to be confirmed by the endpoint smoke test), learning
parallelized (20 min, estimated), notes plus survey parallelized (10 min, estimated), grading streamed (6-minute tail):

| Stage | Min | Depends on | Notes |
|---|---|---|---|
| Gold check (522 SWE-rebench tasks) | about 120, **off the critical path** | none | Runs from t=0 during calibration and train |
| Calibration (2 budgets at once) | 5 | none | Pre-pull of train images starts here |
| Freeze + pin | 2 | calibration | |
| Train (A0) | 30 | freeze | Graded as it streams; test images pre-pulled meanwhile |
| Train grading tail | 6 | train | |
| Kel learns | 20 (est.) | graded train | Names in parallel; extraction per repo in parallel |
| Notes + KP survey | 10 (est.) | Kel frozen | Queries, find_ways and survey in parallel |
| Test A0 | 20 | notes | generate.py requires A0 before other arms |
| Test A0r, KP, K, E, C1, C2 | **99** | A0 | Graded as they stream; reused-A0 attempts copy A0's grade |
| Test grading tail + analyze | 9 | test | |
| **Total** | **3.35 h** | | 20 concurrent at 5 s: 4.56 h; 30 concurrent at 8 s: 4.89 h |

Both benchmarks at once: the grading worker needs a second 12-vCPU worker (quota raise to at least 24 vCPU), and the
endpoint needs about 2x the TPM.

## Levers (measured effect, status)

| # | Lever | Effect | Status |
|---|---|---|---|
| 1 | Endpoint with at least 2-3M TPM (OpenRouter, pinned provider) | 34+ days -> hours | **Needs credits** (see Verdict) |
| 2 | Multiple keys/providers for one model | risk: a confound across providers | Not used: one pinned provider, no fallbacks |
| 3 | Token cuts (caching, caps, compaction) | not measured | Not changed: it would alter agent behaviour; decide before calibration if at all |
| 4 | 429 backoff tuning | moot on an unthrottled endpoint | Not changed (backend lane) |
| 5 | Where episodes run | worktrees 1-2 s; the agent is light on CPU | Keep on the PC; revisit if step latency from India to the endpoint is high (the endpoint test measures it) |
| 6 | generate.py concurrency | `--workers` per arm; 7 test arms in parallel | Existing; run arms concurrently after A0 |
| 7 | **Streaming grading** | the grading tail is about 6 min instead of about 1 h per stage | **Built**: `gce_queue.py stream` |
| 8 | **Grade dedup for reused-A0 attempts** | up to 4 x 193 fewer gradings | **Built**: `grade.py reused_a0_grades` |
| 9 | **Group by image** | each image pulled once per worker | **Built**: sharding by instance hash |
| 10 | Backend and sizing | CPU-bound: 12 vCPU/24 GB, 12 workers; NVMe -27% pull time | **Built**: config `grading.gce` |
| 11 | Docker Hub limits | 90 anonymous pulls across 4 runs, **0 rate-limited** | Monitor; add a Docker Hub login or an Artifact Registry remote repository if 429s appear |
| 12 | Gold check early | about 2 h off the critical path | Plan: start at t=0 |
| 13 | Both calibration budgets at once | half the calibration time | Plan |
| 14 | Verified + SWE-rebench at once | 2x throughput | Needs quota to at least 24 vCPU and 2x endpoint TPM |
| 15 | **learn.py parallel** | sequential about 60+ min (est.) -> about 20 min (est.) | **Built** (order kept per repo) |
| 16 | Stages that cannot overlap | test before Kel is frozen; other arms before A0 | Respected |
| + | Prune train images after grading | disk from about 950 GB to about 350 GB peak | **Built**: `keep_images.txt` |
| + | Spot workers | about 60-70% cheaper grading | Available (`spot: true`); preemptible quota is 0, so untested |

## Changes (commits on main)

- `fb90bd8` grade.py copies A0's grade for reused attempts (+3 tests)
- `c32b3bb` gce_queue.py: streaming GCE grading queue, daemon workers, grade.py/check_env `gce` backend (+4 tests)
- `a73d7a3`, `3ea387c` plan_sim.py, bench_report.py, instrumented worker script (phase timings, timed pre-pull, sampler)
- `3863c46` learn.py: parallel naming, per-repo extraction (+1 test)
- `1dbf58e` notes.py queries/K and kprod survey in parallel; deviations 9-11 logged
- `e908904` worker sizing from the benchmark, striped NVMe, image pruning, spot option (+1 test)

Offline suite: `experiments/swebench/tests`, **9 passed**.

## Costs so far

Modal (September): $31.76 (the uncapped fan-out, before the fixes). GCE today: about 5 short VM runs of 6-8 vCPU for
10-20 min each, plus the streaming worker, which is **under $3** (estimate; no billing export is configured).

## What's next (in order)

1. **Endpoint smoke test** (after credits): 10 parallel episodes x 5 steps on the pinned route. Measure TPM, 429s and p50/p95 step latency, then re-run `plan_sim.py` with the measured step time.
2. Point `experiment.json model` at the chosen route (host, provider order, prices) and log the deviation. Nothing is scored yet.
3. Launch sequence, for approval:
   ```
   # SWE-rebench (repeat with the default config for Verified)
   set KEL_SWEBENCH_CONFIG=experiments/swebench_rebench/experiment.json
   python experiments/swebench/gce_queue.py prepull --parts train+test+calibration
   python experiments/swebench/gce_queue.py workers up
   python experiments/swebench/gce_queue.py gold --parts train+test+calibration     # t=0, off the critical path
   python experiments/swebench/generate.py --part calibration --arm A0 --max-steps 40 --workers 12 &
   python experiments/swebench/generate.py --part calibration --arm A0 --max-steps 60 --workers 12
   python experiments/swebench/calibrate.py   # freeze max_steps, commit
   python experiments/swebench/generate.py --part train --arm A0 --workers 30 &
   python experiments/swebench/gce_queue.py stream --tag train_A0
   python experiments/swebench/learn.py name ; ... import ; ... worker ; ... extract ; ... evidence
   python experiments/swebench/notes.py queries ; ... K ; ... E ; ... controls ; ... freeze ; kprod.py survey
   python experiments/swebench/generate.py --part test --arm A0 --workers 30 & gce_queue.py stream --tag test_A0
   (A0r, KP, K, E, C1, C2: generate.py --workers 5 each, concurrently, each with its own gce_queue.py stream)
   python experiments/swebench/analyze.py
   ```
4. Quota raise: CPUS_ALL_REGIONS 12 -> 32 (a second worker for both benchmarks at once, plus headroom).

## Remaining risks

- The endpoint's real TPM and latency (unmeasured), and provider-side throttling at 30 concurrent.
- The learn/notes durations are estimates. The first run measures them; both are now parallel.
- Docker Hub anonymous limits at 522+ pulls (none seen in 90 so far).
- SWE-rebench tasks that are broken beyond the 2 found. The full gold check decides.
