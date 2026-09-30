# Running this SWE-bench experiment cheaply and quickly

Research date: 2026-09-27

This document compares generation and grading options for the experiment in `experiments/swebench/`. It does not change the frozen protocol. Any change to grading backend, provider, worker count, or environment pinning must be recorded in `experiments/swebench/DEVIATIONS.md` before calibration is frozen.

## 1. What the repository actually does

The live runner is `experiments/swebench/generate.py`; grading is `experiments/swebench/grade.py`; the machine-readable protocol is `experiments/swebench/experiment.json`; operator instructions are in `docs/knowledge_side_improvements.md`.

The pre-run design in the operator document is:

- `princeton-nlp/SWE-bench_Verified`, `test` split.
- Eight scored repositories, projected at about 480 instances.
- About 290 train-pool instances used with A0.
- About 190 held-out instances run through five arms: A0, K, E, C1, C2.
- A projection of roughly 1,240 graded predictions, not 500.
- A projection of roughly 150-250M model tokens, or about 100-200k tokens per episode.
- `gpt-oss-120b` through the OpenAI-compatible endpoint selected by `EXPERIMENT_BASE_URL` and `EXPERIMENT_API_KEY`.

`design.py` has not yet produced a local design file, so these counts are projections, not measured outputs.

Generation edits local git worktrees and does not need SWE-bench images. However, the current `generate.py` calls `check_env.require_pinned()`, and `check_env.py` currently requires a reachable Docker daemon even for generation/calibration. Removing that gate for generation is a small code change and must be recorded as a deviation. Modal grading avoids the image/disk requirement but does **not** automatically remove the current Docker gate in `check_env.py`.

The generation runner already has bounded episode parallelism: `--workers` (default 4) drives a thread pool over instances. The useful change is tuning `--workers`, not building a new pool.

## 2. Executive recommendation

### Cheapest practical setup

- **Generation:** DeepInfra base `openai/gpt-oss-120b`, pinned for every scored arm.
- **Grading:** official SWE-bench harness on Modal, after `grade.py` is extended with a Modal path.
- **Expected generation cost:** about **$10-26** for the projected 150-250M tokens, excluding judge/extraction spend.
- **Expected Modal grading cost:** about **$20-78** for 1,240 predictions under a 1-core/2-GiB to 2-core/4-GiB, 5-10 minute range. This is an estimate, not a Modal per-instance price.
- **Modal Starter credit:** the $30/month free compute may be insufficient for the conservative case plus image builds. Use a dedicated workspace and budget.

### Best speed/cost balance

- **Generation:** Groq `openai/gpt-oss-120b`.
- **Grading:** Modal.
- **Expected generation cost:** about **$36-94** for the projected tokens.
- **Independent throughput measurements range widely:** Artificial Analysis has reported about 471 tok/s; OpenRouter's live provider table reported about 306 tok/s on a Groq endpoint. Re-probe before freezing.

### Fastest

- **Generation:** Cerebras `gpt-oss-120b`.
- **Grading:** Modal.
- **Expected generation cost:** about **$65-138** for the projected tokens.
- **Throughput:** vendor claims reach about 3,000 tok/s; Artificial Analysis has measured around 1,900 tok/s and OpenRouter's live table around 771 tok/s. Treat all three as time-sensitive observations, not guarantees.

### Fallback

If Modal is unavailable, use Epoch AI's optimized images with Docker under WSL2. The full Verified set is reported as 30 GiB, so it fits the reported 52 GB free disk. That conflicts with the operator document's 200 GB grading-machine requirement and must be recorded as a deviation. The official locally built image path normally needs at least 120 GB, which does not fit.

## 3. Grading comparison

