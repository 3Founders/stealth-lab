# What to ingest next — source research (2026-09-25)

Scope: recommend high-signal inputs for the procedural memory substrate (Goals / Procedures / Claims / Evidence),
fit to what the pipeline ingests today (pinned GitHub files, web URLs, repo ingester, OpenHands trajectories,
SPDX license gate). Research only; no code changed.

Method: every repo's license/stars/activity below was read from the GitHub API (`gh api repos/...`, 2026-09-25);
file counts come from `git/trees?recursive=1`. HF datasets were checked on the dataset card and via
`datasets-server.huggingface.co/info` (schema + row counts). Web terms/robots.txt were fetched directly.
Anything I could not verify is marked **unverified**.

---

## 0. Headline findings

1. **The best leverage is vendor-official skill repos, not filename search.** About 50 vendors now publish
   official SKILL.md repos, most MIT/Apache. They are indexed in one MIT-licensed list
   (VoltAgent/awesome-agent-skills: ~593 official skill links across 50 vendor repos). Unlike the 300-doc code-search
   benchmark, these are maintained by the product owner, name *when* to use them (frontmatter `description`), and point at a
   real API or CLI, so they can be checked against something.
2. **Picking AGENTS.md/CLAUDE.md by repo quality works much better than picking by filename.** I probed the top 100
   MIT/Apache repos with >10k stars and a push after 2026-06-01: **61/100 have AGENTS.md or CLAUDE.md** at the root
   (88 files). Examples: vercel/next.js 29.5 KB, openai/codex 22 KB, langchain 20 KB, rust-lang/rust 13 KB,
   llama.cpp 12 KB, electron CLAUDE.md 13 KB. Many CLAUDE.md files are 11-byte `@AGENTS.md` stubs, so dedupe by
   size or content hash.
3. **Large, commercially usable, outcome-labelled SWE trajectory datasets exist.** But they use the OpenAI
   chat-messages format (`role/content/tool_calls`), **not** the OpenHands event-history format our adapter parses.
   One "chat-messages trajectory" adapter would open >1M labelled trajectories.
   `nebius/SWE-rebench-openhands-trajectories` (CC-BY-4.0, 67,074 trajectories, 32,161 resolved, several attempts per
   instance) directly yields **success/failure pairs on the same task**. That is our "failure + recovery" input.
4. **Our license gate is a denylist, and it passes restrictive licenses GitHub reports as `NOASSERTION`.** Verified
   cases: `hesreallyhim/awesome-claude-code` (actually CC BY-NC-ND 4.0), `semgrep/semgrep-rules` (Semgrep Rules License
   v1.0), `elastic/detection-rules` (Elastic License 2.0), `getsentry/sentry-docs` (FSL-1.1), `SigmaHQ/sigma` (DRL 1.1),
   `architecture-decision-record/architecture-decision-record` (CC BY-NC-SA). All of them would pass
   `screening.py::_SPDX_DENYLIST`. The list also lacks CC 3.0/2.x NC/ND variants. **Recommendation: switch to an
   allowlist.** Quarantine NOASSERTION/none for manual review.
5. **Most famous engineering blogs are the wrong first target.** The content is all-rights-reserved. Some opt out of
   AI explicitly: Cloudflare's website terms forbid bots from using site content for AI systems unless robots.txt
   permits. netflixtechblog.com (Medium) disallows GPTBot/ClaudeBot. figma.com disallows GPTBot. Use them for
   discovery or as links, not bulk ingestion. **Permissive substitutes with measured results exist:** web.dev case
   studies (CC BY 4.0, before/after metrics), Kubernetes docs and blog (CC BY 4.0), GitHub Docs (CC BY 4.0), and
   Microsoft Learn azure-docs (CC BY 4.0).

---

## 1. Ranked shortlist (verified)

Legend. Path: **S** = pinned GitHub file (SKILL/AGENTS/Markdown) · **R** = repo ingester · **W** = web URL ·
**T** = trajectory path · **✗** = needs new adapter.

