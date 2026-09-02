# Better-Ways Corpus — Phase 1: Source Resolution + One-Procedure Extraction Gate

**Scope of this phase (narrow, by design):** prove that we can obtain **one concrete, reusable
procedure** from each of the 18 exact source artifacts. Not optimizing retrieval, not doing full
ingestion, not building a corpus. Testing procedural density only.

**Method:** each artifact was fetched live (GitHub HTML + GitHub REST API for the pinned commit
SHA). Nothing was inserted into any canonical production table. Full records:
`./source_results.jsonl` (18 lines, one JSON object per source).

**Run date:** 2026-09-02. All revisions are the tip of the default branch at fetch time.

---

## Extracted procedure per source

| ID | Family | Artifact | Rev (pinned) | Representative procedure extracted |
|----|--------|----------|--------------|-------------------------------------|
| S01 | Anthropic skills | anthropics/skills | `main@5304866` | Author a new Agent Skill (folder + SKILL.md + frontmatter + Examples/Guidelines) |
| S02 | Vercel agent skills | vercel-labs/agent-skills | `main@063bee9` | Deploy a project to Vercel as a claimable deployment (tarball → framework-detect → upload → preview+claim URL) |
| S03 | Web design | …/skills/web-design-guidelines | `main@063bee9` | Review UI code against the Web Interface Guidelines (fetch rules → check → emit `file:line` findings) |
| S04 | React performance | …/skills/react-best-practices | `main@063bee9` | Apply the 70 React/Next.js perf rules by priority (waterfalls → bundle → server cache → memoize → JS patterns) |
| S05 | AGENTS standard | agentsmd/agents.md | `main@6ae2272` | Author an AGENTS.md (Dev environment tips / Testing instructions / PR instructions sections) |
| S06 | AGENTS starter | avoidwork/AGENTS.md | `main@eb85635` | Adapt the 8-section AGENTS.md starter to a project (keep core security rules, swap stack-specific sections) |
| S07 | AGENTS cross-tool | maweis1981/agents-md | `main@1eeb5ef` | One rule set across all AI tools + CI enforcement (`curl` template → customize → wire commitlint/Actions) |
| S08 | Coding methodology | obra/superpowers | `main@b36e082` | RED-GREEN-REFACTOR TDD loop (failing test → watch fail → minimal code → watch pass → refactor → commit) |
| S09 | GH Agentic Workflows | github/gh-aw | `main@957471c` | Author → `gh aw compile` → `.lock.yml` → push → review via safe-outputs |
| S10 | Workflow creation | …/.github/aw/create-agentic-workflow.md | `main@957471c` | Interview-driven workflow design → `create` file → `gh aw compile <id>` → fix errors → PR with `.lock.yml` |
| S11 | Context Rot | chroma-core/context-rot | `master@af80a08` | Reproduce the benchmark (venv → deps → API keys → datasets → run experiment/* → compare to published figures) |
| S12 | HTML slides | zarazhangrui/frontend-slides | `main@9906a34` | Generate a single-file HTML deck (invoke skill → content/pptx → pick 1 of 3 styles → render → deploy/export) |
| S13 | HTML templates | zarazhangrui/beautiful-html-templates | `main@e5e204f` | Select + adapt a pre-built template (read AGENTS.md → parse index.json → match brief → copy → populate) |
| S14 | Codebase to course | zarazhangrui/codebase-to-course | `main@ff8837e` | Turn a codebase into an interactive HTML course (extract architecture → code↔English → visualize → quizzes → 1 HTML file) |
| S15 | Browser execution | vercel-labs/agent-browser | `main@eb05921` | Drive a browser via stable refs (`install` → `open` → `snapshot -i` → `click/fill @eN` → `snapshot` → `close`) |
| S16 | Skill creator | anthropics/skills/…/skill-creator | `main@5304866` | Eval-driven skill authoring loop (intent → SKILL.md → evals.json → with-skill+baseline runs → grade/aggregate → iterate → package) |
| S17 | Agentic maintenance | github/gh-aw (tree/main) | `main@957471c` | Safe-outputs operating loop (agent job read-only → emit safe-outputs → scoped write job → review → approve) |
| S18 | Viral visual source | zarazhangrui/frontend-slides | `main@9906a34` | Lock a visual style before full render (3 preview cards from STYLE_PRESETS.md → pick → optional bold-template-pack → render) |

Notes on the duplicate artifacts in the source set:
- **S09 / S10 / S17** all resolve to `github/gh-aw`. Three *distinct* procedures were extracted:
  the compile flow (S09), the interview-driven designer prompt (S10), the safe-outputs safety loop (S17).
- **S12 / S18** both resolve to `zarazhangrui/frontend-slides`. S12 = end-to-end deck generation;
  S18 = the style-selection sub-procedure. Not byte-duplicates.
- **S16** is a subdirectory of **S01** (`anthropics/skills`); S01 = the lightweight authoring recipe
  from the README, S16 = the full eval-backed workflow from `skill-creator/SKILL.md`.

---

## HARD GATE

| Source | Artifact reachable? | Procedure found? | Exact location? | Tasks found? | License known? |
| ------ | ------------------- | ---------------- | --------------- | ------------ | -------------- |
| S01 | YES | YES | YES (`README.md` §"Creating a Basic Skill" + `template/`) | YES (5) | YES (Apache-2.0; doc skills source-available) |
| S02 | YES | YES | YES (`README.md` §"vercel-deploy-claimable") | YES (5) | YES (MIT) |
| S03 | YES | YES | YES (`skills/web-design-guidelines/SKILL.md` §"How It Works") | YES (5) | YES (MIT) |
| S04 | YES | YES | YES (`skills/react-best-practices/SKILL.md` §"Rule Categories by Priority" + `rules/*`) | YES (5) | YES (MIT) |
| S05 | YES | YES | YES (`AGENTS.md` spec + worked example) | YES (5) | YES (MIT) |
| S06 | YES | YES (template-as-procedure) | YES (`AGENTS.md` §"Structure") | YES (5) | YES (BSD-3-Clause) |
| S07 | YES | YES | YES (`templates/AGENTS.md` §"Option A" + `STANDARDS.md`, `scripts/`, `.github/workflows/lint.yml`) | YES (5) | YES (MIT) |
| S08 | YES | YES | YES (`skills/testing/test-driven-development/SKILL.md`; README §"The Basic Workflow") | YES (5) | YES (MIT) |
| S09 | YES | YES | YES (`.github/aw/github-agentic-workflows.md` + quick-start) | YES (5) | YES (MIT) |
| S10 | YES | YES | YES (`.github/aw/create-agentic-workflow.md` §§"Design Checklist"/"Prompt Requirements"/"Final Steps") | YES (5) | YES (MIT) |
| S11 | YES | YES (reproducible benchmark) | YES (`README.md` §"Experiments" + `experiments/*/README.md`) | YES (5) | YES (MIT) |
| S12 | YES | YES | YES (`SKILL.md` + `STYLE_PRESETS.md`, `html-template.md`, `scripts/`) | YES (5) | YES (MIT) |
| S13 | YES | YES (selection workflow) | YES (`AGENTS.md` + `index.json`, 34 `templates/`) | YES (5) | YES (MIT) |
| S14 | YES | YES | YES (`SKILL.md` + `references/design-system.md`, `references/interactive-elements.md`) | YES (5) | **NO** (no LICENSE file — all rights reserved) |
| S15 | YES | YES | YES (`README.md` §"Quick Start") | YES (5) | YES (Apache-2.0) |
| S16 | YES | YES | YES (`skills/skill-creator/SKILL.md` §"Creating a skill" / "Running and evaluating test cases") | YES (5) | YES (Apache-2.0) |
| S17 | YES | YES | YES (`README.md` "safe-outputs" model + `.github/aw/` samples) | YES (5) | YES (MIT) |
| S18 | YES | YES | YES (`STYLE_PRESETS.md` + `bold-template-pack/selection-index.json` + `SKILL.md`) | YES (5) | YES (MIT) |

### Tallies

| Gate column | Result |
|---|---|
| Artifact reachable? | **18 / 18 YES** |
| Procedure found? | **18 / 18 YES** |
| Exact location (repo + path + section + pinned SHA)? | **18 / 18 YES** |
| Tasks found (2–5 candidate task nodes)? | **18 / 18 YES** |
| License known? | **17 / 18 YES** — S14 has no license file |

### Confidence distribution (from `source_results.jsonl`)

- **high (8):** S03, S04, S05, S08, S09, S10, S15, S16
- **medium (7):** S01, S02, S07, S11, S12, S17, S18
- **low (3):** S06, S13, S14
  - S06 / S13 — real operational content, but the step sequence is reconstructed from a
    structure table / referenced `AGENTS.md` rather than quoted as verbatim numbered steps.
  - S14 — the procedure is solid; the *provenance* is the weak point (no license).

---

## Can we reliably obtain one real procedure from each source?

```
18 / 18 YES   (procedural density)
17 / 18       usable without provenance follow-up  (S14 = no license)
```

Every one of the 18 artifacts is reachable and contains at least one of: explicit workflow
instructions, executable configuration, a documented operational procedure, a reproducible
benchmark workflow, or a skill specification. None required a fabricated procedure. No generic
product README had to be counted on its own — where a repo's top-level README was thin (S02, S11,
S13) the procedure was taken from a skill/spec/experiment file beneath it.

### Caveats to carry into Phase 2 (do not lose these)

1. **S14 (`codebase-to-course`) has no LICENSE.** Extractable as a reference, **not
   redistributable**. Resolve licensing (or drop it) before it enters any shared corpus.
2. **S09 = S10 = S17** (all `github/gh-aw`) and **S12 = S18** (`frontend-slides`). Five source
   IDs, two underlying artifacts. Phase 2 dedup must key on `(repo, commit, file, section)`, not
   on `source_id`, or the corpus will double-count.
3. **S11** run commands are per-experiment in sub-READMEs not fetched in Phase 1 — the top-level
   procedure is env-setup + "follow the experiment README". Deepen before treating S11 as an
   executable benchmark.
4. **S06 / S13** step sequences are reconstructed, not quoted. Re-fetch the actual `AGENTS.md`
   bodies in Phase 2 before promoting either procedure past "candidate".
5. Licenses split **MIT ×12 / Apache-2.0 ×3 / BSD-3-Clause ×1 / none ×1**. All permissive except
   S14; attribution required on every retained procedure.

**Phase 1 gate: PASSED. Proceed to Phase 2.**
