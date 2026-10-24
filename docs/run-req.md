# What the Bedrock open-model run needs (Part 4 of the series-10 task)

Status 2026-10-24: nothing has run on AWS and nothing has been spent. This lists everything needed before the first model
call, who supplies it, and what stops the run. Items marked **verify** are things I believe but have not confirmed against
AWS today; they must be checked in the console before they are relied on.

## What the run is

A test of whether open models served by Amazon Bedrock can join (or replace members of) the cheap-model list that
dispatch uses (today: gemma-4-31b-it, gpt-oss-120b, minimax-m2-7, glm-5.3-flash). It runs on repositories the series did
not use, through `experiments/abtest/modal_survey.py`, and is judged by the Part 4 rule in `docs/series10_prereg.md`:
accepted-and-correct at least the best current cheap model; cost per task no higher; median seconds no more than 1.5x;
errors and rate limits no more than the best + 2.

Paid by the AWS credits only. Budget: $100 in credits, of which at most $20 for this run and $10 kept back. The $70 set
aside for Claude overflow is unused (Claude is not covered by the credits; the series runs `--auth subscription`).

## A. From you (cannot be done from here)

1. **Confirm the account and region.** Region is us-east-2 (Ohio), confirmed. The models below must be available there.
2. **A key pair for the run, in a fresh IAM user or role** (not the one pasted in chat, which is to be rotated):
   - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (and `AWS_SESSION_TOKEN` if temporary);
   - permissions limited to `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:Converse`,
     `bedrock:ConverseStream`, `bedrock:ListFoundationModels`, `bedrock:GetFoundationModelAvailability` on the chosen models;
   - nothing else (no S3, no IAM, no billing writes).
   Alternative: a Bedrock API key (a bearer key) if the OpenAI-compatible endpoint is used (see C.1). **verify**
3. **Model access.** In the Bedrock console for us-east-2, make sure each chosen model is available to the account
   (some open models need an access request or a Marketplace subscription, and that can take time or cost a fee).
4. **The model table**, one row per model, filled in from the Bedrock console and pricing page, not from memory:

   | field | why |
   |---|---|
   | exact model id in us-east-2 (and the inference-profile id if one is required) | what the call names |
   | input and output USD per million tokens, on-demand | cost per task, and the spend ledger |
   | context window and max output tokens | the step agent's prompts must fit |
   | tool calling supported (yes/no, and through which API) | the executor needs tool calls |
   | quota: requests per minute and tokens per minute | the run's parallelism |
   | whether cross-region inference is on (it may send requests outside us-east-2) | data-path declaration |

   Suggested shortlist to look at first (open-weight, usually on Bedrock; availability to be verified): gpt-oss-120b,
   gpt-oss-20b, a Qwen3 coder or large model, DeepSeek, Kimi, GLM, MiniMax, Llama. I will not guess prices.
5. **Service quotas.** Request increases for the chosen models' tokens-per-minute and requests-per-minute if the defaults
   are low (they usually are for new accounts). Increases are granted by AWS and can take a day.
6. **Budget alerts** in AWS Budgets at $60 and $85 of the $100 credits, with email to you. Also confirm that the credits
   apply to Bedrock open models and to nothing that is charged to the card (the Marketplace is the usual exception). **verify**
7. **Yes before the first spending call.** The first smoke run is the first AWS spend; it will not start without it.
8. **Permission to recreate the Modal secret** `aws-bedrock` with region us-east-2 (it currently says us-east-1). That
   needs the new key pair given to the `secrets --bedrock` command from a shell variable, never as an argument.

## B. From me (code, all offline-tested before any spend)

1. **A Bedrock path for the cheap executor.** Today a profile in `experiments/abtest/exec.json` needs an https
   `openai_base_url` and a bearer key (`packaging/npm/lib/executors/profile.mjs`). Bedrock's native API signs requests
   with the IAM key pair (SigV4), which a bearer-key profile cannot do. One of these is needed (decision C.1):
   - a profile pointing at Bedrock's OpenAI-compatible endpoint with a Bedrock API key; or
   - a LiteLLM (or small proxy) process in the container that turns the IAM pair into an OpenAI-compatible local endpoint,
     listed as `http://localhost:PORT/v1` (the profile code allows http only for loopback).
2. **Profiles** in a separate file (not the series `exec.json`, which stays as pre-registered), one per Bedrock model:
   `model_id`, `key_env`, `input_per_mtok`, `output_per_mtok` from table A.4, and declarations `region: "us"`,
   no invented `zdr`, `no_training` or `dpa_signed` (unknown stays absent).
3. **Credentials in the container**: the `aws-bedrock` secret attached to the survey functions
   (`modal_survey.py` currently attaches only the keys secret), with `scrub()` removing the values from every log and row.
4. **A spend ledger for this run** on the volume, per unit, with a hard stop at $20 for Part 4. Cost is computed from
   token counts and the table A.4 prices, plus a margin, and rounded up.
5. **A repository list**: repositories not used in the series (sqlglot and jsonargparse are used), each with at least 8
   issues passing the gold check. Chosen from `swe_series_task.py` candidates.
6. **Smoke test** (3 tasks, one model at a time, capped at $1 in total) before the real run; it checks the call works,
   tools work, usage and cost come back, and the key is not in any output.
7. **Tests**: offline tests for the profiles, the ledger and the cap, and for the scrub, shipped with the code.
8. **Result files** under `experiments/abtest/results/bedrock_open/` and a section in `docs/series10_results.md`.

## C. Decisions needed (my default in brackets)

1. Endpoint: OpenAI-compatible Bedrock endpoint with an API key, or IAM through a local LiteLLM proxy? [IAM through a
   local proxy, because the credentials you gave are an IAM pair and the key never leaves the container.]
2. Cross-region inference profiles: allowed or not? [Not allowed; stay in us-east-2.]
3. Which models and how many? [At most 4, chosen by price and tool-calling support from the table.]
4. Tasks per model and repositories. [8 issues per repository, 2 repositories, same tasks for every model.]
5. Does a passing Bedrock model replace GLM in the series cheap-model list? [No. The series stays as pre-registered; a
   Bedrock model only joins the list in a separate, later run.]

## D. Spend controls

- Credits: $100 total. Part 4 ceiling $20, set in code and checked before every call, with room for calls in flight.
- Smoke runs count against the $20.
- AWS Budgets alerts at $60 and $85 (A.6) are the second layer, because the code only sees what it records.
- Provisioned throughput is never bought; on-demand only. Anything priced per hour is out.

## E. When I stop and report instead of working around it

- The credits do not cover a chosen model, or a chosen model is not available in us-east-2.
- A tool-calling or context limit makes a model unusable for the task.
- A call would take the ledger past $20, or AWS shows spend the ledger does not.
- A quota error persists after a retry, or a key, token or secret value appears in any output.
- A gold check leaves a repository with fewer than 8 issues.

## F. Security

- The key pair pasted in chat on 2026-10-24 is to be rotated after the series; use a new, narrowly scoped pair here.
- Keys live in `backend/.env` and the Modal secret only; never in an argument, a log, a commit or a result row.
- The long-lived memory (`.stealth/library`) saved a pasted OAuth token once (scrubbed locally, never committed); do not
  paste keys into the chat if a file or shell variable can carry them.