| Option | Published cost / credit | Speed | Limits | Setup | Fit with official harness | Per-instance artifacts |
|---|---|---|---|---|---|---|
| **Modal, official `--modal true`** | Modal lists a cheaper Functions rate and a 3x Sandbox rate. The SWE-bench Modal runner uses sandbox execution: about $0.00003942/core/s and $0.00000667/GiB/s. Starter includes $30/month free compute and 100 containers | Modal claims 500 Verified tasks in about 7 minutes; this is vendor marketing, not an independent benchmark for this workload | Starter: 100 containers and 10 GPU concurrency; Modal also documents a 4,000 concurrent-container Function ceiling | `modal setup`, then the official harness flag | **Best fit**, but current `grade.py` must be changed to pass it | Version-dependent. The legacy harness saves per-instance artifacts under `logs/run_evaluation/...`; pin the `swebench` version and verify the layout |
| **sb-cli hosted evaluation** | Free hosted grading; quota is account/subscription specific | mini-SWE-agent docs say typical results within about 20 minutes, bounded by the slowest instance | Public docs example shows **1 remaining Verified test run**. The quota page says quotas refresh periodically; it does not publish a universal Verified quota | `pip install sb-cli`, `sb login`, submit | Poor fit for 6+ scored runs unless the account has enough quota | `get-report` is documented as an aggregate report plus response JSON, not a per-instance `report.json` API |
| **Epoch AI images + local Docker/WSL2** | Free, MIT-licensed images; 30 GiB for 500 Verified, 67 GiB for 2,290 images | 62-73 minutes for Verified on one 32-core/128-GB Linux VM; about 8 s/sample in that setup | x86_64 images are complete; ARM64 set is partial and untested | Pull Epoch images by name | Good local fallback where the installed harness accepts the image template; Modal builds remotely instead of using these local images | Normal official harness artifacts, subject to the same version pin |
| **Runloop** | Devbox CPU $0.108/CPU-hour; RAM $0.0252/GB-hour; storage $0.00034236/GB-hour; site advertises $50 new-account credits | Sub-second devbox starts; built-in benchmark execution | Usage-based; no official per-instance rate | Create a Runloop benchmark integration | Built-in SWE-bench support, but not the official harness; adapter required | Runloop-native output unless the official harness is deployed inside the devbox |
| **E2B** | Published comparisons report about $0.0504/vCPU-hour and $0.0162/GiB-hour; plan free tiers vary by source/account | Firecracker microVMs; vendor and third-party figures range from under 200 ms to roughly 300-800 ms | Hobby/Pro concurrency and session limits apply | SWE-ReX/custom adapter | No official harness integration | Adapter-defined |
| **Daytona** | $200 signup credit is publicly reported; Linux vCPU/RAM rates were not retrievable from the public page reviewed here | Container cold starts are fast; published startup claims are vendor figures | Account/verification dependent | Custom adapter | No official harness integration | Adapter-defined |
| **Northflank** | $0.01667/vCPU-hour and $0.00833/GB-hour, PAYG per second, from Northflank's own comparison | Sub-second microVM claims; no fixed session limit | BYOC available; more orchestration work | Deploy the official harness on sandboxed services | Generic compute only; no official SWE-bench adapter | Official reports only if the official harness is deployed there |
| **Morph** | Fast Apply: $0.80/M input, $1.20/M output; about 10,500 tok/s claimed | Very fast edit merging | Not a test sandbox | OpenAI-compatible Apply API | **Not a grading backend.** Changing the agent's edit mechanism would be a protocol deviation | None |
| **Blaxel / Fly Sprites / Vercel Sandbox / Cloudflare Sandbox** | No single comparable rate; free tiers, idle billing, and concurrency differ materially | Sub-second to multi-second starts | Plan-specific limits | Custom adapter | No official SWE-bench integration | Adapter-defined |

### Modal cost estimate

Modal does not publish a SWE-bench per-instance price. The official Modal evaluation path runs sandboxes, so this estimate uses Modal's Sandbox rate card:

```text
1 physical core, 2 GiB, 600 seconds:
CPU: 1     x 600 x $0.00003942 = $0.023652
RAM: 2     x 600 x $0.00000667 = $0.008004
Total:                              $0.031656 per instance

500 instances:                      about $15.83
1,240 predictions, 10 min:          about $39.25
1,240 predictions, 5 min:           about $19.63
2 cores, 4 GiB, 10 min:             about $78.51
```

At the frozen 1,800-second timeout, a 2-core/4-GiB sandbox that consumes the full timeout for every instance would be about $236. That is a worst-case ceiling, not the expected bill. Image builds, retries, startup, and actual memory above request can add cost.

The functions rate card is about one third of the sandbox rate, but this estimate does not assume the harness bills as a plain Function.

### Modal wall time

The projected 1,240 predictions at 5-10 minutes each total 103-207 sandbox-hours. With 20-way parallelism, the compute-only wall time is roughly 5.2-10.3 hours; at 50-way, roughly 2.1-4.1 hours. Tail instances, retries, image builds, and provider behavior extend that.