| # | Source | Why it carries procedure/outcome signal | License (verified) | Volume | Format | Path |
|---|---|---|---|---|---|---|
| 1 | [anthropics/skills](https://github.com/anthropics/skills) | Reference SKILL.md shape; mcp-builder, webapp-testing, skill-creator | Apache-2.0, **except** `skills/{docx,pdf,pptx,xlsx}` = source-available (exclude) | 20 SKILL.md (16 usable) | MD+scripts | S |
| 2 | [openai/skills](https://github.com/openai/skills) | `.system/.curated/.experimental` tiers give a curation signal | No repo license; **per-skill LICENSE.txt** (38 files; sampled one = Apache-2.0). Repo marked deprecated in favour of [openai/plugins](https://github.com/openai/plugins) (no license, avoid) | 44 | MD | S (check per-skill license) |
| 3 | [microsoft/skills](https://github.com/microsoft/skills) | Azure/Foundry SDK procedures across 6 languages | MIT | 205 (heavy language duplication, so sample) | MD | S |
| 4 | [google/skills](https://github.com/google/skills) | Gemini/Vertex/GCP procedures | Apache-2.0 | 152 | MD | S |
| 5 | [NVIDIA/skills](https://github.com/NVIDIA/skills) | GPU/inference procedures (fills category 22) | Apache-2.0 | 383 | MD | S (sample) |
| 6 | [getsentry/skills](https://github.com/getsentry/skills) | Debug workflow: fix-issues from stack trace, SDK upgrade | Apache-2.0 | 27 | MD | S |
| 7 | [stripe/ai](https://github.com/stripe/ai) · [cloudflare/skills](https://github.com/cloudflare/skills) · [huggingface/skills](https://github.com/huggingface/skills) · [firebase/agent-skills](https://github.com/firebase/agent-skills) · [ClickHouse/agent-skills](https://github.com/ClickHouse/agent-skills) · [expo/skills](https://github.com/expo/skills) · [supabase/agent-skills](https://github.com/supabase/agent-skills) · [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) | Official integration recipes (category 18), with a real API behind each | MIT / Apache-2.0 (each verified) | 60 / 14 / 26 / 13 / 11 / 26 / 2 / 9 | MD | S |
| 8 | [hashicorp/agent-skills](https://github.com/hashicorp/agent-skills) | Terraform provider/module procedures (IaC) | **MPL-2.0** (file-level weak copyleft: fine to read and extract; modifications to the files themselves must stay MPL) | 20 | MD | S |
| 9 | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) · [addyosmani/web-quality-skills](https://github.com/addyosmani/web-quality-skills) | Whole-lifecycle engineering workflows; Core Web Vitals | MIT | 25 + ~6 | MD | S |
| 10 | [obra/superpowers](https://github.com/obra/superpowers) · [mattpocock/skills](https://github.com/mattpocock/skills) · [garrytan/gstack](https://github.com/garrytan/gstack) | Most-installed process skills (TDD, debugging, planning). skills.sh shows install counts, a popularity signal | MIT | 15 / 38 / 66 | MD | S |
| 11 | AGENTS.md/CLAUDE.md in **top-starred permissive repos** (probe list: next.js, openai/codex, langchain, rust-lang/rust, kubernetes, llama.cpp, transformers, electron, deno, supabase, ant-design, browser-use, github/spec-kit ...) | Real repo-native build/test/PR procedures maintained by core teams | Per-repo (MIT/Apache in the probe) | ~60% hit rate. Top 300 repos → ~150–180 real files after stub dedupe | MD | S |
| 12 | [nebius/SWE-rebench-openhands-trajectories](https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories) | Outcome-labelled (`resolved`, `pred_passes_gen_tests`), multi-attempt, so success/failure pairs per instance | CC-BY-4.0 (underlying repos keep their own licenses) | 67,074 traj / 1,823 repos | Parquet, chat messages | **✗** chat-messages adapter |
| 13 | [SWE-bench/SWE-smith-trajectories](https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories) | Claude 3.7 Sonnet + SWE-agent, `resolved` label | MIT | 76k rows (~5k unique) | Parquet, `messages` | **✗** same adapter |
| 14 | [nvidia/Open-SWE-Traces](https://huggingface.co/datasets/nvidia/Open-SWE-Traces) | 5 harnesses (OpenHands, SWE-agent, mini-swe-agent, opencode, claude-code), `resolved` ∈ {1,0,-1} | CC-BY-4.0 | 511,668 rows, 42.6 GB | Parquet | **✗** same adapter |
| 15 | [nebius/SWE-rebench](https://huggingface.co/datasets/nebius/SWE-rebench) | Issue→PR→test triples with `FAIL_TO_PASS`/`PASS_TO_PASS` **and a per-row `license_name`** (license filtering for free) | CC-BY-4.0 | 27,878 rows / 3,400+ repos | Parquet | **✗** structured-row adapter |
| 16 | [prometheus-operator/runbooks](https://github.com/prometheus-operator/runbooks) | One runbook per standard alert: meaning → impact → diagnosis → mitigation | Apache-2.0 | 121 MD | MD | R/S |
| 17 | [PagerDuty/incident-response-docs](https://github.com/PagerDuty/incident-response-docs) ([site](https://response.pagerduty.com/)) | Incident roles, during/after procedures, postmortem templates | Apache-2.0 | 37 MD | MD | S |
| 18 | [gitlab.com/gitlab-com/runbooks](https://gitlab.com/gitlab-com/runbooks) | Production on-call runbooks for a real SaaS | MIT (per repo page; **verify the LICENSE file**) | Large, 26k commits | MD | **✗** GitLab host (GitHub-only fetcher) |
| 19 | [kubernetes/website](https://github.com/kubernetes/website) `content/en/docs/tasks/` | Canonical ops tasks (upgrade, drain, debug) | CC-BY-4.0 | 221 task pages (+773 blog posts) | MD | S/R |
| 20 | [microsoft/code-with-engineering-playbook](https://github.com/microsoft/code-with-engineering-playbook) | Engineering fundamentals: CI/CD, code review, observability, design reviews | CC-BY-4.0 | 249 MD | MD | S |
| 21 | [web.dev case studies](https://web.dev/case-studies) | **Measured before/after results** (e.g. Vodafone: LCP 8.3 s → 5.7 s, +8% sales) | "content … CC BY 4.0, code samples Apache 2.0" (quoted from page footer) | **Unverified count** (JS-rendered listing; probably ~100) | HTML | W (needs a URL list) |
| 22 | [actions/starter-workflows](https://github.com/actions/starter-workflows) | Canonical CI/CD workflows per language/cloud | MIT (API says NOASSERTION; LICENSE file is MIT) | 187 YAML | YAML | R |
| 23 | [rust-lang/rfcs](https://github.com/rust-lang/rfcs) · [kubernetes/enhancements](https://github.com/kubernetes/enhancements) (KEPs) · [python/peps](https://github.com/python/peps) | Design docs with motivation, alternatives and rollout: the **why/when** layer | Apache-2.0/MIT · Apache-2.0 · PEPs are public domain/CC0 per their text (the repo has no LICENSE file) | 657 MD · 692 MD · ~700 | MD/RST | S (RST unverified) |
| 24 | [microsoft/AgentRx](https://huggingface.co/datasets/microsoft/AgentRx) | Failed trajectories with step-level failure category + **root-cause step** (tau-retail, Magentic-One) | CC-BY-4.0 | <1k | JSON | **✗** |
| 25 | [openai/openai-cookbook](https://github.com/openai/openai-cookbook) · [anthropics/claude-cookbooks](https://github.com/anthropics/claude-cookbooks) · [huggingface/cookbook](https://github.com/huggingface/cookbook) | Executable recipes (RAG, evals, tool use, routing, context management) | MIT · MIT · Apache-2.0 | 275 / 101 / 139 `.ipynb` (+141/87/11 MD) | ipynb | **✗** notebook adapter (MD parts fit S now) |

Held back on purpose:
- [trailofbits/skills](https://github.com/trailofbits/skills): 85 high-quality security skills, but **CC-BY-SA-4.0**.
  Share-alike could attach to derived procedures you redistribute. Needs a founder ruling.
- [OWASP/CheatSheetSeries](https://github.com/OWASP/CheatSheetSeries) (CC-BY-SA-4.0) and GitLab `doc/`
  (CC BY-SA 4.0): the same issue.
- [github/awesome-copilot](https://github.com/github/awesome-copilot): MIT, 441 SKILL.md + ~1,800 MD, but community
  quality varies. Second batch, sampled.

Rejected or restricted (verified):
- Google [SRE book/workbook](https://sre.google/sre-book/table-of-contents/): CC BY-NC-ND 4.0.
- `semgrep/semgrep-rules`: Semgrep Rules License.
- `elastic/detection-rules`: Elastic License 2.0.
- SigmaHQ: DRL 1.1, which carries attribution conditions. Possibly usable, but needs review.
- `icco/postmortems`: GPL-3.0.
- [ToolBench](https://github.com/OpenBMB/ToolBench): Apache code, but "research and educational purposes" only, and it
  depends on RapidAPI terms.
- `grafana/grafana`: AGPL.

---

## 2. Curated collections (best leverage)

| Collection | What it gives | License | Use |
|---|---|---|---|
| [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) | ~593 **official** skills across 50 vendor repos (Microsoft 133, OpenAI 42, Sentry 28, Trail of Bits 21, Anthropic 17, HF 13, HashiCorp 11, Datadog 8, Cloudflare 8 …) plus a community section. Links go via `officialskills.sh/<owner>/<repo>/<skill>` | MIT | **Primary seed.** Parse the "official" section only, then map to GitHub repos and license-check each repo (some vendor repos have no license, e.g. `figma/mcp-server-guide`) |
| [skills.sh](https://skills.sh) (+ [vercel-labs/skills](https://github.com/vercel-labs/skills), MIT) | Install-count leaderboard (~1.55M skills tracked); public rate-limited API; the [terms](https://skills.sh/terms) allow "reasonable use, including caching"; skills stay under their source repos' licenses | Directory ToS | Use install counts as a **prior** for which community skills to ingest |
| [PatrickJS/awesome-cursorrules](https://github.com/PatrickJS/awesome-cursorrules) | ~170 `.mdc` rule files by stack | CC0-1.0 | Ingest directly. Rules are prescriptive conventions, not procedures, so they seed claims and applicability, not procedures |
| [sickn33/agentic-awesome-skills](https://github.com/sickn33/agentic-awesome-skills) | Claims 2,445+ skills | MIT | Discovery only; quality unknown |
| [hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) | Well-curated Claude Code resources | **CC BY-NC-ND 4.0** (GitHub reports NOASSERTION) | Do not ingest the list. At most follow links by hand |
| [danluu/post-mortems](https://github.com/danluu/post-mortems) | 100+ postmortem links by failure class (config, hardware, time, DB) | No license file | Links are facts. Follow each link and apply a per-domain web policy |
| [upgundecha/howtheysre](https://github.com/upgundecha/howtheysre) | SRE posts/incidents/tools for 80+ orgs | CC0-1.0 | Discovery index for category 4/19 |
| [dastergon/awesome-sre](https://github.com/dastergon/awesome-sre) | SRE reading list | CC0-1.0 | Discovery |
| [k8s.af](https://k8s.af/) ([codeberg source](https://codeberg.org/hjacobs/kubernetes-failure-stories); GitHub mirror archived) | Kubernetes failure stories | No license verified | Discovery |
| [The VOID](https://www.thevoid.community/database) | ~10k public incident reports from ~600 companies (per Verica's reports) | **Unverified** (page JS-rendered; maintenance status unclear) | Discovery; contact the owners before bulk use |
| [runbear-io/awesome-runbook](https://github.com/runbear-io/awesome-runbook) | Runbook link list | CC0-1.0 | Discovery (small) |
| [agents.md](https://agents.md/) | Claims 60k+ repos; links to GitHub search | — | Ignore the raw search (the same failure as the 300-doc run). Use the stars-first probe (§0.2) |
| HF trajectory family: [nvidia/SWE-Hero-openhands-trajectories](https://huggingface.co/datasets/nvidia/SWE-Hero-openhands-trajectories) (CC-BY-4.0, 34,269, **no resolved field**, but a per-row `license`), [nvidia/SWE-Zero-openhands-trajectories](https://huggingface.co/datasets/nvidia/SWE-Zero-openhands-trajectories), [SWE-Gym/OpenHands-Sampled-Trajectories](https://huggingface.co/datasets/SWE-Gym/OpenHands-Sampled-Trajectories) (6,055, `resolved`+`test_result`), [nebius/SWE-agent-trajectories](https://huggingface.co/datasets/nebius/SWE-agent-trajectories) (CC-BY-4.0, 80,036, 16.7% resolved; card says model outputs are also subject to the **Llama 3.1 license**) | Labelled SWE trajectories | As noted | T after the chat-messages adapter |
| Non-SWE: [xlangai/ubuntu_osworld_verified_trajs](https://huggingface.co/datasets/xlangai/ubuntu_osworld_verified_trajs) (MIT, ~500 GB, screenshots), [Agent-Eval-Refine/Agent-Trajectories](https://huggingface.co/datasets/Agent-Eval-Refine/Agent-Trajectories) (WebArena/Android/iOS, 31 GB, license not shown), [HuggingFaceH4/tau2-bench-data](https://huggingface.co/datasets/HuggingFaceH4/tau2-bench-data) (domain data), [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) (MIT; domain `policy.md` files are procedures; already vendored locally) | GUI/web/customer-service trajectories | As noted | Later; OSWorld needs vision |

---

## 3. First batch (~830 documents, ≈$50 at $0.06/doc)

All of it fits **existing** paths (S/R/W). Trajectories are a separate batch 1b once the adapter lands.

| Slice | Source | Count | Selection rule | Est. $ |
|---|---|---|---|---|
| A1 | Official vendor skills: anthropics (16, excl. 4 source-available), openai (44, per-skill license check), getsentry (27), cloudflare (14), huggingface (26), firebase (13), ClickHouse (11), vercel-labs (9), supabase (2), hashicorp (20) | 182 | All | 11 |
| A2 | Large vendor sets, sampled: microsoft/skills (60 of 205, one per service, prefer Python), google/skills (40 of 152), NVIDIA/skills (40 of 383, inference/GPU first), stripe/ai (20 of 60), expo (10) | 170 | Drop near-duplicates by description | 10 |
| A3 | Process skills: addyosmani/agent-skills (25), obra/superpowers (15), mattpocock/skills (15), garrytan/gstack (15) | 70 | Top by skills.sh installs | 4 |
| B | AGENTS.md / CLAUDE.md from top-300 MIT/Apache/BSD repos (stars >10k, pushed within 90 days) | 150 | File ≥1 KB; one per repo (prefer AGENTS.md); drop `@AGENTS.md` stubs | 9 |
| C | Runbooks and incident process: prometheus-operator/runbooks (121), PagerDuty (37) | 158 | All | 9.5 |
| D | Kubernetes `docs/tasks` (60) + MS engineering playbook (40: CI/CD, observability, code review, design reviews) | 100 | Task/how-to pages only | 6 |
| E | Measured improvements: web.dev case studies | ~?40 | Only pages with numeric before/after | 2.4 |
| | **Total** | **~830–870** | | **≈$50–52** |

Throughput: 2 min/doc ≈ 28 hours serial, so run it in parallel. Add eval hooks: tag each slice so Band-3 sweeps can
check whether retrieval hit-rate or verification rate differs by source (official vs. repo-native vs. runbook).
That turns the batch itself into a measurement.

**Batch 1b (after adapter, ≈200 trajectories).** From `nebius/SWE-rebench-openhands-trajectories`: 100 instances
that have ≥1 `resolved=1` **and** ≥1 `resolved=0` attempt, filtered to repos whose license is permissive.
Join to `nebius/SWE-rebench.license_name` on `instance_id`. Ingest both sides as contrastive pairs: the failure
episode, the successful recovery, and a machine-verifiable outcome (`FAIL_TO_PASS`). Trajectories are long
(avg 64 turns), so expect a higher cost per trajectory than per document. Cap the tokens per trajectory.

---

## 4. Gaps: sources we cannot ingest yet

| Gap | Unlocks | Work |
|---|---|---|
| **Chat-messages trajectory adapter** (`messages`/`trajectory` = list of `{role, content, tool_calls[{function{name,arguments}}], tool_call_id}`, plus a `resolved` field) | nebius SWE-rebench-openhands, SWE-smith, Open-SWE-Traces, SWE-Gym, SWE-Hero (>1M trajectories) | Map to `NormalizedTrajectory`: pair each assistant tool_call with the tool message that has the matching `tool_call_id`. `resolved` becomes outcome evidence. Same quarantine semantics as `openhands.py`. Plus a parquet/HF streaming reader with row selection |
| **SWE-agent `.traj` / nebius `trajectory` format** | nebius/SWE-agent-trajectories (80k) | Small adapter, or convert to chat messages |
| **Structured issue→PR→test rows** | nebius/SWE-rebench, SWE-bench(_Verified), SWE-Gym | Row → Goal (problem_statement), Procedure candidate (patch diff summarised), Evidence (FAIL_TO_PASS after test_patch). The machine-verifiable outcome comes free. Better than the GitHub issue/PR API (no rate limits; license field included) |
| **Live GitHub issue→PR→CI histories** | Repos outside the datasets | GitHub GraphQL: issue → `closedByPullRequestsReferences` → PR commits → `checkSuites`/`checkRuns` conclusion. Needs an adapter and a PII policy (usernames, emails in commits) |
| **Notebook adapter (.ipynb)** | ~515 cookbook notebooks (OpenAI/Anthropic/HF) | nbformat → Markdown. Keep code cells. Drop outputs except small text outputs, which serve as expected-result evidence. Strip images/base64 |
| **Non-GitHub git hosts** | gitlab-com/runbooks, codeberg k8s-failure-stories | GitLab/Codeberg raw fetch pinned to a commit SHA, plus a license lookup (GitLab has no Licensee API: parse LICENSE yourself) |
| **Link-list crawler with per-domain policy** | danluu/post-mortems, howtheysre, awesome-sre, VOID | Parse list → URLs → check robots.txt (incl. GPTBot/ClaudeBot/`Content-Signal`), a domain allow/deny table and dedupe → fetch. The policy table is the hard part (see §5) |
| **Sitemap/JS-rendered listings** | web.dev case studies, many vendor docs | Sitemap reader or headless render just for enumeration |
| **RST / docs-as-code** | PEPs (RST), Sphinx docs | RST→MD (docutils/pandoc) |
| **YAML-heavy design docs** | KEPs (`kep.yaml` carries stage/milestones/status) | Treat kep.yaml as structured metadata on the README.md procedure; stage transitions work as validity windows |
| **Vision / OCR** | OSWorld trajectories (screenshots), scanned PDFs, architecture diagrams | Out of scope until the OCR/vision path exists |
| **tau2 simulation JSON** | tau2-bench runs (`data/simulations/`), AgentRx | Adapter from simulation messages + reward to trajectory + evidence. The local vendor copy lets us *generate* runs we own, with no license question |
| **Gate fix (small, high priority)** | Everything | SPDX **allowlist** (MIT, Apache-2.0, BSD-2/3, ISC, 0BSD, Unlicense, CC0-1.0, CC-BY-4.0, MPL-2.0 flagged, PSF, Zlib). NOASSERTION/none go to QUARANTINE plus a LICENSE-text sniff for "NonCommercial", "NoDerivatives", "Elastic License", "Functional Source License", "Business Source", "Semgrep Rules License", "Commons Clause". Add CC 3.0/2.5 NC/ND ids and CC-BY-SA as its own "flag". Honour per-subdirectory licenses (anthropics/skills document skills, openai/skills per-skill LICENSE.txt, gitlabhq `doc/` vs `ee/`) |

---

## 5. Licensing / legal cautions (not legal advice; get counsel before anything public)

1. **A repo license is not a content license for everything in it.** Verified mixed cases:
   - anthropics/skills: document skills are source-available.
   - openai/skills: licensed per skill.
   - gitlabhq: `doc/` CC BY-SA, `ee/` proprietary.
   - architecture-decision-record: the author's text is CC BY-NC-SA; templates are under their authors' terms.

   The gate should resolve licenses at the file/directory level.
2. **GitHub's "NOASSERTION" hides restrictive licenses** (§0.4). Denylist + unknown→allow is the wrong default for a
   commercial product.
3. **Attribution obligations are real under CC-BY-4.0, MIT and Apache.** Store the attribution string, licence id
   and commit SHA in provenance (we already pin SHAs), and show them wherever a derived procedure is displayed or
   exported. Apache-2.0 NOTICE files must travel with redistributed material.
4. **Share-alike (CC-BY-SA: Trail of Bits, OWASP, GitLab docs):** if derived procedures are "adaptations" and you
   redistribute them, SA can require your derived content to be CC-BY-SA. Keep SA sources in a separate tenant/scope,
   or exclude them, until there is a ruling.
5. **Dataset license ≠ underlying-content license.** SWE-rebench, SWE-Hero and nebius datasets say to respect each
   source repository's license. Trajectories embed repo code. Filter on the per-row license fields they provide.
   Model-output terms can also apply: the nebius SWE-agent card cites the Llama 3.1 license. ToolBench is
   research-only.
6. **Website terms and robots.txt:**
   - Cloudflare's [website terms](https://www.cloudflare.com/website-terms/) prohibit bots from using content for
     AI systems. Yet blog.cloudflare.com's robots.txt publishes `Content-Signal: ai-train=yes, search=yes,
     ai-input=yes`. That is a conflict, so get counsel before relying on either.
   - netflixtechblog.com and medium.com disallow ClaudeBot/GPTBot. figma.com disallows GPTBot.
   - Record the robots.txt snapshot and any `Content-Signal` in provenance at fetch time.
7. **EU:** the Art. 4 DSM TDM exception yields to machine-readable opt-outs. The Hamburg Higher Regional Court
   (Dec 2025, LAION) held that natural-language ToS opt-outs are insufficient and machine-readable ones (robots.txt,
   TDMRep) count ([Kluwer](https://legalblogs.wolterskluwer.com/copyright-blog/laion-round-2-machine-readable-but-still-not-actionable-the-lack-of-progress-on-tdm-opt-outs-part-1/)).
   Honour robots.txt and TDMRep.
8. **US:** *Thomson Reuters v. Ross* found no fair use for training a **non-generative** legal-research tool on
   Westlaw headnotes (D. Del., Feb 2025). The Third Circuit heard argument in June 2026 and has not ruled
   ([LawSites](https://www.lawnext.com/2026/06/at-3rd-circuit-judges-press-ross-and-thomson-reuters-on-fair-use-ai-training-and-market-harm.html),
   [Baker Botts](https://www.bakerbotts.com/thought-leadership/publications/2026/july/third-circuit-hears-oral-argument)).
   A retrieval substrate that extracts and serves procedures from others' text looks more like Ross than like
   generative-model training. For all-rights-reserved sources: store extracted claims/procedures with short quotes
   plus a link and hash, not full text. Never re-serve the full text.
9. **GitHub ToS:** the D.9 "Access Reciprocity" clause (effective 2026-04-27) applies only to entities with ≥700M MAU,
   so it does not bind StealthLab. The Acceptable Use Policy forbids spam and selling personal information scraped
   from GitHub.
10. **Security, not only legal:** third-party SKILL.md and AGENTS.md files are prompt-injection surfaces
    (VoltAgent's README carries a security notice to the same effect). Ingested procedures should stay `candidate`,
    and a skill's scripts should never auto-execute during verification without a sandbox.
11. **Personal data:** trajectories and issue histories contain usernames, emails, sometimes secrets. They route
    through `trace_redaction.redact_event` (already the rule) before INSERT.

---

## Sources (primary)
GitHub API reads for every repo above (2026-09-25) · https://skills.sh · https://skills.sh/terms ·
https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories · https://huggingface.co/datasets/nebius/SWE-rebench ·
https://huggingface.co/datasets/nebius/SWE-agent-trajectories · https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories ·
https://huggingface.co/datasets/nvidia/Open-SWE-Traces · https://huggingface.co/datasets/nvidia/SWE-Hero-openhands-trajectories ·
https://huggingface.co/datasets/SWE-Gym/OpenHands-Sampled-Trajectories · https://huggingface.co/datasets/microsoft/AgentRx ·
https://huggingface.co/datasets/xlangai/ubuntu_osworld_verified_trajs · https://huggingface.co/datasets/Agent-Eval-Refine/Agent-Trajectories ·
https://github.com/SWE-bench/experiments · https://github.com/OpenBMB/ToolBench · https://sre.google/sre-book/table-of-contents/ ·
https://web.dev/case-studies/vodafone · https://www.cloudflare.com/website-terms/ · https://docs.github.com/en/site-policy/github-terms/github-terms-of-service ·
https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies · https://agents.md/ ·
https://aws.amazon.com/premiumsupport/technology/pes/ (16–18 AWS post-event summaries; AWS site terms apply) · https://www.thevoid.community/database
