# Step 4 research — verifiers as check types (actionlint, zizmor, ast-grep) + ast-grep-essentials

Date: 2026-09-28
Author: step-4 agent (owns the check runner and the CHECK_TYPES question)

## 0. Bottom line first

1. **`screening.CHECK_TYPES` is the wrong vocabulary and must not be extended.** It describes
   *admission screening of untrusted source text* (prompt injection, secrets, PII, license) and is
   mirrored by a DB CHECK constraint (`check_type_chk_screening_decisions`, `db/68_screening_decisions.sql`).
   A verifier verdict is not a screening decision. Extending it would need a spec + schema change and
   would corrupt the meaning of `screening_decisions`. Board question filed as **Q-STEP4-1**.
2. **A verifier verdict must be five-valued, not boolean.** All three tools conflate "clean",
   "crashed", and "found nothing to inspect" in ways `exit == 0` reads as pass. Confirmed
   empirically for ast-grep (see §4.3). This is the single most important design consequence.
3. **ast-grep-essentials at commit `73120109` does not run as a whole suite** on ast-grep
   0.43.0, 0.44.1 or 0.45.3: 30 of its 184 rules use a prelude `utils:` syntax that every
   available CLI rejects, and one bad rule aborts the entire run (exit 8). Per-rule isolation is
   required; 154/184 rules are usable, 30 are quarantined with a reason.
4. **No published false-accept or false-reject rate exists for actionlint, zizmor, or
   ast-grep.** The literature does not establish this. We must measure our own (§2.4).
5. **Pin everything.** ast-grep-essentials has *no releases* — pin by commit SHA. zizmor's
   `--format=sarif` silently forces exit 0 even with findings. zizmor honours inline
   `# zizmor: ignore` comments by default, so audited code can silence its own finding.

## 1. Sources read

### 1.1 Tool documentation (read, not summarised from memory)

| Source | URL | Read on |
|---|---|---|
| actionlint usage / exit status | https://github.com/rhysd/actionlint/blob/main/docs/usage.md | 2026-09-28 |
| actionlint install (versions, images) | https://github.com/rhysd/actionlint/blob/main/docs/install.md | 2026-09-28 |
| actionlint CHANGELOG (v1.7.12, SC2153) | https://github.com/rhysd/actionlint/blob/main/CHANGELOG.md | 2026-09-28 |
| zizmor usage / output formats / exit codes | https://docs.zizmor.sh/usage/ | 2026-09-28 |
| zizmor quickstart (full flag list) | https://docs.zizmor.sh/quickstart/ | 2026-09-28 |
| zizmor integrations (SARIF exit-0 warning) | https://docs.zizmor.sh/integrations/ | 2026-09-28 |
| ast-grep "Test Your Rule" | https://ast-grep.github.io/guide/test-rule | 2026-09-28 |
| ast-grep `sgconfig.yml` reference | https://ast-grep.github.io/reference/sgconfig | 2026-09-28 |
| ast-grep `test_case.rs` (TestCase schema) | https://github.com/ast-grep/ast-grep/blob/ff97c38d/crates/cli/src/verify/test_case.rs | 2026-09-28 |
| ast-grep-essentials `sgconfig.yml` | https://github.com/coderabbitai/ast-grep-essentials/blob/main/sgconfig.yml | 2026-09-28 |
| ast-grep-essentials LICENSE | https://raw.githubusercontent.com/coderabbitai/ast-grep-essentials/main/LICENSE | 2026-09-28 |
| ast-grep-essentials versions (npm) | https://registry.npmjs.org/@ast-grep/cli | 2026-09-28 |

### 1.2 Literature on verifier-gated agents and static-analysis precision