The repository's current local `max_workers=4` implies about 26-52 hours for the same projected grading work. Modal's advantage requires a separate Modal parallelism setting; `max_workers` is the local Docker knob.

Modal's 500-task/7-minute statement is a vendor claim and should not be used as this experiment's SLA.

### Where reports end up

Pin the `swebench` version before relying on paths. The repository's `grade.py` currently expects the legacy layout under its `runs/` working directory:

```text
experiments/swebench/runs/logs/run_evaluation/<run_id>/<tag>__<model_id>/<instance_id>/report.json
```

The model segment is produced from the tag and model id, for example `test_A0__gpt-oss-120b`. Newer harness/CLI documentation also describes `evaluation_results/<run_id>/results.json` plus `instance_results.jsonl`; if the installed version writes that layout, `grade.py` must be updated before a scored run or it will mark every instance as an error.

## 4. Generation provider comparison

All endpoints are OpenAI-compatible. Provider changes must be pinned before freeze and must not differ by scored arm.

| Provider | gpt-oss-120b input/output per 1M | Throughput observations | Limits relevant here | 150-250M token estimate |
|---|---:|---|---|---:|
| **DeepInfra base** | $0.037 / $0.17 | Artificial Analysis about 46 tok/s; OpenRouter live table about 56 tok/s for a bf16 endpoint | Dynamic/account limits not published in the reviewed primary docs | about **$10-26** |
| **OpenRouter cheapest routed providers** | Roughly $0.03 / $0.17 at the cheapest providers; standard route pricing has changed over time | Provider-dependent and can change between requests | Secondary provider comparisons report a 5.5% credit-purchase fee; verify in OpenRouter billing docs | about **$9-26** before/after a small purchase fee |
| **Groq** | $0.15 / $0.60; cached input about $0.075 | Artificial Analysis about 471 tok/s; OpenRouter live table about 306 tok/s | Account limits; use response headers/backoff | about **$36-94** |
| **Together AI** | $0.15 / $0.60; batch up to 50% cheaper | Provider/measurement dependent | Dynamic per-model limits; no fixed public threshold | standard about **$36-94**; batch potentially about **$18-47** |
| **Fireworks** | $0.15 input / $0.015 cached input / $0.60 output; batch 50% off | Default ceilings published as 21.6M prompt TPM and 216k generated TPM; adaptive | Account-wide request ceiling up to 6,000 RPM with payment/credits | standard about **$36-94**; batch about **$18-47** |
| **Cerebras** | $0.35 / $0.75 | Advertised up to about 3,000 tok/s; Artificial Analysis about 1,900; OpenRouter live table about 771 | Developer: 1M TPM and 1k RPM; free trial only 5 RPM/30k TPM/1M TPD | about **$65-138** |
| **General Compute** | Not publicly verified | Protocol default; one working key was reported by the owner | Must be probed with the exact model and concurrency | Unknown |
| **Other routers** | Not substitutes for the frozen `gpt-oss-120b` model | N/A | N/A | N/A |

### Token-cost arithmetic

For 120M input + 30M output tokens:

```text
DeepInfra: 120 x $0.037 + 30 x $0.17 = $4.44 + $5.10 = $9.54
Groq:      120 x $0.15  + 30 x $0.60 = $18.00 + $18.00 = $36.00
Cerebras:  120 x $0.35  + 30 x $0.75 = $42.00 + $22.50 = $64.50
```

For an output-heavy 125M input + 125M output split:

```text
DeepInfra: $4.63 + $21.25 = $25.88
Groq:      $18.75 + $75.00 = $93.75
Cerebras:  $43.75 + $93.75 = $137.50
```

Reasoning tokens are billed as generated tokens. The final bill depends on reasoning effort and how much context is resent each turn.

These ranges exclude the separate judge/extraction providers used by `learn.py` and `notes.py`: JEV/NLI, Gemini/Gemma, and General Compute extraction calls. That spend is not represented in the generation token total.

### Provider recommendation

- **Cheapest:** DeepInfra base or a pinned cheapest OpenRouter provider.
- **Best balance:** Groq, because an agent loop is latency-bound and sequential turns compound output speed.
- **Fastest:** Cerebras, after a short calibration probe.
- **Do not mix providers across scored arms.** Provider routing is part of the treatment.

