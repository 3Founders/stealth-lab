# researchplan.md - Point-by-Point Research Plan

Created: 2026-08-25 · Owner: ox-alpha sessions · Cadence: one point per turn; user advances with "next"

## Protocol

| Tag | Tool |
|---|---|
| [web] | built-in websearch (Exa-backed) |
| [exa] | Exa deep-crawl where plain search underdelivers |
| [papers] | arXiv / Semantic Scholar via webfetch |
| [openalex] | OpenAlex premium API - key stored in `backend/.env` as `OPENALEX_API_KEY` (verified working 2026-08-25) |
| [local] | in-repo docs/code/vendor forks |

Rules: every point records findings inline under its heading (single-file discipline); every claim links its source; status markers PENDING / RUNNING / DONE; contradictions with prior findings are escalated, not smoothed over. Canonical-file hygiene: this file is script-written UTF-8 only.

Key facts already banked: RippleEdits = 38 citations (momentum anchor for Pillar B) · OpenAlex indexing lag makes citation counts useless for 2026 preprints (AgentTrace/Quipu/Mandato = 0) · competitive delta scan DONE (commLLM §16).

---

## Track L - Launch-blocking (before 72h window ends)

### P1 - Actionable-rejection schema for check_procedure [web] - Status: DONE (2026-08-25)
Question: what field-for-field shape should WOULD_REFUSE payloads use so our structured refusal matches the best documented pattern in the wild?
Findings (from Evidence-Gated-Memory README, verified): every rejection returns (a) which evidence type is missing, (b) why it failed which gate (expired_evidence_block / source_system_not_allowed / llm_output_not_as_source), (c) the exact next tool to call, (d) an audit_id. Gates are deterministic YAML-schema functions - "The LLM never decides what counts as evidence; the schema does." Their tau-bench A/B: 7/8 pass, ~24x context compression, 0 false acceptances.
Adopted spec for our payload: `{verdict: WOULD_REFUSE, procedure_id, reason_class: <§36 cause>, missing: [{evidence_type, why}], next_action: {tool, args_hint}, audit_id, evidence_refs[]}`. Delta vs EGM worth keeping: we add belief-links (which superseded claim killed it) and capability-note - neither exists in EGM.
Remaining sub-task: mirror the same shape into Harness Investigator-Pipeline theory JSON when wiring that integration.

### P2 - Combined clean sharded falsifier run (C1 + B2 in one weekend of compute) [local+web] - Status: PENDING (SPEC)
Question: does the substrate *measurably help* - settling the reliability-tax question (C1) and the terminal-dominance question (B2) with data instead of the ~70% prior?
Design: two General-Compute keys = two independent 10M-token/day budgets; one tau2 instance each at concurrency <=2; `num_retries >= 4` so 429s die call-level. Arms on dev-12 first, then full corpus x2 trials: (i) gold-docs+scaffold, (ii) gold-docs-bare, (iii) `terminal_kb` shell-over-docs, (iv) current stealthlab_procedures arm as control.
Metrics: trial-pair agreement (tests C1's overhead-tax prediction), pass^1/pass^3, tokens-per-task, timeout-termination rate (<5% required to call the run clean).
Output: first clean numbers retire D2/D3; C1 verdict decides render-slim vs keep; B2 verdict decides retrieval-quality pitch vs governance pitch. Both falsifiers from assumptions.md's load-bearing ranking retired by one run.
Falsifier handling: publish whatever it says per ROADMAP P5 discipline.

### P3 - Data Manifesto exemplars -> our page draft inputs [web] - Status: PENDING
Question: what do the 5 most-trusted OSS telemetry/data policies actually say, sentence by sentence, so ours is competitive on first publish?
Targets: Sentry security/privacy/AI-TOS (already partially banked in commLLM §6), Next.js telemetry page, Go transparent-telemetry posts, VS Code telemetry docs, Langfuse self-host privacy posture.
Output: bullet-level outline of our Data Manifesto + the five commitment sentences.

### P4 - MCP Registry + directory submission mechanics [web] - Status: PENDING
Question: exact current requirements (server name rules, metadata, verification badges, review latency) for registry.modelcontextprotocol.io, Smithery, Glama, PulseMCP; plus awesome-mcp-servers PR conventions.
Output: submission checklist wired into launch-day H60-84 block.

### P5 - Explicit-vs-implicit propagation literature deep-read [papers] - Status: PENDING
Question: defend or refine the §8 scoping note. Read CLaRE-style entanglement discovery, JNO (2606.01610), ChainEdit (2507.08427) closely: what fraction of ripple failures come from implicit logical coupling vs explicit edges? Does any cheap discovery method exist we could ship as stretch?
Output: go/no-go on adding implicit-coupling discovery to month-1 vs deferring; sharpened wording for the ripple receipt.

## Track M - Month-1 receipts & flagship features

### P6 - AgentTrace + Who&When calibration harness spec [papers] - Status: PENDING
Question: exact metrics/splits/protocols to claim calibrated attribution for failures.py routes + node scorer. AgentTrace public benchmark mechanics, Who&When strict agent-and-step accuracy, SearchAuditBench rubric reuse.
Output: eval/attr_calibration spec ready to implement.

### P7 - DCR implementation digest (D2ACCI 2608.17756) [papers] - Status: PENDING
Question: how is Diagnostic-Coverage-Rate computed stage-wise; what does adopting it require in our trace schema?
Output: internal metric definition + instrumentation ticket text.

### P8 - tau3-Banking pass^k receipt protocol [local+web] - Status: PENDING
Question: digest vendored fork's evaluation.md + leaderboard-submission.md; pass^1/pass^3 semantics, user-sim disclosure norms, what "clean run" requires (sharded keys, retry floors per assumptions.md D2).
Output: receipt runbook.

### P9 - EnvACE rehearsal integration design [papers+code] - Status: PENDING
Question: their pre-commit validation loop vs our bridge/sandbox_executor; what generalizes to banking ledger states.
Output: design note for the flagship trust feature.

### P10 - CLEANER purification criteria + GATS three-tier mapping [papers+code] - Status: PENDING
Question: failure-replacement criterion specifics; mapping tiers L1/L2/L3 onto retrieval_mixins.py without breaking RRF contract tests.
Output: two implementation tickets fully specified.

### P11 - Banking typed-domain schema sources [local+web] - Status: PENDING
Question: what tau2/tau3 banking domains already define (tools, DB state, policies) vs what a typed OCM-style schema needs beyond them.
Output: schema skeleton + gap list.

### P12 - Financial-PII redaction pattern set [web] - Status: PENDING
Question: patterns/regulators' expectations beyond generic PII (account numbers, IBAN, card PANs, transaction narratives); existing open-source detectors worth reusing.
Output: rule additions to trace_redaction.py + test fixtures.

## Track S - Strategy & post-Day-30

### P13 - Competitor watchlist cadence + delta-scan rerun template [openalex+exa] - Status: PENDING
Question: monthly rerun template for the five-system matrix (+Engram, +academic cluster); alert triggers (funding, MCP-surface ships, benchmark publication).
Output: watchlist section template appended here each month.

### P14 - Preprint groundwork [papers] - Status: PENDING
Question: venue shortlist (workshop first: AIWILD/ICLR, FORGE, agentic-reliability tracks); related-work section skeleton drawing from commLLM §3 + §16; which claims are paper-grade vs marketing-grade.
Output: outline + submission calendar.

---

## Execution log

| Point | Date | Status | Key output |
|---|---|---|---|
| P1 | 2026-08-25 | DONE | WOULD_REFUSE payload spec adopted from EGM pattern + our belief-link delta |
