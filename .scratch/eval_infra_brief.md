# Eval infrastructure brief: optimize before anything is created (2026-09-27)

Nothing has been created or enabled yet. Every number below was read from the GCP project or measured in this
repo; estimates are marked. Goal: the fastest and cheapest correct way to GRADE SWE-bench-style patches for the
knowledge-transfer experiment (`experiments/swebench`, `experiments/swebench_rebench`).

## Workload

| Item | Count |
|---|---|
| Verified design | 286 train / 193 test / 12 calibration (8 repos) |
| SWE-rebench design | 317 train / 193 test / 12 calibration (13 repos), 522 prebuilt images (`swerebench/sweb.eval.x86_64.*`) |
| Gradings per benchmark (upper bound) | about 1,650 (train + 193 x 7 arms + calibration); fewer when an arm reuses A0's patch (identical prediction, so the grade can be copied) |
| Gold check | Verified calibration: done (12/12). SWE-rebench: 522 tasks (7 done before Modal was cut) |
| Per task | pull image (1-5 GB compressed, est.), apply patch, run eval script (tests), parse log. Typical test time 1-5 min (est.) |

Grading must stay faithful to the official harness (`swebench.harness.run_evaluation`): same image, eval script,
log parser and report. Any change is logged in DEVIATIONS.md.

## GCP project (new account)

- Account `ddda36609@gmail.com`. Billing account `01F936-B7B5C7-B3E1C5`, **open**.
- The only billed project: `project-cbf120d5-4a54-4b36-968` ("My First Project", created 2026-09-22).
  gcloud's config still points at the old `kell-509215` (no access); needs `gcloud config set project`.
- **Not yet enabled:** run, cloudbuild, artifactregistry, secretmanager. Enabled: compute, aiplatform, logging,
  monitoring, storage-api, iam, bigquery and more.

### Quotas (read 2026-09-27)

| Service | Quota | Value |
|---|---|---|
| Compute Engine | **CPUS_ALL_REGIONS (global)** | **12** (the binding limit) |
| Compute Engine | CPUS per region (us-central1, asia-south1) | 32 |
| Compute Engine | N2_CPUS / C3_CPUS / E2_CPUS (us-central1) | 32 / 8 / 8 |
| Compute Engine | PREEMPTIBLE_CPUS | 0 (spot VMs may draw from the regular CPU quota instead; unverified) |
| Compute Engine | DISKS_TOTAL_GB / SSD_TOTAL_GB per region | 2048 / 250 |
| Compute Engine | INSTANCES per region | 8 |
| Cloud Run | CPU allocation per region | 20 vCPU |
| Cloud Run | Memory allocation per region | 40 GiB |
| Cloud Run | MaxRegionsPerProject | 3 |
| Cloud Run | JobsPerProject / RunningExecutionsPerProject | 1000 / 1000 |
| Cloud Run | Job runs per minute per region | 30 |
| Cloud Run | Max ephemeral disk per instance | 10 GiB |

## Options to optimize

| | A. One Compute Engine VM, official harness + Docker | B. Cloud Run Jobs | C. Modal (fixed) | D. This PC |
|---|---|---|---|---|
| Faithfulness | **Official harness, unmodified** | Harness logic reimplemented per task (a deviation) | Harness + `modal_compat` transport patch (a deviation, gold-verified) | Official harness |
| Parallelism | 12 vCPU / 48 GB (n2-standard-12), so about 6 tasks at once (est.) | 20 vCPU/region x 3 regions, so about 30 tasks at 2 vCPU | cap chosen (10-20) | about 3 (16 GB RAM) |
| Image handling | **Local Docker cache**: each image pulled once, reused by every arm | Cold pull on every task (no shared cache) | Modal image cache; SWE-rebench needed a per-image build layer (fixable) | Cache impossible (42 GB free) |
| Est. time for 1,650 gradings | about 15-20 h (about 4 min/task, 6 parallel) | about 6-8 h (about 8 min/task incl. pull, 30 parallel) | about 3-6 h | about 1.5-3 days |
| Est. cost | about $0.65/h, so about $10-15 plus disk (spot cheaper if allowed) | about $0.02/task, so about $35 (est.) | unknown per task; the first run spent $30.47 through uncapped fan-out | $0 |
| Engineering | about 2 h (startup script, disk, run the existing `grade.py` with the docker backend) | 1-2 days; 1 job per image (1000-job cap: Verified + SWE-rebench about 1,000) | about 1 h of fixes | none |
| Risks | 12-vCPU global quota; Docker Hub pull rate limits (log in) | Docker Hub pulls per task, rate limits, cold starts | workspace re-enable, spend control | ties up the PC |

Quota increase to request (usually granted quickly): CPUS_ALL_REGIONS 12 -> 48. With it, option A runs
about 24 tasks at once (e.g. n2-standard-48, or 2 x 24 vCPU), so about 4-5 h.

## Questions for reviewers

1. VM sizing: CPU:RAM per concurrent SWE-bench task. Disk type and size for the image cache (pd-balanced vs SSD 250 GB cap).
2. Image strategy: pre-pull all images for a design (dedupe shared layers) vs pull-as-you-go; an Artifact Registry
   remote (pull-through) repo to avoid Docker Hub limits; order tasks by image so each is pulled once.
3. Scheduling: grade all arms of one instance back to back (one image pull), and skip identical patches
   (arms reusing A0).
4. Spot VMs with checkpointing (the harness caches reports per run_id, so a restart resumes).
5. Whether the LLM generation (the real bottleneck: about 700M tokens per benchmark) should run on the same VM,
   and which provider/quota makes it fastest (General Compute is capped at 10M tokens/day).