## 5. Parallel generation

The repo already runs episodes through a bounded `ThreadPoolExecutor` via `generate.py --workers` (default 4). Tune this rather than adding another pool.

Recommended sequence:

1. Probe the chosen endpoint with 8, 16, and 32 workers on calibration instances.
2. Select the highest worker count with no sustained 429 rate and acceptable completion latency.
3. Respect provider-specific dynamic/adaptive limits and `x-ratelimit-reset` headers.
4. Retry only the failed episode. Never restart a completed arm.
5. Key results by `instance_id`. Files are appended in completion order, but `grade.py` sorts prediction ids before grading, so output order is cosmetic rather than a grading defect.
6. Keep one provider for train and all scored arms.

SWE-ReX is a sandbox/command-runtime abstraction. This experiment's generation phase deliberately never runs code, so adopting SWE-ReX for generation would change the protocol. mini-SWE-agent's batch runner is a useful implementation reference, not a replacement for the frozen StealthLab agent.

## 6. Disk, Windows, and WSL2

The official Docker guide asks for at least 120 GB free, 16 GB RAM, and 8 CPUs. `--cache_level env` still needs roughly 100 GB, and `instance` caching can approach 2 TB.

The operator document for this experiment requires 200 GB+, while the reported machine has 52 GB free. Therefore:

1. **Preferred: Modal.** No local grading image stack.
2. **Epoch fallback:** 30 GiB for Verified fits physically, but violates the protocol's 200 GB operator requirement. Record the deviation, use WSL2, size the Docker virtual disk, and avoid locally built images.
3. **Do not use the default locally built `env` image path on 52 GB.** It does not fit.
4. **Do not plan around x86 emulation on ARM.** Epoch's ARM set is partial and untested; independent experiments report roughly 6.3x slowdown for the x86-only remainder.

## 7. Known harness and benchmark risks

- **Docker gate:** `check_env.py` currently requires Docker even for generation. Modal does not remove that software gate; change the checker or record why it remains satisfied.
- **Harness version:** `check_env.py` records `swebench_version` and fails on drift. Install once before the first scored step, record the version, and never upgrade mid-experiment.
- **Report layout:** the repo's collector expects legacy `report.json` paths. Verify them immediately after the Modal gold check.
- **Cache by run ID:** the harness reuses an instance result for the same `run_id` and instance, even if the prediction changed. Never reuse a scored run id for a new patch. `grade.py --gold` uses a fixed id, so use a fresh id when re-checking gold after an environment change.
- **Timeout:** the protocol uses 1,800 seconds per instance; heavy SymPy/Matplotlib tasks can hit it.
- **Modal hangs:** there are reported cases where tests and reports completed but the remote process did not exit. Keep the timeout and inspect the downloaded report before declaring failure.
- **Test poisoning:** older harness versions allowed model-added files to collide with the official `test_patch`. Fixes landed, but pin the harness and run a poison-patch regression before scored grading.
- **Flaky/broken instances:** community `KNOWN_BAD.md` lists gold failures, flaky tests, external-service dependencies, and weak PASS_TO_PASS cases. Use it diagnostically; do not silently change the frozen denominator.
- **OpenAI's 2026 audit:** OpenAI reported flawed tests that reject functionally correct solutions in 59.4% of 138 audited difficult Verified tasks. This is a reason to report failures honestly, not to curate after scoring.
- **Contamination:** OpenAI reported that frontier models could reproduce gold patches for some Verified tasks and stopped reporting Verified scores. This experiment measures paired memory benefit, but the limitation belongs in the write-up.
- **Modal egress:** Modal documents chargeable network egress beginning October 1, 2026 at $0.04/GiB after plan allowances.
- **Cost accounting:** `experiment.json` has null `price_per_mtok`; `analyze.py` reports tokens but not dollars. A0-reuse records are copied into each reusing arm, so naive per-arm token summation overstates the provider bill. Query-writer calls in `notes.py` are not in the attempt files at all.

## 8. Setup commands

Run each block from the repository root in a fresh shell.

### Install and authenticate Modal once

```bash
cd backend
python -m pip install "swebench[modal]" modal
python -c "from importlib.metadata import version; print(version('swebench'))"

# Interactive browser login; stores the Modal token locally.
modal setup
modal profile current
```

