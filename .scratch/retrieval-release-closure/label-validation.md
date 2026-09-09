# Retrieval label validation (release closure section 2)

Sample: **91 (query, candidate) pairs**, stratified across all 8 buckets and all four grade bands, fixed seed 20260908.

## Human review status

**Not completed.** No human review infrastructure exists in this environment and no human annotator is available. `human_label` is BLANK in every row of `label-validation-sample.jsonl` and awaits a human pass.

## Interim signal: independent second-model cross-check

Independent grader: `gpt-oss-120b` (General Compute; different model family from the original Claude labeller). This is a **bias probe, not a human substitute.**

| metric | value |
|---|---|
| pairs graded by both | 91 |
| exact-label agreement | 50.5% |
| agreement within +/-1 | 92.3% |
| same relevant/not-relevant class (label>=2) | 76.9% |
| exact disagreements | 45 |

### Disagreements (independent model vs original label)

| query | candidate | orig | indep | indep reason |
|---|---|---|---|---|
| migrate Java code from the javax namespace to th | java-helidon | 0 | 1 | The procedure covers Helidon development and mentions Jakarta, but doe |
| drive development with a red green refactor test | eval-driven-dev | 2 | 0 | The procedure describes evaluation-driven AI development, not the red- |
| diagnose why a metric changed or differs from ex | kpi-reporting | 1 | 2 | The KPI Reporting procedure covers exploring data sources and explaini |
| diagnose why a metric changed or differs from ex | qdrant-monitoring-debugging | 0 | 1 | The procedure shows metric‑based debugging steps but is specific to Qd |
| develop and debug a Temporal workflow applicatio | temporal-serverless | 2 | 1 | The procedure deals with deploying Temporal workers on serverless plat |
| map and document an unfamiliar codebase to onboa | uml-and-software-architecture-visu | 1 | 2 | The UML visualization procedure can help map and document a codebase,  |
| map and document an unfamiliar codebase to onboa | wiki-researcher | 0 | 3 | The Wiki Researcher procedure directly addresses mapping and documenti |
| plan a move of a dotnet solution off Oracle and  | migrating-oracle-to-postgres-data- | 2 | 3 | The procedure directly describes migrating .NET data access code from  |
| plan a move of a dotnet solution off Oracle and  | postgresql-optimization | 0 | 1 | The procedure covers PostgreSQL optimization, not the steps needed to  |
| fix automatic batching regressions in React 18 c | react18-lifecycle-patterns | 2 | 1 | Procedure covers lifecycle method migration in React 18, not the autom |
| keep a large MCP toolset from eating the context | mcp-lazy-tool-schema-loading | 2 | 3 | The procedure directly addresses lazy-loading tool schemas to prevent  |
| keep a large MCP toolset from eating the context | context-engineering | 1 | 0 | The procedure describes code schema and testing steps, not managing to |
| keep a large MCP toolset from eating the context | mcp-create-declarative-agent | 0 | 2 | The procedure explains how to select and limit tools when creating a d |
| drive a browser to exercise a locally running we | webmcpify | 0 | 1 | The procedure deals with exposing a web app to AI agents and manifest  |
| shift a legacy servlet app to the newer enterpri | dotnet-upgrade | 1 | 0 | The procedure focuses on .NET framework upgrades, not on migrating a l |
| sandbox concurrent automation processes so their | sandbox-sdk | 1 | 2 | The Sandbox SDK outlines how to isolate code execution in containers,  |
| run security static analysis as part of continuo | security-and-hardening | 2 | 1 | The procedure covers general security hardening and dependency checks, |
| upgrade an Azure workload to a higher pricing ti | az-cost-optimize | 0 | 1 | Both involve Azure resources, but the procedure focuses on cost optimi |
| transcribe an existing Twilio call recording int | azure-ai-transcription-py | 2 | 1 | The procedure covers Azure speech-to-text transcription but does not a |
| transcribe an existing Twilio call recording int | speak-summary | 1 | 0 | The procedure describes text-to-speech synthesis, not transcription of |
| transcribe an existing Twilio call recording int | twilio-voice-conversation-relay | 0 | 1 | The procedure covers real-time ConversationRelay and streaming ASR, no |
| profile and speed up a slow SQL query | supabase-postgres-best-practices | 2 | 3 | The procedure directly addresses profiling and optimizing slow SQL que |
| profile and speed up a slow SQL query | sql-code-review | 1 | 2 | The procedure includes performance optimization steps relevant to spee |
| write data-driven unit tests with pytest fixture | pytest-coverage | 2 | 0 | The procedure focuses on test coverage, not on creating data-driven te |
| write data-driven unit tests with pytest fixture | csharp-tunit | 1 | 0 | The procedure describes C# TUnit testing, not Python pytest fixtures o |
| embed a Zoom meeting inside a Flutter mobile app | zoom-video-sdk-flutter | 2 | 3 | The Zoom Video SDK Flutter procedure directly explains how to integrat |
| embed a Zoom meeting inside a Flutter mobile app | build-zoom-meeting-sdk-app | 1 | 2 | The procedure outlines building a Zoom Meeting SDK app, which is relev |
| embed a Zoom meeting inside a Flutter mobile app | zoom-apps-sdk | 0 | 1 | Related to Zoom integration but describes the Zoom Apps SDK, not how t |
| deploy a static site to Netlify | corpus-s03-deploy-a-project-to-ver | 2 | 0 | The procedure describes deploying to Vercel, not Netlify, so it does n |
| build a Slack app that posts messages from a wor | build-zoom-team-chat-app | 1 | 0 | The procedure describes building a Zoom Team Chat app, not a Slack app |
| generate a simple text-to-speech narration of an | narrator | 2 | 1 | The procedure deals with a specialized, production‑grade narration wor |
| generate a simple text-to-speech narration of an | podcast-generation | 1 | 2 | The procedure describes generating audio narration from text using Azu |
| review my code | requesting-code-review | 2 | 3 | The procedure outlines how to request a code review, directly matching |
| review my code | code-tour | 1 | 0 | The procedure creates code walkthrough tours, not code reviews, so it  |
| review my code | corpus-s04-review-ui-code-against- | 0 | 1 | The procedure performs a specific UI code compliance review, which is  |
| help me refactor this | java-refactoring-extract-method | 3 | 2 | The procedure offers a concrete Java extract‑method refactoring workfl |
| improve the quality of this codebase | refactor | 2 | 3 | The refactor procedure directly targets improving codebase quality thr |
| improve the quality of this codebase | receiving-code-review | 1 | 2 | The procedure outlines a systematic code review process, which is a us |
| isolate and fix a flaky failing test | test-coverage-improver | 2 | 0 | The procedure deals with coverage measurement and adding tests, not wi |
| isolate and fix a flaky failing test | runtime-behavior-probe | 1 | 2 | The procedure outlines a systematic probing approach that can help inv |