| Source | Identifier | Tier |
|---|---|---|
| Aleithan et al., *solution leakage / weak tests in SWE-bench* | arXiv:2410.06992 | `[PREPRINT]` |
| PatchDiff, patch correctness on SWE-bench Verified | arXiv:2503.15223 | `[PREPRINT]` |
| SWE-ABS, strengthened test suites reject 19.78% of accepted patches | arXiv:2603.00520 | `[PREPRINT]` |
| OpenAI, *Why we no longer evaluate SWE-bench Verified* (audit of 138 tasks) | openai.com/index/why-we-no-longer-evaluate-swe-bench-verified | `[PRODUCTION WRITEDOWN]` |
| SWE-bench maintainers, non-deterministic gold-patch evaluation (30/300) | github.com/ScalingIntelligence/swe-bench-lite-samples | `[TECH REPORT]` |
| SWE-bench issue #538 (test-file poisoning flips eval to `resolved: true`) | github.com/SWE-bench/SWE-bench/issues/538 | `[DOCS]` |
| SWE-bench issue #577 (harness-caused failures ≈ 8% of all failures) | github.com/SWE-bench/SWE-bench/issues/577 | `[DOCS]` |
| Habib & Pradel, *How do static bug detectors detect real bugs?* (recall 4.5%) | ASE 2018, software-lab.org/publications/ase2018_static_bug_detectors_study.pdf | `[PEER-REVIEWED]` |
| Machiry et al., *An evaluation of CodeQL* (34% FP on embedded C) | ICSE-SEIP 2022, machiry.github.io/files/embosssast.pdf | `[PEER-REVIEWED]` |
| Emelund et al., developer-confirmed FP/FN for PMD/SpotBugs/SonarQube | arXiv:2408.13855 | `[PREPRINT]` |
| BkCheck, industrial false-positive rate 76% | arXiv:2601.18844 | `[PREPRINT]` |
| Self-report vs executed verification (submit 100% / resolve 44%; 5.5× asymmetry) | arXiv:2603.25764, arXiv:2602.06948 | `[PREPRINT]` |
| *The Confidence Dichotomy* — executing a check reduces overconfidence, asking a model increases it | ACL 2026 long 520 | `[PEER-REVIEWED]` |
| EvilGenie — hardcoded / deleted tests under verifier gating | arXiv:2511.21654 | `[PREPRINT]` |
| SpecBench — reward-hacking gap grows ~27pp per 10× LOC | arXiv:2605.21384 | `[PREPRINT]` |
| RHB — hardening cut exploit rate 6.5% → 0.8%, no success cost | arXiv:2605.02964 | `[PREPRINT]` |

### 1.3 Correction to the step brief

The brief names `ast-grep-essentials` under the ast-grep org. **The canonical repository is
`coderabbitai/ast-grep-essentials`.** The `ast-grep` org owns the CLI (`ast-grep/ast-grep`).
The brief also implies a `rule-tests/` directory; the real test directory is `tests/`.

## 2. What the tools actually are (and are not)

### 2.1 actionlint — GitHub Actions workflow linter

- **License:** MIT. On `DEFAULT_ALLOWLIST` — no gating friction.
- **Current release:** `v1.7.12`. Pinned Docker image `rhysd/actionlint:1.7.12`.
- **Exit codes (official table):** `0` ran, no problem · `1` ran, problem found · `2` invalid
  command-line option · `3` fatal error. Constants: `ExitStatusSuccessNoProblem=0`,
  `ExitStatusSuccessProblemFound=1`, `ExitStatusInvalidCommandOption=2`, `ExitStatusFailure=3`.
- **Output:** human-readable and colourised by default. Machine-readable via
  `-format '<go template>'`; `{{json .}}` yields a JSON array, `{{range $err := .}}{{json $err}}{{end}}`
  yields JSON Lines. Per-error fields: `Message`, `Snippet`, `Kind`, `Filepath`, `Line`, `Column`,
  `EndColumn`. A SARIF template ships at `testdata/format/sarif_template.txt`.
- **Undeclared external dependency (important).** `shellcheck` and `pyflakes` are
  **auto-detected on `PATH` and silently used when present** — the defaults in `command.go` are
  the literal strings `"shellcheck"` / `"pyflakes"`, and `linter.go` only constructs those rules
  when the option is non-empty. shellcheck applies only to `bash`/`sh` steps; pyflakes only to
  `shell: python` steps. **Disable with `actionlint -shellcheck= -pyflakes=`** — otherwise the
  verdict depends on what happens to be installed in the runner image, which makes a recorded
  verdict unreproducible. v1.7.11 already changed a verdict by disabling SC2153, so this knob is
  load-bearing, not cosmetic.