Do not upgrade `swebench` after the first pinned scored step.

### Current grading limitation

`grade.py` does not yet pass `--modal true`. Direct harness invocation is therefore a diagnostic path, not yet integrated into the frozen runner:

```bash
cd experiments/swebench/runs

python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --split test \
  --predictions_path predictions_test_A0.jsonl \
  --run_id modal_test_A0_v1 \
  --timeout 1800 \
  --modal true \
  --parallelism 20
```

Some installed harness versions expose `--parallelism` for Modal; others rely on Modal autoscaling. Check `python -m swebench.harness.run_evaluation --help` before use. Do not substitute local `--max_workers` for Modal `--parallelism`.

Before scored use, add a Modal option to `grade.py` that:

1. appends `--modal true` and a pinned Modal parallelism;
2. keeps the frozen timeout and split;
3. uses a unique run id per arm;
4. accepts an explicit run id for gold re-checks, so a fixed cached `gold_check` id cannot be reused;
5. verifies the downloaded report layout before analysis;
6. is recorded in `experiments/swebench/DEVIATIONS.md`.

The mandatory gold gate remains the repository's full calibration-set command after that change:

```bash
cd experiments/swebench
python grade.py --gold --run-id gold_modal_v1
```

Any failure must stop the run. Until `grade.py` accepts `--run-id`, use the direct harness path above with the calibration ids and a fresh `--run_id` for every re-check.

### Generation with Groq

```bash
export EXPERIMENT_BASE_URL=https://api.groq.com/openai/v1
export EXPERIMENT_API_KEY=<groq-key>

cd experiments/swebench
python check_env.py --check
python design.py
python generate.py --part calibration --arm A0 --max-steps 40 --workers 8
python generate.py --part calibration --arm A0 --max-steps 60 --workers 8
python calibrate.py
```

### Generation with DeepInfra

```bash
export EXPERIMENT_BASE_URL=https://api.deepinfra.com/v1/openai
export EXPERIMENT_API_KEY=<deepinfra-key>

cd experiments/swebench
python check_env.py --check
```

### Generation with Cerebras

```bash
export EXPERIMENT_BASE_URL=https://api.cerebras.ai/v1
export EXPERIMENT_API_KEY=<cerebras-key>

cd experiments/swebench
python check_env.py --check
```

`check_env.py` currently fails without Docker. Either keep Docker Desktop running for the check or change the checker so generation does not require it; record that change.

### Epoch local fallback

```bash
# Docker Desktop -> Resources -> Advanced: WSL2, >=8 CPU, >=16 GB RAM.
docker pull ghcr.io/epoch-research/swe-bench.eval.x86_64.django__django-13371:latest
docker system df

# Prefer dangling-only cleanup between arms. Do not delete the whole cache
# with `docker image prune -a` unless disk pressure requires a full re-pull.
docker image prune
```

## 9. Total cost estimate

Assumptions: 1,240 projected predictions, 5-10 minutes per grading instance, 150-250M generation tokens, one provider for all arms. These are planning estimates, not measured results.

| Combination | Generation | Modal grading | Total | Notes |
|---|---:|---:|---:|---|
| DeepInfra + Modal | $10-26 | $20-78 | **$30-104** | Cheapest; excludes judge/extraction spend |
| Groq + Modal | $36-94 | $20-78 | **$56-172** | Recommended balance |
| Cerebras + Modal | $65-138 | $20-78 | **$85-216** | Fastest, highest model cost |
| OpenRouter cheapest pinned provider + Modal | about $9-26 | $20-78 | **$29-104** | Provider routing must be pinned |
| sb-cli + any generation provider | about **$9-138** | $0 if quota allows | **$9-138** | Feasibility depends on Verified quota; aggregate reports need conversion |

Add separate JEV/NLI and extraction costs after measuring `learn.py` and `notes.py` usage. Do not claim the table above is total experiment spend until those ledgers are included.

## 10. Risks and decisions