## Bias assessment

- Same-class agreement (the metric that actually drives precision/recall, since 'relevant' := label>=2) is **76.9%**.
- A materially biased model-labelled benchmark would show low same-class agreement and a systematic direction (independent model consistently harsher or softer). Inspect the disagreement table for direction: independent labelled LOWER in 19 cases, HIGHER in 26.

## Records requiring human review

- **Minimum:** the 91 pairs in `label-validation-sample.jsonl` (stratified representative sample).
- **Full set for a strong release claim:** all **855** labelled candidates in `tests/data/retrieval_eval_v1.jsonl`.

## Instructions for the human reviewer

1. Open `label-validation-sample.jsonl`. For each row, read `query` and look up the procedure by `candidate_name` (or its `retrieval_document`).
2. Assign `human_label` in {0,1,2,3} per the rubric (0 irrelevant / 1 related-not-useful / 2 useful / 3 excellent). Add a one-line `human_reason`.
3. Do NOT look at `model_label` or `independent_model_label` first.
4. Re-run `scripts/eval_retrieval_quality.py --measure` after merging the human labels back into `retrieval_eval_v1.jsonl` to get human-anchored precision/recall and the human-anchored relevance threshold.

## Release implication

The relevance-gate threshold and the before/after precision numbers are currently derived from **model-assigned labels only**. They are adequate for engineering iteration and for the RELATIVE comparisons in this closure (old vs new representation, model vs model — the labels are held constant across those). They are **not** a sufficient basis for an absolute release-quality precision claim until the human pass above is done. This is a NAMED open item on the release gate.