- No network access is needed for local file checking; the image bundles shellcheck + pyflakes,
  which is another reason to pin the image.

### 2.2 zizmor — GitHub Actions security auditor

- **License:** MIT. On `DEFAULT_ALLOWLIST`.
- **Pinned image:** `ghcr.io/zizmorcore/zizmor:1.29.0` (docs' own CI pins `ZIZMOR_VERSION: 1.29.0`;
  the newest tag was not independently confirmed).
- **Exit codes:** `0` no findings (**or SARIF mode**) · `1` error during audit · `2` argument
  parsing failure · `3` no inputs collected · `11`/`12`/`13`/`14` findings whose highest severity
  is informational / low / medium / high.
- **`--format=sarif` forces exit 0 even with findings** (documented twice). "This should not be
  confused with a lack of findings." Same suppression under `--no-exit-codes` and under `--fix`
  when all fixes apply. **Never gate on exit code with SARIF.**
- **JSON:** `--format=json` is an alias for `--format=json-v1`. Flat array. Per-finding fields:
  `ident`, `desc`, `url`, `determinations{confidence,severity,persona}`, `locations[]`,
  `ignored` (bool), optional `fixes[]`. A JSON Schema is published and in SchemaStore.
- **Off-by-one trap:** `json-v1` uses **0-based** line numbers under key `row`; `plain` and
  `sarif` are 1-based.
- **Network:** offline is the default when no GitHub token is set, and `--offline` takes
  precedence over `--gh-token` and over `GH_TOKEN`/`GITHUB_TOKEN`/`ZIZMOR_GITHUB_TOKEN` in the
  environment. But **the docs make no claim about process-level egress** — `--offline` is a tool
  policy, not a sandbox. Enforce egress at the runner (`--network none`).
- **Verdict knobs that must be recorded:** `--no-ignores` (by default zizmor honours
  `# zizmor: ignore[rule]` comments in the audited file and config ignore rules, so **audited code
  can silence its own finding**), `--min-severity`, `--min-confidence`, `--persona`
  (auditor|pedantic|regular, default `regular`), `--strict-collection`. Filtering happens in
  `FindingRegistry` *after* the audits run, which is correct for a verdict.

### 2.3 ast-grep and ast-grep-essentials

- **ast-grep CLI:** MIT. **No accessible container image** — `ghcr.io/ast-grep/cli` returns
  `denied` unauthenticated and `ast-grep/cli` does not exist on Docker Hub. Installed instead
  from npm `@ast-grep/cli`, pinned.
- **Versions available:** up to `0.45.3`. `0.45.3` is what we pin.
- **`ast-grep-essentials`:** `coderabbitai/ast-grep-essentials`, **Apache-2.0** (root `LICENSE`),
  **184 rules** across 15 languages (python 48, java 36, ruby 18, cpp 15, go 11, csharp 11, c 9,
  rust 8, javascript 7, typescript 6, swift 5, kotlin 5, scala 2, php 2, html 1), **184 test
  files**, plus a `__snapshots__` dir. Layout is `rules/<language>/security/<rule>.yml` and
  `tests/<language>/<rule>-test.yml`.
- **No GitHub releases** ("There aren't any releases here") → **pin by commit SHA**
  `73120109bf45c284d0cd8a37bdd7082e80e92e87`. (A third party, pi-lens, vendors the same SHA.)
- **License discrepancy, recorded honestly:** the root `LICENSE` is Apache-2.0 and governs the
  rule files; `package.json` says `"license": "ISC"`, which describes the npm package metadata.
  Both ids are on `DEFAULT_ALLOWLIST`, so the verdict is ALLOW either way; the reason string
  records the root-LICENSE id because that is what governs the ingested content.
- **Rule YAML:** `id` (unique across the whole package, not per language), `language`, `message`,
  `note`, `severity`, `rule`, optional `utils`, `constraints`, and a package marker
  `ast-grep-essentials: true`. `id` + `message` + `note` (which carries the CWE id and
  references) is exactly the material for a Procedure body — that is why these are worth
  ingesting.