1. **Provider confound:** different providers across arms invalidate the paired comparison. Pin one provider before freeze.
2. **Protocol integration:** Modal grading is not reachable through current `grade.py`; a code change and deviation record are required.
3. **Environment gate:** current `check_env.py` requires Docker even when generation does not need images.
4. **Cost accounting:** price fields are null, A0 reuse is double-counted in per-arm attempt files, and query-writer calls are omitted.
5. **Quota risk:** sb-cli's public example shows one remaining Verified test run; do not base the experiment on hosted free grading.
6. **Credit risk:** Modal's $30 free compute may not cover the conservative grading case plus image builds.
7. **Rate-limit risk:** Together and Fireworks use dynamic/adaptive limits; Cerebras Developer is 1M TPM; OpenRouter capacity varies by provider.
8. **Reasoning-cost risk:** reasoning tokens are output tokens, so the token projection can move materially with effort.
9. **Benchmark validity:** Verified contains flawed and contaminated tasks. Keep the frozen denominator and report diagnostics.
10. **Harness version risk:** pin `swebench`; test report paths, gold, and poison behavior before scored grading.
11. **No default local grading on 52 GB:** use Modal or a recorded Epoch/WSL2 deviation.

## 11. Sources

### SWE-bench and official harness

- Evaluation guide: https://www.swebench.com/SWE-bench/guides/evaluation/
- Harness API: https://www.swebench.com/SWE-bench/api/harness/
- Docker setup and Windows/WSL2: https://www.swebench.com/SWE-bench/guides/docker_setup/
- Harness source: https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/run_evaluation.py
- SWE-bench FAQ: https://www.swebench.com/SWE-bench/faq/
- sb-cli repository: https://github.com/swe-bench/sb-cli
- sb-cli quotas: https://www.swebench.com/sb-cli/user-guide/get-quotas/
- sb-cli submit: https://www.swebench.com/sb-cli/user-guide/submit/
- sb-cli reports: https://www.swebench.com/sb-cli/user-guide/get-report/

### Modal

- Pricing and free compute: https://modal.com/pricing
- Billing: https://modal.com/docs/guide/billing
- Scaling limits: https://modal.com/docs/guide/scale
- CPU/memory/disk configuration: https://modal.com/docs/guide/resources
- Network egress billing: https://modal.com/docs/guide/network-egress-billing
- SWE-bench comparison and 7-minute vendor claim: https://modal.com/resources/best-sandboxes-swe-bench-coding-agents

### Epoch AI

- Optimized registry, sizes, and timings: https://epoch.ai/latest/swebench-docker
- Image registry: https://github.com/epoch-research/SWE-bench
- Verified benchmark notes: https://epoch.ai/benchmarks/swe-bench-verified

### Generation providers

- OpenRouter model/provider table: https://openrouter.ai/openai/gpt-oss-120b
- Artificial Analysis provider measurements: https://artificialanalysis.ai/models/gpt-oss-120b/providers
- Groq pricing: https://groq.com/pricing
- Together catalog: https://docs.together.ai/docs/serverless/models
- Together dynamic limits: https://docs.together.ai/docs/serverless/rate-limits
- Fireworks pricing: https://docs.fireworks.ai/serverless/pricing
- Fireworks limits: https://docs.fireworks.ai/serverless/rate-limits
- Cerebras model card/pricing: https://inference-docs.cerebras.ai/models/openai-oss
- Cerebras limits: https://inference-docs.cerebras.ai/support/rate-limits
- DeepInfra model pricing: https://deepinfra.com/openai/gpt-oss-120b/api

### Other sandboxes and parallelism

- Runloop pricing: https://runloop.ai/pricing
- Runloop sizes: https://docs.runloop.ai/docs/devboxes/configuration/sizes
- E2B third-party pricing analysis: https://www.morphllm.com/e2b-pricing
- Northflank comparison: https://northflank.com/blog/ai-sandbox-pricing
- Morph Fast Apply: https://www.morphllm.com/fast-apply-model
- SWE-ReX: https://swe-rex.com/latest/
- mini-SWE-agent SWE-bench batch mode: https://mini-swe-agent.com/v2/usage/swebench/

### Benchmark validity

- OpenAI 2026 Verified audit: https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
- Community known-bad list: https://github.com/kimjune01/swebench-verified/blob/main/KNOWN_BAD.md
- Test-poisoning issue: https://github.com/SWE-bench/SWE-bench/issues/538
- Test/CI patch audit: https://github.com/SWE-bench/SWE-bench/issues/600
- Patch-validity study: https://arxiv.org/html/2503.15223v1
- ARM64 limitations: https://github.com/SWE-bench/SWE-bench/issues/520