- **Test YAML:** `id` (must match the rule id), `valid: [str]` (must produce **no** match),
  `invalid: [str]` (must produce **a** match). Snapshots are separate.
- **Verdict-changing flags:** `--skip-snapshot-tests` disables output checking (leaves
  fire/no-fire only); `-U/--update-all` and `-i/--interactive` **rewrite the expected results**;
  `--include-off` is needed because `severity: off` rules are otherwise **silently skipped**.

### 2.4 What the literature does and does not establish about these three

**Null result, stated plainly: no measured false-accept or false-reject rate exists for
actionlint, zizmor, or ast-grep-essentials, and none for static verifiers used as binary gates on
LLM-produced code.** actionlint's README only *aspires* to "make false positives as minimal as
possible". ast-grep-essentials' CONTRIBUTING asks contributors to keep rules "essential" with "a
low false positive rate" — also unmeasured. The nearest measured numbers come from *different
tools on different corpora* and must not be transplanted:

| Number | Tool / corpus | Direction |
|---|---|---|
| recall 4.5% of real bugs | Error Prone / Infer / SpotBugs, 594 real Java bugs | false-accept |
| **34% FP rate**; 60% of rules had zero FPs; 20% of rules produced >60% of all FPs | CodeQL, 258 embedded C/C++ repos | false-reject |
| CodeQL Autobuild **failed to run on 184/258 repos (71%)** | same | false-accept via "no output" |
| DNF rate 52.58% (Clang-Tidy), 11.67% (Semgrep), 0.96% (Snyk) | PrimeVul, 4,659 C/C++ pairs | false-accept |
| FP rate 76% | BkCheck, industrial Java/C++ alarms | false-reject |
| **33.04% solution leakage, 12.50% incorrect tests** | SWE-bench Verified | both |
| **19.78% of accepted patches rejected** by strengthened suites; their own new tests 10.6% overfit | SWE-bench Verified, 11,041 patches | false-reject |
| **7.8% of "plausible" patches incorrect; 82.7% of that invisible to the FULL test suite** | SWE-bench Verified | false-accept |
| 30/300 (10%) instances where the evaluator non-deterministically marks the GOLD patch wrong | SWE-bench Lite | non-determinism |
| 8% of all failures are harness-caused, not agent-caused | SWE-bench Verified | false-reject |
| test-file poisoning flips eval to `resolved: true` | SWE-bench #538 | false-accept |

**The asymmetry is the finding.** Every source that measured both directions found
false-accept ≫ false-reject on the accept side (7.8%, 11.0%, 19.78%, 24%). The errors that
corrupt a memory substrate are the ones it cannot see from inside: a Procedure closed as
"verified" on a check that was never really run, or that did not cover the defect.

**Self-report is close to worthless as a verdict, directionally:** submit-rate vs
test-verified resolve-rate was 100%/44% (GPT-5) and 99%/18% (Llama 4); 62% of self-predictions on
*failing* instances were overconfident vs 11% underconfident on passing ones (5.5×); model
rankings *invert* between the two indicators; and uncertainty-driven self-correction **degrades**
Pass@1 while verification-driven correction gains 6–26pp. Executing a check reduces
overconfidence where asking a model increases it (ACL 2026). So: the subject's report never
touches the verdict column.

**Measurement caveat to carry into any doc we write:** FP definitions differ. Machiry counts a
"harmless report" (rule fired correctly, infeasible in context) as a true positive, splitting 362
TP into 158 harmless. Habib & Pradel's 4.5% vs Thung et al.'s 64–99% is *entirely* a
line-match-vs-verified-correspondence methodology difference. Any number we publish must carry its
definition and corpus.

## 3. Implications for this step's build

1. **Five-valued verdict** `{pass, fail, error, no_input, not_run}` — never a bare boolean.
   `error` and `no_input` must never be scored as pass.
2. **A liveness assertion per tool**, because "no output" parses as success:
   - actionlint: `0` and `1` are verdicts; `2`/`3` are `error`.
   - zizmor: map 11–14 by the highest severity; `0` only means pass when findings array is
     empty; `1`/`2`/`3` are `error`; and we parse JSON rather than trusting the exit code.
   - ast-grep `test`: require the summary to report `N passed` with `N > 0` and equal to the
     expected case count. **Verified necessary** (§4.3).
3. **Pin and record everything:** tool version, every threshold/suppression flag, the resolved
   rule set, and the finding count. A stored verdict without these is not reproducible.
4. **Isolation is a correctness control, not hygiene** (RHB: 87.7% relative reduction in exploit
   rate at no significant success cost). `--network none`, read-only root, dropped caps,
   no-new-privileges, non-root user, memory/cpu/pid limits, hard timeout.
5. **Never let the subject reach the verdict or the inputs** (#538).
6. **Per-rule isolation for ast-grep**, because one unparseable rule aborts the whole suite.
7. **Only verified outcomes become Procedures.** An ast-grep rule becomes a Procedure *because*
   `ast-grep test` passes its own upstream cases. A rule whose test errors is **not** a
   Procedure — it is quarantined with a reason, per hard rule "only verified outcomes become
   Procedures".

## 4. Local verification (downloaded, measured, not assumed)

### 4.1 Environment

- Docker 29.7.2 available and the daemon responds → container-backed verifiers are viable.
- `go` is **absent** → `go install github.com/rhysd/actionlint/cmd/actionlint@latest` is not an
  option; the pinned image is the only path.
- `actionlint`, `zizmor`, `ast-grep` are **absent** from PATH.
- Local shard Postgres: `pgvector/pgvector:pg15` container on **port 55441**, database
  `sl_step4`, migrations applied through **124**. Next free migration number is **125**.
  Port 55432 (the experiment Postgres, `kel_*`) was deliberately **not** used.

### 4.2 Verifier versions actually run

| Tool | Pin | Verified output |
|---|---|---|
| actionlint | `rhysd/actionlint:1.7.12` | `1.7.12 / installed by building from source / go1.26.1 linux/amd64` |
| zizmor | `ghcr.io/zizmorcore/zizmor:1.29.0` | `zizmor 1.29.0` |
| ast-grep | npm `@ast-grep/cli@0.45.3` | `ast-grep 0.45.3` |

### 4.3 The ast-grep false-accept, reproduced

Empirical contract for `ast-grep test --skip-snapshot-tests`, measured on a single-rule project:

| Situation | Exit | Summary line | Correct verdict |
|---|---|---|---|
| rule + test agree | `0` | `test result: ok. 1 passed; 0 failed;` | `pass` |
| an `invalid` case does not match | `4` | `Error: test failed. 0 passed; 1 failed;` | `fail` |
| a rule fails to parse (prelude `utils`) | `8` | `Fail to parse yaml as Rule.` | `error` |
| **test file present, rule id not loaded** | **`0`** | **`test result: ok. 0 passed; 0 failed;`** | **`error` (no cases ran)** |

The last row is the trap. ast-grep prints `Configuration not found! <rule-id>`, runs **zero**
cases, prints `0 passed; 0 failed`, and **exits 0**. A runner that gates on the exit code would
record a *verified Procedure* having checked nothing at all. Hence the mandatory
"expected case count ran" assertion. (ast-grep issue #2403 records a related overload: `scan`
returns exit 1 both for "findings" and for "no findings" on the stdin path, so exit 1 is not a
reliable "a rule fired" signal either. Separately, default `scan` severity is `hint`, so an
unmodified `ast-grep scan` exits 0 even with findings.)

### 4.4 The upstream suite does not run whole — and the reason is worse than it looks

`ast-grep test` over all 184 rules at commit `73120109`:

| ast-grep | Result |
|---|---|
| 0.43.0 | exit **8** — ``Utility id `PATTERN_1(identifier)` contains reserved characters`` |
| 0.44.1 | exit **8** — same class of error |
| 0.45.3 | exit **8** — same class of error |

**67 of 184 rules** reference a prelude `utils:` entry of the form `PATTERN_1(identifier)`,
`PATTERN_2(...)` or `PATTERN_3(...)`. ast-grep parses these as *utility ids* and rejects the
parentheses as reserved characters. Because ast-grep loads every rule before running any test,
one such rule aborts the entire suite with no results at all.

**CORRECTION to my own first reading, made after running it:** I initially attributed the
whole-suite failure to a *whole-suite-only* interaction and expected per-rule isolation to
rescue the affected rules. It does not. Run individually, each of those 67 rules fails with
`Error: Cannot parse rule .\rules\<rule>.yml` / `Fail to parse yaml as Rule.` / ``Utility id
`PATTERN_3(field_expression)` contains reserved characters`` (exit 8). **This is a genuine
upstream corpus defect at this commit, not an artifact of how we invoke the tool.** Measured
split: **117 pass, 67 cannot be parsed by the pinned CLI, 0 fail their own test cases.**

So the yield is **117/184 = 63.6%**, not the ~154 I first projected. The 67 become no
Procedures, with reason `check_error` and a detail that names the cause as an upstream
rule-definition defect rather than a check failure.

### 4.5 Two more corpus defects found by running it, not by reading it

**(a) Test files do not follow the `<rule_id>-test.yml` convention for 5 rules.** Joining
tests to rules by filename would have wrongly quarantined 5 usable rules. The declared `id`
is the only safe join key — it is what ast-grep itself matches on, and it is unique across the
package by the corpus's own contract:

| rule id | test filename |
|---|---|
| `missing-nul-cpp-string-memcpy-copy-cpp` | `missing-nul-cpp-string-memcpy-cpp-test.yml` (missing `copy-`) |
| `sizeof-this-cpp` | `size-of-this-test.yml` (different words) |
| `networkcredential-hardcoded-secret-csharp` | `networkcredential-hardcoded-secret-python-test.yml` (**says python**) |
| `jwt-simple-noverify-typescript` | `jwt-simple-noverify-typecript-test.yml` (**typo: typecript**) |
| `detect-angular-sce-disabled-typescript` | `detect-angular-sce-disabled-typescript.yml` (no suffix) |

Recovering these took the yield from 113 to 117. The inverse defect also exists and is
counted: a test whose `id` matches no rule is reported as `test_without_rule` rather than
ignored, because it is the same shape as the zero-case false-accept.

**(b) `N passed` counts rule TEST FILES, not individual snippets.** A rule-test holding
1 valid and 1 invalid case reports `1 passed`. My first liveness assertion compared that
against `len(valid) + len(invalid)` and **correctly rejected a rule that had genuinely
passed** — the assertion catching my own unit error is the strongest evidence yet that it
is worth having. The expectation is now 1 test group per rule, with `snippet_count` kept for
reporting only. A second assertion was added: a pass must *name* the rule id it checked.

### 4.6 ast-grep has no anonymously-pullable container image

`ghcr.io/ast-grep/cli` returns `denied` unauthenticated and `ast-grep/cli` does not exist on
Docker Hub, so ast-grep runs as a **pinned local binary** (npm `@ast-grep/cli@0.45.3`) with a
scrubbed environment and a hard timeout, while actionlint and zizmor get the full container
flag set. That asymmetry is a real reduction in isolation for one of the three tools and is
stated in the module docstring rather than glossed.

## 5. Safety / licensing summary

- ast-grep-essentials: **Apache-2.0** (root LICENSE governs the rule content) → ALLOW on
  `DEFAULT_ALLOWLIST`. `package.json` says ISC; recorded, does not change the verdict.
- actionlint: MIT → ALLOW. zizmor: MIT → ALLOW. ast-grep CLI: MIT → ALLOW.
- No per-rule license headers exist upstream; the single root LICENSE is the correct granularity
  and matches the plan's "license per file" requirement being satisfiable by one root LICENSE
  covering all files at the pinned commit.
- No source in this step contains agent traces, so `redact_event` is not on the ingest path
  here; the rule bodies are linted data. Recorded as an explicit scope limit rather than a
  silent omission.
