# Source-Ingestion Literature Review: CI Workflow Histories & Bot Dependency PRs

**Scope.** Can public CI artifacts be mined into *evidence-backed* reusable Procedures? Two candidate
sources: (A) the GHA workflow-history corpus, (B) Dependabot/Renovate dependency-bump PRs with green CI.
Prepared 2026-09-28. Every claim below carries a resolvable identifier. Anything I could not resolve is
marked `[UNVERIFIED]` and named as such.

**Label key:** `[PEER-REVIEWED]` · `[PREPRINT]` · `[TECH REPORT]` · `[DATASET CARD]` · `[PRODUCTION WRITEOUT]`

---

## 1. Executive summary

1. **The workflow-history corpus contains no run outcomes — not success/failure, not run IDs, not job
   results.** Its 17 metadata columns are all commit- and file-level. This is stated by the dataset's own
   most-cited successor: GHALogs' related work says Cardoen et al. "track changes in the workflow files
   themselves… However, it does not provide performance or outcome data of the workflows' executions"
   `[S5, §II]`. The MSR'24 paper's *own* limitations section does **not** list this — it is a scope
   property, not an admitted defect `[S1, §5]`.
2. **Its license today is CC-BY-4.0 and it is far bigger than the paper advertises.** Current version
   `10.5281/zenodo.20340547` (2026-05-22): 4.1M+ workflow file versions across 52.9K+ repositories, ~3.7 GB
   across 5 files `[S2]`. The paper's "160K+ / 32K+ / 1.5M+" figures describe the 2023-10 extraction with
   gigawork 1.2.0 `[S1, §3, Table 1]`. **Cite the version, not the paper, for any number.**
3. **GHALogs is CC-BY-SA-4.0 (`10.5281/zenodo.10154920`) — your license-based exclusion is correct and
   citable.** It is also the *only* widely-available source of GHA run outcomes + full logs at scale
   (513K runs / 2.3M steps / 142 GB), scraped Oct 2023 `[S5, §IV]`. Its content is otherwise
   irreplaceable in your design, which is why the exclusion needs an honest cost statement, not just a
   rule.
4. **Green CI is not sufficient evidence that a procedure is correct, and the contrary evidence is
   strong and multi-sourced.** Four independent findings: 42.1% of GHA workflows build without any test
   step `[S9]`; ~11% of *successful* CI jobs are rerun, indicating silent failure — exit code 0 while the
   job did not do its work `[S11]`; official test suites accept 589 of 20,375 AtCoder submissions that are
   in fact buggy `[S14]`; tests cover only 58% of direct and ~20% of transitive dependency calls and
   detect only 47%/35% of injected faults `[S13]`.
5. **Two artifacts make "green" not even mean "the check ran".** A green PR status can be a required check
   that never executed `[S15]`; and developers demonstrably merge with red CI and with
   "LGTM, fedora failure is unrelated" `[S16]`. Your evidence predicate must be a *specific, named,
   observed check-run conclusion on the merge commit*, not the PR's aggregate status.

**The 5 decisions that matter:**

| # | Decision | Position | Anchor |
|---|---|---|---|
| D1 | Can a passing CI run be the *sole* verification predicate? | **No.** Green is a necessary, weak signal. It is a *filter*, not a *proof*. | `[S9][S11][S13][S14]` |
| D2 | Can Source A supply verification evidence at all? | **No.** Zero outcome data. Source A can only supply *candidate* procedures and *preconditions*; it cannot be the evidence layer. | `[S1][S5]` |
| D3 | Is the GHALogs exclusion permanent? | It is **structurally necessary** (SA viral clause) but you lose the entire outcome+log ground truth. Compensate by collecting prospectively. | `[S5]`, §5 below |
| D4 | Can you assemble a *historical* green-CI evidence chain from the API? | **No — and this is the hardest constraint.** Run retention is bounded and configurable. | `[S5, §IV-B]`, `[S16]` |
| D5 | What is the license of extracted "knowledge"? | **Genuinely unresolved.** Repo license covers the file verbatim; the legal status of a *method* extracted from it is jurisdiction-dependent. Do not guess — get counsel. | §3.3 below |

---

## 2. Source A — the workflow-history corpus

### 2.1 What a "workflow history" actually is

**This is the single most common misreading of this dataset and it will corrupt any schema you build.**

It is **not** a run. It is **not** a job. It is **not** even a file revision in isolation. A *workflow
history* is the **commit-level version lineage of one `.github/workflows/*.y[a]ml` file within one
repository**, tracked on the default branch under the first-parent rule `[S1, §2, §5]`.

The row granularity reconciles exactly as follows `[S1, §3, Table 1]`:

| Quantity | Value | Meaning |
|---|---|---|
| Repositories | 32,886 | of 46,281 SEART candidates (71.8% had a `.github/workflows` dir) |
| Repos with ≥1 workflow file | 32,882 | |
| Commits touching workflow files | 1,004,202 | |
| **Workflow histories** | **160,443** | file lineages (≈9.5 versions each) |
| **Workflow files** | **1,526,475** | file-version *occurrences* (one per commit×file event) |
| — modifications | 1,342,662 | |
| — additions | 147,978 | |
| — deletions | 35,835 | |
| Auxiliary files | 75,234 | non-`.y[a]ml` files under the directory |
| Earliest / latest commit | 2019-07-11 / 2023-10-12 | |

`1,342,662 + 147,978 + 35,835 = 1,526,475` — i.e. **one CSV row per (commit, file) event.** The 1.6M
"files" the tool extracted split into 1,526,475 workflow + 75,234 auxiliary.

**"Workflow file" is a content-addressed row, not a file identity.** `file_hash` is a SHA-256 of content;
identical content is stored once. The paper reports this removed **156,933 duplicate files = 10.36% of the
dataset** `[S1, §3]`. Consequences for you:

- One stored content blob is referenced by many `(repository, uid)` lineages. Your "procedure instance"
  count will be massively inflated if you count rows instead of distinct `uid`s.
- Fork/template boilerplate is *already collapsed* in storage but **not** in lineage — the same content
  has N distinct `uid`s. You must dedup on content hash *and* decide whether N forks of the same
  workflow is 1 procedure or N weak corroborations. **The literature does not establish which.**
- Silent data-loss risk: the 10.36% dedup means a `D` (delete) event's `previous_file_hash` can point to
  content first authored by a *different repository*.

### 2.2 Row schema as documented — and the drift you must handle

**Current authoritative schema (Zenodo 20340547 card)** `[S2]`, 17 columns, shared by
`workflows.csv.gz` and `workflows_auxiliaries.csv.gz`:

```
repository, commit_hash, author_name, author_email, committer_name, committer_email,
committed_date, authored_date, file_path, previous_file_path,
file_hash, previous_file_hash, git_change_type,
valid_yaml, probably_workflow, valid_workflow, uid
```

Field semantics that matter:

- `git_change_type` — `A`/`D`/`M`/`R` (Added/Deleted/Modified/**Renamed**), passed through from GitPython
  "provided as is" `[S2]`.
- `valid_yaml` — parses as YAML.
- `probably_workflow` — contains YAML keys `on` **and** `jobs`. **Explicitly may be true while the file is
  invalid YAML** `[S2]`.
- `valid_workflow` — conforms to a **freely available JSON Schema not written or maintained by the dataset
  authors** (originally `json.schemastore.org/github-workflow.json`) `[S3]`. Treat as a weak hint, not a
  correctness guarantee.
- `uid` — assigned at file creation, survives modification *and rename*, dies at deletion `[S2]`.
  This is the correct join key for lineage.

**Schema drift between the paper and the live card — five concrete hazards:**

| Hazard | Paper (MSR'24) | Live card (2026-05-22) |
|---|---|---|
| Validation columns | **Absent from Table 3** | `valid_yaml`, `probably_workflow`, `valid_workflow` present |
| Lineage key | `file_path` + `previous_file_path` + `change_type` | `uid` added |
| Rename handling | "If a file is renamed, it will appear as **Modified**" | Explicit **`R`** value |
| Column name | `change_type` | `git_change_type` |
| Aux file CSV | `auxiliaries.csv.gz` | `workflows_auxiliaries.csv.gz` |

Also: `gigawork` version moved **1.2.0** (paper) → **1.4.2** (all live versions) `[S1, S2]`.

**A `valid_yaml` trap you will hit.** GitHub Actions uses a *subset of YAML 1.2*; PyYAML implements
YAML 1.1, where the bare key `on:` resolves to the **boolean `True`**. Any PyYAML-based validator or
parser will silently mangle every GHA workflow's trigger block. This is a long-standing, pinned,
acknowledged PyYAML issue (pinned as #486; concretely reproducible in #855) `[S18]`. If your extractor uses
`yaml.safe_load` you will produce a corpus that looks plausible and is wrong.

### 2.3 Version lineage and the citation hazards

Concept DOI `10.5281/zenodo.10259013`. **The live record is a versioned concept; 10259013 resolves to the
latest, which is not the version most people cite.** Observed lineage:

| Version DOI | Date | Reported size | Note |
|---|---|---|---|
| `10.5281/zenodo.12698016` | 2024-07-09 | 1.3M-file-era | "fix sometimes invalid `valid_yaml` flag" |
| `10.5281/zenodo.13985548` | 2024-10-25 | 2.3M workflows / 43.3K repos | used by GHA-Repair `[S7]` |
| `10.5281/zenodo.15221545` | 2025-04-15 | 2.3M (unchanged window) | "fix missing metadata" |
| `10.5281/zenodo.17301952` | 2025-10-09 | 3M+ workflows / 49.2K repos | used by `[S6]` |
| **`10.5281/zenodo.20340547`** | **2026-05-22** | **4.1M+ workflows / 52.9K+ repos** | **current** |

Three hazards:

1. **A published DOI is wrong.** The MSR 2024 conference *program page* cites
   `zenodo.org/doi/10.5281/zenodo.**10259014**` — a transposed digit, off by one from the correct
   `10259013` `[S4]`. If you seed from the program page you will 404 or hit the wrong record. The ACM
   abstract and the paper PDF both carry the correct `10259013` `[S1]`.
2. **"Double compression" is a known live bug.** Zenodo is re-gzipping already-gzipped files, so downloads
   land as `x.gz.gz`; the published MD5 refers to the *original* file `[S2]`. **Checksum verification will
   fail unless you account for this.** Undocumented in the paper; only on the live card.
3. **Differing observation windows make sizes non-comparable.** Each version re-runs SEART against a
   different activity cutoff, so "4.1M workflows" is not a superset of the 2024 extraction — repo lists
   are regenerated `[S2]`.

### 2.4 The authors' stated limitations, verbatim in substance `[S1, §5]`

Four, and only four:

1. Git-based extraction is "subject to the intrinsic limitations of mining git histories."
2. SEART scope limits to ≥300 commits, ≥100 stars — "may have excluded smaller or less active projects
   relying on GitHub Actions."
3. Pre-clone check for `.github/workflows` "implies that we potentially excluded repositories that were
   making use of GitHub Actions workflows but stopped using it and deleted this directory." → **survivorship
   bias against de-adoption**, i.e. it systematically drops the negative examples you most want.
4. Default branch + first-parent only; parallel-branch commits not considered (their changes still appear
   once merged).

**Not listed, and it is the most important gap for you: absence of run/outcome data.** The paper's
exploratory analysis explicitly self-limits: "We could not verify this assumption since our dataset does
not provide any information about Travis usage" `[S1, §4, fn.7]`. The authors demonstrate the limitation
*in practice* while not elevating it to §5. The clean citable statement of the gap is third-party, from
GHALogs `[S5, §II]`.

### 2.5 Known quality problems — what I found and what I could not

**Confirmed problems (cited):**

- **~23% of file snapshots are unparseable YAML.** In the 2025-10-09 version, **776,339 of 3,418,911**
  snapshots are flagged invalid YAML. Because dropping a mid-history bad version silently aggregates an
  `A→B→C` change into `A→C`, the authors had to drop **30,922 entire workflow histories** (267,955 →
  236,775) `[S6, §3.2]`. For a knowledge substrate this is the dominant single quality fact: **your usable
  corpus is ~88% of nominal histories, and the loss is non-random** (correlated with broken configs).
- **The `valid_yaml` flag itself was wrong in early versions** — the 2024-07-09 changelog says "fix
  sometimes invalid `valid_yaml` flag" `[S2]`. Any pre-2024-07-09 snapshot's validity flags are untrusted.
- **Content-hash collapse across repos** (10.36%) with `previous_file_hash` cross-repo aliasing (§2.1).
- **Change classification is git-level, not semantic.** `A`/`D`/`M`/`R` are GitPython's letters, passed
  through unmodified `[S2]`. A "modification" that only reorders YAML keys is indistinguishable from a
  semantic change. The only reason a body-level change exists is `gawd` `[S1, §3]` and the diff metadata
  in `[S6]`.
- **No language/domain/action taxonomy on the workflow rows.** `repositories.csv.gz` carries SEART
  metadata (stars, contributors, main language) but the *action* dimension (`actions/checkout@v4`) is only
  present as free text inside file content. The domain/action breakdown you asked about does **not exist as
  a field**; you must parse it. GHALogs by contrast spans **20 programming languages** and 25K+ repos
  `[S5]` — Source A has **no language stratification of its own** beyond the repo-level `main language`.

**Negative result — reported honestly:** I searched for 2025–2026 papers or issue-tracker entries
specifically reporting broken rows, corrupt archives, or format changes in this dataset. **I found none.**
What I did find is (a) the authors' own in-paper admission that ~23% of snapshots fail to parse `[S6]`,
(b) the `valid_yaml` flag fix and double-compression bug on the live Zenodo card `[S2]`, and (c) two
independent 2025–2026 works that *successfully* reused it (GHA-Repair `[S7]`, and `[S6]` itself) — which
is weak positive evidence of fitness, not of quality.

**Open, and I will not guess:** there is no published measurement of precision/recall for
`valid_workflow`, and no published inter-rater agreement for the JSON-Schema conformance check. **The
literature does not establish these.**

### 2.6 GHALogs — content, difference, and the exclusion case

**Contents** `[S5, §III–IV]`: 116K GHA workflows / **513K workflow runs** / **~2.3M steps** across **25K+
public repos in 20 programming languages**, with **complete run logs** (142.3 GB) plus
`repositories.json.gz` (69 MB) and `runs.json.gz` (1.1 GB).

**How it differs from Source A — the distinction is clean and total:**

| | Source A (IEEE/MSR'24) | GHALogs (MSR'25) |
|---|---|---|
| Unit | file-version / lineage | **run** (with jobs & steps) |
| Time axis | full history since 2019 | **the 5 most recent runs per workflow**, scraped Oct 2023 |
| Has outcomes? | **No** | **Yes** — `conclusion` ∈ {success, failure, timed_out} |
| Has logs? | No | Yes, complete |
| Selection | SEART: ≥300 commits, ≥100 stars, non-fork, active | GitHub Search: >100 stars, then **≥30 runs in last 90 days** |
| Scale | 4.1M versions / 52.9K repos | 513K runs / 25K repos / 142 GB |
| License | **CC-BY-4.0** | **CC-BY-SA-4.0** |

GHALogs' own framing is the cleanest available statement of the trade `[S5, §IV]`: "collecting workflow
definition files is not enough, as it is commonly done by the other studies of the GHA ecosystem… because
they do not contain any runtime information."

**Citable justification for exclusion.** The Zenodo record for `10.5281/zenodo.10154920` carries
`license.id = cc-by-sa-4.0` `[S6b]`. CC BY-SA 4.0 §3(b) requires that **Modified Material be licensed under
the same license**, and §3(a) requires sharing adaptations under those terms. A corpus you derive from
GHALogs — including derived *knowledge* you then ship inside a product — is Adapted Material. That is
viral: it contaminates every downstream artifact and forecloses proprietary licensing of the derived
knowledge. Source A's CC-BY-4.0 has **no** share-alike clause, so derivation carries only attribution.

**State the cost honestly in your own write-up.** GHALogs is, as of this review, the only large public
corpus of GHA **run outcomes + logs**. Excluding it means you have **no** offline source of "this workflow
actually passed." §3.1(D4) explains why you cannot fully replace it retrospectively.

---

## 3. Source B — bot dependency-bump PRs

### 3.1 The structural constraint: you cannot build this retroactively

**GitHub Actions run logs and run history are retained for a bounded, repository-configurable period —
90 days by default, reducible by the owner to as little as 1 day** `[S5, §IV-B]`. GitHub maintains a
**workflow run history retention policy** as an explicit administration/billing control `[S16]`. The
GHALogs authors scraped "as fast as possible after the run is finished" precisely because of this, and
used ETag conditional requests to detect new runs cheaply `[S5, §IV-B]`.

**Consequence, stated plainly: a historical green-CI evidence chain for a merged dependency PR cannot be
retrieved from the API today.** Merged PR metadata (`merged_at`, the merge commit SHA) persists
indefinitely; the *check outcomes on that commit* do not. Any evidence layer you build over historical bot
PRs must be assembled **prospectively** (collector running forward, ETag-polling repos) or sourced from a
third party — and your only large third party is license-excluded.

This is the strongest single argument for a collector-first architecture, and it is independent of
everything else in this report.

### 3.2 API approach — endpoints, qualifiers, limits (all cited to official docs)

**Discovery.**

`GET /search/issues` is the only way to enumerate bot PRs across all of GitHub. Qualifiers, all documented:

- `author:app/USERNAME` — "matches issues created by the integration account named `robot`" `[S19]`
- `-author:app/USERNAME` — negation form `[S19]`
- `type:pr` / `is:pr` — restrict to PRs (default returns both issues and PRs) `[S19]`
- `is:merged` — merged PRs `[S19]`
- `status:success` / `status:failure` / `status:pending` — filter by **commit status** `[S19]`
- `review:approved`, `review:changes_requested`, `reviewed-by:`, `review-requested:` `[S19]`
- `language:`, `created:`, `merged:`, `org:`, `repo:`, `user:`, `is:public`, `draft:true|false`,
  `in:title|body|comments`, `linked:pr|issue` `[S19]`
- Boolean: `AND` / `OR` / `NOT`, ranges, comparison, parentheses `[S19]`

**`/search/issues` vs `/repos/{o}/{r}/pulls` — the actual tradeoffs, all documented:**

| | `GET /search/issues` | `GET /repos/{owner}/{repo}/pulls` |
|---|---|---|
| Scope | **Global** (all of GitHub, or per `org:`) | One repository |
| `author:` filter | **Yes** | **No** — only `head`/`base` (branch), `state`, `sort` |
| Pagination | `per_page` ≤100, 1,000 results max | `per_page` ≤100, unbounded pages |
| Rate limit | **30 req/min** (auth) / 10 (unauth) `[S20]` | 5,000 req/hr primary `[S21]` |
| Hard query cap | **256 chars** ex-operators; **≤5** `AND`/`OR`/`NOT` `[S20]` | n/a |
| Search scope cap | **4,000 repositories matched per query** `[S20]` | n/a |
| Failure mode | Timeouts → `incomplete_results: true` (silent partial results) `[S20]` | `304` on ETag |
| `merged_at` | Present in `pull_request` sub-object `[S20]` | Present `[S22]` |
| Bot typing | `author_association: MANNEQUIN` is returned per item `[S20]` | — |

**Operational reading:** `/search/issues` is the *only* way to find bot PRs at scale, but it is
**rate-limited 10× harder per unit time than core API** (30/min = 1,800/hr vs 5,000/hr) and can silently
return partial results. `/repos/{o}/{r}/pulls` cannot filter by author at all, so per-repo bot harvesting
means **client-side filtering on every PR** — wasteful. Hybrid is the sane pattern: use search to find
candidate repos, then `/repos/{o}/{r}/pulls?state=closed` + client-side `user.login` filter per repo, with
ETag conditional requests so unchanged repos cost **zero** primary budget `[S23]`.

**Rate limits as of 2026-09-28 (from GitHub's own docs)** `[S21]`:

- Unauthenticated: **60 req/hr**, keyed to source IP.
- Authenticated (PAT, OAuth, or GitHub App user token): **5,000 req/hr**.
- GitHub App **installation** token: 5,000 req/hr base; **15,000 req/hr** if the installation is on a GitHub
  Enterprise Cloud org; scales **+50 req/hr per repo** (>20 repos) and **+50 req/hr per org user** (>20
  users), **capped at 12,500 req/hr**.
- Enterprise Cloud–owned App/OAuth: **15,000 req/hr**. A 15,000/hr App **consumes** the PAT budget
  (10,000 via App exhausts the 5,000 PAT allowance).
- `GITHUB_TOKEN` in Actions: **1,000 req/hr per repository** (15,000 on GHEC).
- Search: **30 req/min** authenticated, **10 req/min** unauthenticated; `/search/code` **10 req/min, auth
  mandatory** `[S20]`.
- **Secondary limits:** ≤**100 concurrent** requests (shared REST+GraphQL); ≤**900 points/min** for REST
  (**most `GET`/`HEAD`/`OPTIONS` = 1 point; most `POST`/`PATCH`/`PUT`/`DELETE` = 5 points**); ≤90s CPU per
  60s wall; ≤**80 content-creating req/min** and ≤**500 content-creating req/hr**; ≤2,000 OAuth token
  requests/hr. "Subject to change without notice," and undocumented secondary limits exist.

**Budget arithmetic.** Per dependency-bump PR a minimal evidence chain costs roughly: 1 search or list-pulls
hit, 1 `GET /pulls/{n}` (for `merged_at` + changed files), 1 `GET /pulls/{n}/files`, 1–2 check-runs or
statuses calls, plus content fetches. Call it **4–6 core requests**. At 5,000 req/hr that is **~830–1,250
PRs/hr**, i.e. **~20K–30K PRs/day** on a single PAT, before secondary limits. At the search tier (1,800/hr)
you get ~300–450 PRs/hr. **State this budget before committing to corpus size.**

**Required scopes — flagged as uncertain.** The `GET /repos/{owner}/{repo}/pulls` documentation page
describes parameters and response schema but the retrieved page text **does not include a
scopes/permissions table** `[S22]`. For *public* repositories, unauthenticated and authenticated reads
require no OAuth scope — this is well established but I did **not** find it stated verbatim in the
endpoints I fetched, so the specific fine-grained-PAT permission name (`Pull requests: read`) is
`[UNVERIFIED]`. **Verify before implementing.** One verified, load-bearing caveat: *"For requests made by
GitHub Apps with a user access token, you can't retrieve a combination of issues and pull requests in a
single query. Requests that don't include the `is:issue` or `is:pull-request` qualifier will receive an
HTTP 422"* `[S20]` — so **always** send `type:pr`/`is:pr`.

**Two operational gotchas that will silently corrupt a corpus:**

1. **`author:app/dependabot` may not be the right string.** GitHub's docs only show the generic
   `author:app/USERNAME` form `[S19]`. Dependabot's PRs have historically been authored by a plain bot
   *user* login (`dependabot`), not necessarily an app slug, whereas Renovate installs as an app
   (`app/renovate`). `[UNVERIFIED]` — **test both `author:app/dependabot` and `author:dependabot` against
   the live API and compare counts before you build a pipeline on either.** `author_association:
   MANNEQUIN` is available per-item as a cross-check `[S20]`.
2. **`/search/code` is nearly useless here:** it only indexes the **default branch**, only files
   **<384 KB**, requires a search term, and is capped at **10 req/min** `[S20]`. It will not find workflow
   files in old revisions. Use the git tree/diff APIs instead.

### 3.3 Licensing and ToS — what is settled and what is genuinely uncertain

**Settled, from GitHub's own terms (effective 2026-04-27)** `[S17]`:

- **§D.8 — public repo content is not restricted by GitHub.** "By choosing to contribute Content to a
  public repository, you are choosing and directing us to make such Content accessible to everyone on the
  internet. **Unless specifically set forth herein, these Terms do not restrict lawful access to or use of
  the contents of public repositories by third parties**, or by GitHub or its Affiliates."
- **§D.6 — contributions inherit the repository license (inbound = outbound).** "Whenever you add Content
  to a repository containing notice of a license, you license that Content under the same terms."
  → **A `.github/workflows/ci.yml` in an Apache-2.0 repo is Apache-2.0. A GPL-3.0 repo's workflow is
  GPL-3.0.** This is the same viral-contamination problem as GHALogs, at *file* granularity, and it applies
  to Source B even though Source B involves no dataset license at all.
- **§H (API Terms) — mining is not prohibited.** Prohibitions are: excessively frequent requests
  (suspension risk), sharing tokens to evade rate limits, downloading "for spamming purposes, including
  for the purposes of selling GitHub users' personal information, such as to recruiters, headhunters, and
  job boards." Also: "GitHub may offer subscription-based access to our API for those Users who require
  **high-throughput access** or access that would result in **resale of GitHub's Service**."
  → Nothing here forbids building a knowledge corpus from public metadata. But the **§3.2 budget above
  (5,000/hr) is the "ordinary" tier; sustained high-throughput ingestion is contemplated as a
  commercial-agreement product.** That is a commercial decision, not a legal obstacle.
- **§D.9 (Access Reciprocity) — read this carefully, it cuts both ways.** If you use "automated means to
  access, collect, or otherwise use" publicly accessible Content "for the purpose of developing or training
  any commercially available artificial intelligence model, machine learning system, or similar technology
  (a 'Commercial AI System')," you "waive any and all policies, terms, conditions, or contractual
  provisions governing products, services, websites or datasets **you own or operate**" that would restrict
  such Access, and agree "not to impose technical or other targeted measures to restrict or retaliate
  against such Access." Carve-outs: academic research, and any product with **<700M MAU** in the preceding
  calendar month. Note the direction carefully — it waives **your** restrictions, it does **not** grant you
  rights in third-party-copyrighted repo content.
- **§J.2/J.4 — GitHub disclaims AI-output IP warranty.** "Output may resemble code or content in the
  model's training data or that is subject to third-party copyrights and open source license terms. **You
  are responsible for determining whether your use of Output requires a third-party license and for
  complying with any such license.**" That is GitHub telling *its own* users to clear exactly the
  obligation you are asking about.

**Genuinely uncertain — flagged, not guessed:**

1. **Whose license governs the *knowledge* you extract?** Two layers, and the second has no settled
   answer:
   - The **workflow file text and the manifest diff** are unambiguously covered by the repository license
     under §D.6. Verbatim or near-verbatim transcription of step sequences is at minimum attribution-
     bound and, for copyleft (GPL/AGPL/LGPL) repositories, potentially source-disclosure-bound on your
     derived corpus.
   - The **abstract procedure** ("to build this project on Ubuntu, run X then Y") is the thing you actually
     want. Whether a method/idea extracted from a copyrighted work is itself copyrightable, and whether a
     corpus of such extracted methods is an adaptation, is **jurisdiction-dependent and not settled**; the
     Berne Convention and 17 U.S.C. §102(b) exclude "ideas, procedures, processes, systems, methods of
     operation" from copyrightability, but substantial-selectivity / abstraction doctrines can pull
     heavily-structured extractions back in. **I am not going to resolve this. Get counsel, and until
     then: record the repository's SPDX license ID per evidence row, prefer MIT/Apache-2.0/BSD repos, and
     treat GPL-family-sourced knowledge as quarantined.**
2. **Whether your "evidence-backed Procedure" is a database or a derivative work** under
   CC BY-SA / GPL. Unresolved here.
3. **Personal data.** `author_email` appears in Source A's schema `[S2]` (in the paper's own example:
   `tom.mens@umons.ac.be`) and in Source B every bot PR carries a `user` object. This is personal data
   under GDPR. §H singles out "selling GitHub users' personal information" as prohibited; the conservative
   read is that you must have a lawful basis, minimize, and honor deletion. **Not researched further here —
   flagging as a live legal workstream.**

---

## 4. Is "green CI" sufficient evidence of a verified procedure?

**No. The literature does not support it, and it fails on at least five independent mechanisms.**
Strongest contrary evidence, ordered by how directly it attacks the claim:

**(1) The check may verify nothing.** Across Java, Python and C++ GHA repos: **`Execute Tests` appears in
only 27.8% of workflows** vs `Build Project` at 44.2%; **42.1% "build without test"** is the single most
common deviation from GitHub's own canonical CI pipeline, and a further **22.8% "checkout only"** (label /
notify / release pipelines). The authors conclude: "Build-without-test patterns suggest that workflow
design is driven more by ecosystem conventions and platform defaults than by best practices" `[S9]`.
→ A large fraction of green GHA runs are green because *nothing was tested*.

**(2) Green can mean the job did not do its work ("silent failure").** Aïdasso, Bordeleau & Tizghadam:
**11% of *successful* jobs are rerun**, 35% of those reruns >24h later, used as a silent-failure proxy over
**142,387 jobs / 81 industrial projects**. Their manual analysis of 92 public issues yields **11 categories**
of silent failure, most frequent being **artifact operation errors, caching errors, and ignored exit
codes** `[S11]`. The PDF's worked example is a build-and-publish job re-executed twice with no code or
build-script change, all three executions green. → **Exit code 0 is not the same as the task completed.**

**(3) Green can mean a flaky test happened to pass.** On GHA specifically: **3.2% of builds are rerun, of
which 67.73% are flaky**; 15 distinct flakiness categories identified, with **flaky tests 64.99%** of flaky
failures, plus network and dependency-resolution issues `[S12]`. Independently, on Python,
**0.86% of tests exhibit flaky behaviour** (up to 5.3% in production-stable projects) `[S10b]`; and
developers' *documented* remedy — retry-until-pass — is itself a mechanism that converts red into green
`[S10b]`. → **A single green run is one sample from a non-deterministic process. Reruns are the
population.**

**(4) Green can mean the tests don't exercise the changed behaviour.** Hejderup & Gousios, on 521
well-tested Java projects: tests cover only **58% of direct** and **20–21% of transitive** dependency
calls; with **1,122,420 injected artificial faults** across 262 projects, test suites detect only **47% of
direct** and **35% of indirect** faults. Their conclusion: "the combination of static and dynamic analysis
should be a **requirement** for future dependency updating systems" `[S13]`. The follow-up on Dependabot's
own compatibility score — 579,206 PRs, 618,045 score records — finds the score is **uncomputable for 83%
of updates**, and that computable scores rest on **"low-quality data"**; the authors explicitly recommend
bots "consider **the quality of tests that exercise candidate updates**" `[S17b]`. → **For a dependency-bump
source specifically, this is the decisive citation: the green is computed from tests that demonstrably
miss most of the surface that changed.**

**(5) Green can be formally satisfied by tests that pass vacuously.** On AtCoder — the closest available
proxy for an "official authoritative test suite" — a certification chain (multiple independently written
accepted solutions agreeing on every test's expected output, brute-force tie-breaking, per-problem input
validators) identified **589 verified accepted-but-buggy submissions among 20,375 audited accepted
submissions (~2.9%)**, with a **union floor of 906** across five agents. All five agents stayed within
1.7pp of official-suite coverage on the bugs those suites *do* catch — i.e. the gap is specifically the
bugs the official suite **misses** `[S14]`. → **An authoritative, human-curated, adversarial test suite
misses ~3% of real bugs. A CI suite is weaker than that.** This is also the single best citation for Q10(a)
in §6.

**Two additional mechanisms specific to "the PR was green":**

**(6) A green *status* may be a required check that never ran.** Reported in production writeup as "The CI
Check That Never Ran Once Gave Us a Fake Green" — an extension-validation step compared an expected ABI
against an artifact that genuinely was built with the intended toolchain, i.e. the step was structurally
bypassed `[S15]`. This is a `[PRODUCTION WRITEOUT]`, not peer-reviewed, and there is no clean academic
quantification of the phenomenon. **Weakest single citation in this list; do not lead with it.**

**(7) Developers merge with red CI, and post-merge quality ≠ merge success.** A 2026 qualitative study of
952 repos with run histories documents the practice directly: *"LGTM, fedora failure is unrelated"* merged
over a failing platform job; *"I am happy to remove the coverage run. I have not looked at the results in
years…"*; and systematic ignoring of PR failures that get no reaction/fix commit `[S16]`. On the other side,
an analysis of **1,210 merged agent-generated PRs** via SonarQube differential analysis (base vs merge
commit) found code smells dominate at critical/major severity, and concludes: **"merge success does not
reliably reflect post-merge code quality"** `[S16b]`. → **Merge + green is a weak proxy, and humans
frequently override it anyway.**

**What the literature DOES establish, positively.** CI re-execution is a *defensible* oracle when it is
the *only* oracle and when you accept its cost: CI-Repair-Bench deliberately evaluates repair correctness
"**exclusively through full CI re-execution under original workflows**," enforcing all stages — formatting,
linting, config checks, environment setup, tests — and finds repair works for tool-enforced failures but
collapses on dependency/environment/configuration/workflow-logic failures (**best LLM: 18.9% success**)
`[S8]`. GHA-Repair's hybrid verifier (structural preservation + SMT control-flow equivalence on `on`,
`if`, concurrency) reached **80.7% smell-removal recall but only 41.3% scope-verified patch rate** — i.e.
even *generation* quality is far below the recall figure, and the gap is exactly the verification gap
`[S7]`. → **The right reading: full CI re-execution is a good *necessary* condition and a poor *sufficient*
one. Layer it, don't rely on it.**

**Bottom line for your design.** The honest, defensible claim is:

> "This procedure was **observed to execute to completion** in N independent CI runs across M
> repositories/ecosystems, with a **specific named check** as the evidence record."

That is a *much* stronger and more truthful claim than "verified correct," and it is fully supportable. It
is also what a re-execution oracle in the style of CI-Repair-Bench `[S8]` actually measures.

---

## 5. Quality and contamination risks, per source

### Source A — workflow histories

| Risk | Severity | Evidence / mitigation |
|---|---|---|
| **No outcome data at all** | **Critical** | `[S5, §II]`. Mitigation: Source A is a *candidate generator only*. Evidence must come from Source B or a prospective collector. |
| **~23% snapshots unparseable YAML; 30,922 histories dropped** | High | `[S6]`. Filter on `valid_yaml`; expect ~88% of histories. Loss is non-random. |
| **Pre-2024-07-09 `valid_yaml` flag is known-wrong** | High | `[S2]`. Do not use versions `≤10.5281/zenodo.12698016`. |
| **Survivorship bias: de-adopters excluded** | High | `[S1, §5.3]`. Your negatives are systematically missing. Fix only by self-collecting. |
| **Content-hash collapse (10.36%) + cross-repo `previous_file_hash` aliasing** | High | `[S1, §3]`. Dedup on content; never infer repo identity from `file_hash`. |
| **No action/language field** | Medium | `[S2]`. Parse from content; plan for it as a cost. |
| **YAML 1.1 `on:`→`True` corruption** | Medium | `[S18]`. Use a YAML-1.2 parser; assert on the trigger key. |
| **`valid_workflow` is a third-party JSON Schema** | Medium | `[S3]`. Weak hint only. |
| **Five-plus undocumented schema changes (paper → live)** | Medium | §2.2. **Pin one version DOI; write a schema-drift assertion.** |
| **Zenodo double-gzip breaks MD5 verification** | Medium | `[S2]`. Verify against the *inner* payload. |
| **Wrong DOI in the conference program (`…10259014`)** | Low but nasty | `[S4]`. Seed from the ACM abstract, not the program page. |
| **Selection bias: ≥100 stars, ≥300 commits** | Low (for reuse) | `[S1, §5.2]`. Well-documented; state it as a generalizability limit. |

### Source B — bot dependency PRs

| Risk | Severity | Evidence / mitigation |
|---|---|---|
| **No historical outcome data — retention is bounded and owner-configurable (90d default, as low as 1d)** | **Critical** | `[S5, §IV-B]`, `[S16]`. **Collector must run forward.** This is architectural. |
| **Repository license governs the workflow file verbatim; GPL-family is viral** | **Critical** | `[S17 §D.6]`. Store SPDX ID per evidence row; quarantine copyleft. |
| **Abstract-procedure licensing is unsettled** | **Critical, unresolved** | §3.3. Counsel required. Do not guess. |
| **Personal data in `user` objects and `author_email`** | High | `[S2]`, `[S17 §H]`. Minimize; lawful basis; honor deletion. |
| **Bot identity qualifier may be wrong** | High | `[S19]` docs show only generic form. Test both forms; cross-check `author_association: MANNEQUIN`. |
| **Search silently truncates** | High | `[S20]`: 1,000-result cap, 4,000-repo scope cap, 256-char/5-operator query cap, `incomplete_results: true` on timeout. **Treat `incomplete_results` as a hard error, not a warning.** |
| **Search is 10× slower per unit time than core API** | High | `[S20]` vs `[S21]`. 30/min vs 5,000/hr. |
| **Secondary limits undocumented; suspension risk** | High | `[S21]`. ≤100 concurrent, ≤900 pts/min, ≤80 content-creating/min. |
| **Bot PRs are not a uniform population** | Medium | Dependabot ≈70% merge rate but *security* PRs are 65.42% merged and are only ~6.9% of the corpus `[S25][S25b]`; Renovate PRs are grouped and differ structurally. **Stratify; do not pool.** |
| **11.3% of Dependabot adopters deprecate it** | Medium | `[S25]`. Churn biases any "long-lived config" inference. |
| **CI coverage on bot PRs is incomplete** | Medium | Only **71.1%** of 540,665 Dependabot PRs had *any* CI check result `[S25]`. ~29% have **no green signal at all** — exclude, don't infer. |
| **Lockfile-only / manifest-only bumps carry no test signal** | Medium | `[S26]` DepBench explicitly filters these out. Mirror that filter. |

---

## 6. Recommendations for the build

Each tied to a citation. These are epistemic/process recommendations, not software design.

**R1 — Rename the evidence tier. Do not ship "verified."** Ship a graded predicate and make the grade
visible in the record: `executed_to_completion` (named check, named run) > `merged_with_ci_signal` >
`merged_unverified` > `candidate`. The literature supports the *first* grade and is silent-to-negative on
the others `[S8][S7][S14]`. The single most defensible sentence you can write is the one in §4's bottom
line.

**R2 — Source A is a candidate generator, not an evidence source. Encode that in the schema.** Any
Procedure derived from Source A must carry `evidence_class = "static_configuration"` and must never be
served as verified. `[S5, §II]` is your citation for why this is not a limitation you can engineer around.

**R3 — Pin an exact version DOI, and write a schema-drift assertion.** Use
`10.5281/zenodo.20340547` (2026-05-22, CC-BY-4.0) or an explicitly older pin. Assert the 17 columns
present, assert `git_change_type ∈ {A,D,M,R}`, assert `uid` non-null. This is not paranoia — the schema
has already changed five times since publication and the `valid_yaml` flag shipped broken once `[S2]`.

**R4 — Make the evidence predicate check-scoped, not PR-scoped.** Record
`{check_name, conclusion, run_id, head_sha}` where `head_sha` is the **merge commit**. Never store
"PR was green." `[S11]` (ignored exit codes), `[S15]` (check never ran), `[S16]` (merged with red CI).

**R5 — Require rerun-stability or explicit single-run labelling.** If you have ≥2 runs, record the
distribution. If exactly 1, say so in the record. `[S12]` (67.73% of rerun builds are flaky) and `[S10b]`
(0.86–5.3% flaky test rate) make a single sample a weak observation.

**R6 — Require a test-execution witness, not a green build.** A Procedure whose evidence names a
build/lint-only check should be capped at a lower grade. `[S9]`: 42.1% of workflows build without testing;
27.8% execute tests at all. This is cheap to compute from the check name + the workflow YAML.

**R7 — Collect prospectively or not at all.** Because of retention `[S5, §IV-B]`, budget for a
forward-running collector with ETag conditional requests (`304` costs zero primary budget `[S23]`) and
`x-poll-interval` respect. This is a scheduling/ops commitment, not a data-formatting one.

**R8 — Store the source repository's SPDX license ID on every evidence row, and quarantine copyleft.**
`[S17 §D.6]`. This makes the license question auditable per-item instead of a corpus-wide blocker, and it
lets you ship MIT/Apache/BSD-sourced knowledge now while the abstract-procedure question goes to counsel.

**R9 — Treat `incomplete_results: true` from `/search/issues` as a failed collection.** `[S20]`. Silent
truncation is the failure mode that will quietly under-count your corpus and you will not notice.

**R10 — Keep failure knowledge, and mine it deliberately.** Wen, Nagy, Lanza & Bavota's "quick remedy
commits" — commits that rapidly follow a same-author commit to repair omitted changes — are the closest
thing in the literature to a *negative-example* signal, with a 69-type taxonomy of which 20 are genuinely
compensatory; categories include `Fix Broken Test` and `Build Issue` `[S27]`. Note their own caveat:
treating reverted commits as noise contributes 0.07–0.27 noisy data points for bug-fix and refactoring
mining respectively, so **the effect on your corpus is small but non-zero and should be measured, not
assumed**. Also mine non-merged bot PRs — Dependabot's own compatibility-score machinery exists precisely
because most updates never get merged `[S17b]`.

**R11 — Avoid tautological verification by construction: the oracle must be independent of the
generator.** The design pattern with the strongest support in the literature is the certification chain in
`[S14]`: agreement across *independently written* solutions, brute-force ground truth for disagreements,
and a per-input legality validator — precisely so the verdict does not rely on the judge being right. The
symptom to avoid is documented as a pipeline pattern: *"Completions passing the respective unit tests are
considered valid code solutions"* — generation and test synthesized by the same model family, which is how
vacuous green suites enter `[S28]`. The complementary hazard, benchmark contamination, is quantified in
`[S28]` (direct + synthetic-data leakage channels) and `[S29]` (leakage detection via train/test perplexity
deltas, with the honest limitation that near-zero delta is ambiguous between "no leakage" and "both splits
leaked"). **For your pipeline this means: never let the same model that extracted a Procedure also author
its verification.** `[S30]` gives the structural example — code-mutation-based repair scored by the
project's *own* existing tests is accepted practice, and is exactly the reusable case you are building.

**R12 — Prefer a re-execution oracle where the cost allows, and know its measured ceiling.** CI-Repair-Bench
shows full native CI re-execution is a legitimate oracle and that the achievable rate on
realistic failure modes is **18.9%** `[S8]`; GHA-Repair shows verification, not generation, is the binding
constraint (80.7% recall vs **41.3% verified**) `[S7]`. Calibrate expectations accordingly: verification
is the scarce resource, and its measured cost is roughly 2× generation effort. DepBench's construction is
the reference recipe for a *dependency*-specific executable oracle — Docker-executed, fail→pass, with
upstream-evidence grounding and three explicit quality gates `[S26]`.

**R13 — Stratify, do not pool.** Dependabot version updates, Dependabot security updates, Renovate
grouped updates, and human upgrade PRs have different acceptance rates, different CI coverage, and
different failure modes `[S25][S25b][S17b]`. Any capability score computed over the union is a weighted
average of incomparable things.

**R14 — Calibrate against the best available test-quality baseline, and expect a large correction.**
Hejderup & Gousios' finding is the single best guide to the *size* of the correction you owe: even for
**well-tested** projects with assurance badges, tests miss >50% of injected direct-dependency faults
`[S13]`. Your dependency-bump evidence inherits that gap almost exactly, because it is the same tests.
Applying Dependabot's own crowd-based estimate, 83% of updates have **no** computable compatibility score
and what exists rests on low-quality data `[S17b]`.

---

## 7. Negative / null results — reported explicitly

1. **No study measures the post-merge breakage rate of Dependabot/Renovate PRs that merged with green CI.**
   I searched for this specifically and repeatedly. The nearest neighbours each cover a different slice:
   `[S26]` measures breakage *detected at upgrade time* on a curated benchmark; `[S27]` measures
   quick-remedy commits (not bot-specific); `[S16b]` measures post-merge quality of *agent*-generated PRs;
   `[S16]` documents humans merging *red* CI. **The literature does not establish a post-merge breakage
   rate for bot dependency PRs with green CI.** If your substrate's core claim rests on this number, you
   would be the first to measure it — which is an argument for building the collector, not for assuming the
   number is small.
2. **No 2025–2026 paper or issue reports the MSR'24 dataset as broken, corrupt, or withdrawn.** I looked
   and found none. The problems I *can* cite are the authors'/maintainers' own (§2.5) and two downstream
   works that reused it successfully `[S6][S7]`.
3. **No published precision/recall for `valid_workflow`**, and no inter-rater agreement for the JSON-Schema
   check `[S3]`.
4. **No study establishes whether N forks of an identical workflow constitute 1 procedure or N independent
   corroborations.** Given the 10.36% content-dedup `[S1, §3]`, this is a live modelling decision with no
   literature to lean on. Pick one, state it, and make it a parameter.
5. **No academic quantification of the "required check never ran → fake green" phenomenon.** Only the
   production writeup `[S15]`. Treat as a design risk to defend against, not a finding.
6. **No literature on whether a workflow *file* revision is a good unit of reusable procedural knowledge.**
   The closest adjacent work is *generation* of workflow configs from NL (`[S31]`: up to 69% similarity
   but only **3% perfect matches**; `[S32]`: 67,870 repos, GH-WCOM; `[S33]`: GPT-3.5/4 workflow
   generation; `[S34]`: 127,559 workflow files across 7 CI systems, 8.25 recommendations/repo at 96.1%
   YAML-valid). **All of it is generation; none of it is validation.** The negative result is the point:
   a 96.1%-YAML-valid recommendation set `[S34]` and a 3%-perfect-match generation result `[S31]` are the
   ceiling of the field. There is no published pipeline that mines workflows into *validated* procedures.
7. **The MSR'24 paper's limitations section does not mention the absence of run outcomes** `[S1, §5]`. Do
   not cite the limitations section for that claim; cite GHALogs' related work `[S5, §II]` or the
   absence of outcome columns in the schema `[S2]`.

---

## 8. Bibliography

Ordered by tier. All identifiers resolvable as of 2026-09-28.

### Source A — workflow corpus

- **[S1] [PEER-REVIEWED]** Cardoen, G., Mens, T., Decan, A. *A dataset of GitHub Actions workflow histories.* MSR 2024 Data & Tool Showcase Track, pp. 677–681. **DOI `10.1145/3643991.3644867`**. ACM DL: `https://dl.acm.org/doi/10.1145/3643991.3644867`. Author mirror (full text incl. §5 Limitations): `https://decan.lexpage.net/files/msr-2024d.pdf`. *Primary source for: definition of a workflow history, Table 1 counts, Table 3 schema, the four stated limitations, the 10.36% content-dedup figure, the "no Travis data" self-limitation.*
- **[S2] [DATASET CARD]** Cardoen, G. *A dataset of GitHub Actions workflow histories.* Zenodo. **Concept DOI `10.5281/zenodo.10259013`**; **current version `10.5281/zenodo.20340547`** (2026-05-22), **CC-BY-4.0**, 4.1M+ workflows / 52.9K+ repos, 5 files ≈3.7 GB. Earlier versions: `…12698016` (2024-07-09), `…13985548` (2024-10-25), `…15221545` (2025-04-15). API: `https://zenodo.org/api/records/20340547`. *Primary source for: the live 17-column schema, `uid` semantics, `R` rename value, double-gzip warning, SEART filter evolution, the `valid_yaml` fix.*
- **[S3] [TECH REPORT / TOOL]** Cardoen, G. *gigawork — An automated tool for extracting GitHub Actions' workflows from Git repositories.* PyPI: `https://pypi.org/project/gigawork`; source `https://github.com/cardoeng/gigawork`. *Source for: the `valid_yaml` / `probably_workflow` / `valid_workflow` definitions and the fact that the validation schema is third-party (`json.schemastore.org/github-workflow.json`).*
- **[S4] [PRODUCTION WRITEOUT]** MSR 2024 program page, *A dataset of GitHub Actions workflow histories.* `https://2024.msrconf.org/details/msr-2024-data-and-tool-showcase-track/5/`. *Cited only as evidence of the DOI typo (`…10259014`).*

### GHALogs — the excluded source

- **[S5] [PEER-REVIEWED]** Moriconi, F., Durieux, T., Falleri, J.-R., Troncy, R., Francillon, A. *GHALogs: Large-Scale Dataset of GitHub Actions Runs.* MSR 2025 Data & Tool Showcase Track, pp. 669–673. **DOI `10.1109/MSR66628.2025.00104`** (ACM mirror `10.1145/10.1109/MSR66628.2025.00104`). Author PDF: `https://s3.eurecom.fr/docs/msr25_moriconi.pdf`. Code: `https://github.com/D2KLab/gha-dataset`. *Primary source for: the explicit statement that Source A "does not provide performance or outcome data"; selection criteria; 90-day log retention and the 5-runs-per-workflow cap; the `conclusion ∈ {success, failure, timed_out}` filter; ETag/conditional-request practice; the 5000 req/hr figure.*
- **[S6b] [DATASET CARD]** Moriconi, F., et al. *GHALogs: Large-Scale Dataset of GitHub Actions Runs.* Zenodo `10.5281/zenodo.10154920` (2024-12-04), **`license.id = cc-by-sa-4.0`**. Files: `github_run_logs.zip` 142.3 GB, `runs.json.gz` 1.1 GB, `repositories.json.gz` 69.2 MB. API: `https://zenodo.org/api/records/10154920`. *Primary source for the license determination.*

### Downstream reuse and quality reporting

- **[S6] [PEER-REVIEWED + PREPRINT]** Rostami Mazrae, P., Decan, A., Mens, T., Wessel, M. *An Empirical Study of the Evolution of GitHub Actions Workflows.* **Journal of Systems and Software 236:112824 (2026)**, **DOI `10.1016/j.jss.2026.112824`**; preprint **arXiv:2602.14572** (v3, 2026-03-01). Replication package: `10.5281/zenodo.18414824`. Underlying data: `10.5281/zenodo.17301952`. *Primary source for: 776,339 / 3,418,911 invalid-YAML snapshots; exclusion of 30,922 histories; the A→B→C aggregation hazard; 7.3% of workflow files changed weekly; 236,775 final histories / 2,640,584 snapshots.*

### Source A-adjacent: workflow quality, repair, generation

- **[S7] [PEER-REVIEWED (accepted)]** Kim, N., Lee, E. *GHA-Repair: Trustworthy Automated Repair for GitHub Actions Workflows via Two-Phase Generation and Hybrid Verification.* **ICSME 2026** Research Papers Track (Benevento, 2026-09-14/18). Program: `https://conf.researchr.org/details/icsme-2026/icsme-2026-papers/34/`. Replication package: **`10.5281/zenodo.20812923`** (2026-06-23), code MIT, data CC-BY-4.0, **curated from `10.5281/zenodo.13985548`**. *GHA-Evo-7K = 6,998 paired workflows; 13,380 evolution traces; 80.7% smell-removal recall vs 41.3% scope-verified patch rate; "smell masking" and "smell persistence." IEEE proceedings DOI not yet resolvable — `[UNVERIFIED]`.*
- **[S8] [PREPRINT]** Muna, R. K., Rafi, M. N., Chen, T.-H. (P.). *CI-Repair-Bench: A Repository-Aware Benchmark for Automated Patch Validation via CI Workflows.* **arXiv:2604.27148** (v2, 2026-05-04). Benchmark: `https://github.com/CI-Repair/CI-Repair-bench`. *567 instances / 103 repos / 12 CI error types; oracle = full GHA re-execution; best LLM 18.9%.*
- **[S9] [PREPRINT]** Abrokwah, E., Ghaleb, T. A. *An Empirical Study of Complexity, Heterogeneity, and Compliance of GitHub Actions Workflows.* **arXiv:2507.18062** (v2, 2026-08-03). *42.1% build-without-test; 27.8% Execute Tests; 22.8% checkout-only; 39.5% common prefix; only CodeQL exceeds the 5% global threshold.*
- **[S10a] [PREPRINT]** Luo, S., et al. *Empirical Study of Restarted and Flaky Builds on Travis CI.* **arXiv:2003.11772** (v2). *8,384 failed → 4,586 passing on restart; 53.42% restart within 1h.*
- **[S10b] [PEER-REVIEWED conf. / PREPRINT]** Luo, S., et al. *An Empirical Study of Flaky Tests in Python.* **arXiv:2101.09077**; IWSC 2021 (ACM DL — *venue DOI `[UNVERIFIED]`*). *0.86% overall, 5.3% production-stable; documents retry-until-pass as a green-producing mechanism.*
- **[S10c] [PREPRINT]** *Understanding and Detecting Flaky Builds in GitHub Actions.* **arXiv:2602.02307** (2026-02-02). *3.2% of builds rerun; 67.73% of those flaky; 15 categories; flaky tests 64.99%.*
- **[S11] [PREPRINT]** Aïdasso, H., Bordeleau, F., Tizghadam, A. *On the Illusion of Success: An Empirical Study of Build Reruns and Silent Failures in Industrial CI.* **arXiv:2509.14347** (2025-09-17), CC BY-NC-ND 4.0. *142,387 jobs / 81 projects; 11% of successful jobs rerun; 35% >24h; 11 silent-failure categories (artifact ops, caching, ignored exit codes); mixed-effects AUC 0.85 on 32 variables.*
- **[S12] [PREPRINT]** Huang, A., da Costa, D. A., Dick, G., El Mezouar, M. *Is this Build Failure Related to my Patch?* **arXiv:2605.05564**; **EMSE (2026), DOI `10.1007/s10664-026-10874-8`**. *77,354 CI failures / 7 Apache projects; 10,316 potentially unrelated; median 4h developer time; 20% unrelated test failures; PU-learning precision 0.70–0.88, AUC 0.63–0.97.*
- **[S13] [PEER-REVIEWED]** Huang, Y. A., et al. *""Good" and "Bad" Failures in Industrial CI/CD.* **arXiv:2504.11839**. *pre-merge:post-merge failure ratio 5:3; check-count ratio 15:1; "good" vs "bad" failure framing.*
- **[S15] [PRODUCTION WRITEOUT]** *The CI Check That Never Ran Once Gave Us a Fake Green.* `https://www.todzhang.com/blogs/tech/en/ci-fix-verification-blind-spots`. *Lowest-tier source in this report. Use only as a design-risk illustration.*
- **[S16] [PREPRINT]** Khatami, A., Brandt, C., Zaidman, A. *Beyond the YAML File: Understanding Real-World GitHub Actions Workflow Adoption.* **arXiv:2604.17662** (v2, 2026-06-09), CC BY 4.0. *952 repos w/ run histories; documents "LGTM, fedora failure is unrelated"; systematic ignoring of PR/main-branch failures; failure rate 30.3% (high-evolution) vs 3.2% (none); cites GitHub's official workflow-run retention policy.*
- **[S16b] [PREPRINT / ACM]** Cynthia, S. T., Al Muttakin, Roy, B. *Beyond Bug Fixes: An Empirical Investigation of Post-Merge Code Quality Issues in Agent-Generated Pull Requests.* **arXiv:2601.20109**; **DOI `10.1145/3793302.3793615`**. *1,210 merged agent PRs; SonarQube differential base-vs-merge; "merge success does not reliably reflect post-merge code quality."*
- **[S17] [PRODUCTION WRITEOUT — OFFICIAL]** *GitHub Terms of Service*, effective **2026-04-27**. `https://docs.github.com/en/site-policy/github-terms/github-terms-of-service`. *Cited: §D.5, §D.6 (inbound=outbound), §D.8 (public repos unrestricted), §D.9 (Access Reciprocity + <700M MAU carve-out), §H (API Terms), §J.2/J.4 (third-party license responsibility), §O (as-is).*
- **[S17b] [PREPRINT]** Rombaut, B., Cogo, F. R., Hassan, A. E. *Leveraging the Crowd for Dependency Management: An Empirical Study on the Dependabot Compatibility Score.* **arXiv:2403.09012** (2024-03-14). *579,206 Dependabot PRs; 618,045 score records; **83% uncomputable**; computable scores rest on low-quality data; recommends bots consider test quality of candidate updates; notes clients group build+test+deploy into one check workflow, blocking the merge-status study.*
- **[S18] [PRODUCTION WRITEOUT]** PyYAML issue **#855** (2025-04-13) and pinned **#486**. `https://github.com/yaml/pyyaml/issues/855`. *PyYAML = YAML 1.1; `on:` → boolean `True`; GHA uses a YAML-1.2 subset. Maintainer confirms intentional and points to `yamlcore` for 1.2 behaviour.*
- **[S19] [PRODUCTION WRITEOUT — OFFICIAL]** *Searching issues and pull requests* (qualifier reference). `https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests`. *Cited: `author:app/USERNAME`, `-author:app/USERNAME`, `type:pr`/`is:pr`, `is:merged`, `status:success|failure|pending`, `review:*`, `language:`, `merged:`, `created:`, `org:`, `is:public`, `linked:*`, `author_association` semantics.*
- **[S20] [PRODUCTION WRITEOUT — OFFICIAL]** *REST API endpoints for search.* `https://docs.github.com/en/rest/search/search`. *Cited: 30 req/min auth (search), 10 req/min `/search/code` auth-only, 10 req/min unauth; 1,000-result cap; 4,000-repo scope cap; 256-char / 5-operator query cap; `incomplete_results`; the GitHub-App-user-token 422 rule requiring `is:issue`/`is:pull-request`; `/search/code` default-branch-only and 384 KB file limit; `author_association` enum incl. `MANNEQUIN`; `merged_at` in `pull_request`.*
- **[S21] [PRODUCTION WRITEOUT — OFFICIAL]** *Rate limits for the REST API.* `https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api`. *Cited: 60/hr unauth; 5,000/hr auth; 15,000/hr GHEC App/OAuth; App installation 5,000 base + 50/repo + 50/user, cap 12,500; GITHUB_TOKEN 1,000/hr/repo; secondary: 100 concurrent, 900 pts/min REST (GET/HEAD/OPTIONS=1, POST/PATCH/PUT/DELETE=5), 90s CPU/60s, 80 content-creating/min, 500/hr, 2,000 OAuth tokens/hr; `x-ratelimit-*` headers authoritative; `GET /rate_limit` free of primary.*
- **[S22] [PRODUCTION WRITEOUT — OFFICIAL]** *REST API endpoints for pull requests.* `https://docs.github.com/en/rest/pulls/pulls`. *Cited: `GET /repos/{owner}/{repo}/pulls` params (`state`, `head`, `base`, `sort`, `per_page`≤100) and **absence of an author filter**; `merged_at` in `Pull Request Simple`; `GET /pulls/{n}/merge` → 204/404; `GET /pulls/{n}/files` 3,000-file cap; `mergeable: null` semantics. No scopes table in retrieved text — see §3.2.*
- **[S23] [PRODUCTION WRITEOUT — OFFICIAL]** *Best practices for using the REST API.* `https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api`. *Cited: 304-on-conditional-request does not count against primary limit; `x-poll-interval`; stable sort order for cacheable pagination; serial requests; 1s between mutative requests; exponential backoff; 404 ≠ absent for private resources.*

### Source B — dependency-bump PRs

- **[S25] [PEER-REVIEWED]** He, R., He, H., Zhang, Y., Zhou, M. *Automating Dependency Updates in Practice: An Exploratory Study on GitHub Dependabot.* **IEEE TSE 49(8)**, **DOI `10.1109/TSE.2023.3278129`**; preprint **arXiv:2206.07230** (v3, 2023-05-25). *540,665 Dependabot PRs over 15,390 dependencies / 167,841 version pairs; **71.1% had a CI check result**; merge rates 70.13% / 73.71% / 76.01% across the three PR strata; security PRs 65.42% merged and only 6.9% of corpus; 11.3% of projects deprecate Dependabot; "update suspicion" and notification fatigue; quotes developer testimony "we've been frequently bitten by dependency upgrades causing breakages."*
- **[S25b] [PEER-REVIEWED]** Alfadel, M., da Costa, D. A., Shihab, E., Mkhallalati, M. *On the Use of Dependabot Security Pull Requests.* **MSR 2021** Technical Papers. `https://2021.msrconf.org/details/msr-2021-technical-papers/25/`. **DOI `[UNVERIFIED]`.** *65.42% of security PRs accepted, often merged within a day.*
- **[S24] [PEER-REVIEWED]** Rebatchi, H., Bissyandé, T. F. d'A., Moha, N. *Dependabot and security pull requests: large empirical study.* **EMSE 29(5) (2024)**, **DOI `10.1007/s10664-024-10523-y`**. *9,916,318 PR-related issues / 1,743,035 projects / 10+ languages; Dependabot >65% of dependency-management activity; 71% manual / 29% auto-merged; threat lifetime 512 days.*
- **[S26] [PREPRINT]** *DepRepair: LLM-Based Source-Code Repair for Dependency Breaking Changes* (introduces **DepBench**). **arXiv:2607.17957** (2026-07-20). *95 real dependency-update instances across 4 ecosystems, Docker-executed consumer-test oracle; 3 quality gates (repo / PR / evidence) explicitly excluding lockfile-only and manifest-only changes; grounded in distilled cross-repository upstream evidence. Reference: Frunkte & Krinke, *Automatically Fixing Dependency Breaking Changes*, Proc. ACM Softw. Eng. 2 (FSE 2025) — cited-only, DOI `[UNVERIFIED]`. Henkel & Diwan, *CatchUp!* — cited-only, `[UNVERIFIED]`.*
- **[S27] [PEER-REVIEWED]** Wen, F., Nagy, C., Lanza, M., Bavota, G. *Quick remedy commits and their impact on mining software repositories.* **EMSE 27:14 (2022)**, **DOI `10.1007/s10664-021-10051-z`**; PMC **`PMC8553712`**, PMID **34744487**. *500 manually labelled; 1,500 candidates from 1,497 Java projects; 69 types / 20 genuinely compensatory; `Fix Broken Test` (79) and `Build Issue` (68); 0.07–0.27 noisy data points; preprint `https://csnagy.github.io/research/pdfs/2020/Wen2020-preprint.pdf` (ICPC'20).*
- **[S31] [PREPRINT]** *Can LLMs Write CI? A Study on Automatic Generation of GitHub Actions Configurations.* **arXiv:2507.17165** (2025-07-23). *6 LLMs; **up to 69% similarity, only 3% perfect matches**; code-pretrained models underperform general-purpose on YAML CI.*
- **[S32] [PREPRINT]** *Toward Automatically Completing GitHub Workflows* (**GH-WCOM**). **arXiv:2308.16774** (v3, 2023-09-06). *67,870 repos (29,778 using GHA); T5-based. Venue DOI `[UNVERIFIED]`.*
- **[S33] [PREPRINT]** Mehta, D., Rawool, K., Gujar, S., Xu, B. *Automated DevOps Pipeline Generation for Code Repositories using Large Language Models.* **arXiv:2312.13225** (2023-12-20). *GPT-3.5/4; exact-match, BLEU, and a DevOps-aware score; Probot-based GitHub App.*
- **[S34] [PREPRINT]** *LLM-Driven CI-CD Workflow Intelligence for Cyber Systems Engineering.* **arXiv:2607.04579** (2026-07-06). *59,550 repos ≥1,000 stars → 34,225 with CI/CD → 127,559 config files across 7 CI systems; few-shot prompting best; 8.25 recommendations/repo at **96.1% YAML-valid**.*

### Cross-cutting — methodology of honest verification

- **[S14] [PREPRINT]** Xie, S., Xie, S., Zhu, F., Ji, Y., Zuo, W. *Coding Agents as Test-Suite Auditors: Finding What Official Suites Miss While Approaching What They Catch.* **arXiv:2608.01715** (2026-08-03). ***589 verified accepted-but-buggy submissions among 20,375 audited AtCoder accepted submissions; union floor 906 across five agents; all agents within 1.7pp of official-suite coverage on caught logic bugs.*** *The single most load-bearing source in §4 and §6-R11.*
- **[S28] [PREPRINT]** *On Leakage of Code Generation Evaluation Datasets.* **arXiv:2407.07565** (v3, 2024-10-03), CC BY 4.0. *Direct + synthetic-data contamination channels; the "completions passing LLM-synthesised unit tests are considered valid" pipeline pattern; LBPP as a leakage-controlled benchmark.*
- **[S29] [PREPRINT]** *Benchmarking Benchmark Leakage in Large Language Models.* **arXiv:2404.18824** (2024-04-30). *Perplexity-delta detection; honest limitation that a near-zero train/test delta is ambiguous between no leakage and both-splits-leaked.*
- **[S30] [PREPRINT]** Petrović, G., Ivanković, M., Fraser, G., Just, R. *Practical Mutation Testing at Scale.* **arXiv:2102.11378** (v2, 2021-02-26). *24,000+ developers, 1,000+ projects; diff-aware mutation in code review; human-reviewed high-score mutants 18.0% → 71.0%.* Companion: *Does mutation testing improve testing practices?* **arXiv:2103.07189** (ICSE'21).
- **[S35] [PREPRINT]** *How Far Are We from Detecting Flaky Tests? On the Limits of Code-Based Detection.* **arXiv:2607.09345** (2026-07-10). *Of 86 flaky e2e tests mined from CI logs that passed and failed on the same commit, test code + CI log explained only **42%**; **58% required further execution evidence**.*
- **[S36] [PREPRINT]** *Silent Failure in LLM Agent Systems: The Entropy Principle.* **arXiv:2606.08162** (2026). *40,000+ controlled trials, 100,000+ agent interactions. Relevant to self-monitoring failure modes in an agent-served substrate.*
- **[S37] [PREPRINT]** Li, C., Behrang, F., Shi, A., Liu, P. *FlakyGuard: Automatically Fixing Flaky Tests at Industry Scale.* **arXiv:2511.14002** (ASE 2025). *47.6% of reproducible flaky tests repaired, 51.8% developer-accepted; names the "context problem" (too little vs too much context) that constrains LLM repair.*
- **[S38] [PREPRINT]** Kula, R. G., German, D. M. *An Empirical Study of Python Library Migration (PyMigBench / PyMigTax).* **arXiv:2207.01124**; artifact `https://doi.org/10.6084/m9.figshare.24216858.v2`. *3,096 labelled migration code changes, 335 migrations, 311 client repos, 141 library pairs, 35 domains. The canonical "migration-related code change" taxonomy.*
- **[S39] [PREPRINT]** *MigrateLib: a Tool for End-to-End Python Library Migration.* **arXiv:2510.08810** (v3, 2026-02-23). *314 benchmark developer changes; of 25 PyMigBench migrations with unit tests covering the changed code, LLMs correctly migrated **36–64%** (GPT-4o highest).*
- **[S40] [PREPRINT]** Islam, M. M., et al. *An Empirical Study of Python Library Migration Using Large Language Models.* **arXiv:2504.13272** (2025). *Llama 3.1 / GPT-4o mini / GPT-4o; "Unseen migrations" — LLMs depend on parametric API knowledge, which is not auditable evidence.*
- **[S41] [PREPRINT]** *What a diff makes: automating code migration with large language models* (**AIMigrate**). **arXiv:2511.00160** (2025). *Explicitly states: "The workflow sketch does not include checks of the generated code; any code produced by the LLM should be inspected for both safety and quality."*
- **[S42] [PREPRINT]** *On Leakage of Code Generation… / SZZ validity* — Herbold, S., et al. *Problems with SZZ and Features: An empirical study of the state of practice of defect prediction data collection.* **arXiv:1911.08938** (v3). *Only ~half of SZZ-identified bug-fixing commits are actually bug-fixing; SZZ misses ~1/5; ~64% of file changes in bug-fixing commits are not part of the fix. Supports treating commit-derived labels as noisy, not ground truth.*
- **[S43] [PEER-REVIEWED]** Inozemtseva, I., Holmes, R. *Replication and invalidation in empirical software engineering research.* **MSR 2014**, DOI `10.1109/MSR.2014.22`. Cited via `[S13]` for the premise that test-suite effectiveness varies enormously across projects.

---

## Appendix — verification log for my own claims

| Claim | How verified |
|---|---|
| Current Zenodo version + license + size | `GET https://zenodo.org/api/records/10259013` → conceptrecid resolution; `GET .../20340547` → `license.id`, `publication_date`, `files[]` |
| GHALogs license | `GET https://zenodo.org/api/records/10154920` → `license.id = cc-by-sa-4.0` |
| MSR'24 §5 limitations verbatim | Downloaded `https://decan.lexpage.net/files/msr-2024d.pdf`, extracted with PyMuPDF, read §5 directly |
| GitHub ToS section text | Fetched the live ToS page; §D.6/§D.8/§D.9/§H/§J quoted from that render |
| Rate limits / search limits | Fetched the two live docs pages; all figures quoted from those renders |
| `author:app/USERNAME` qualifier exists | Fetched the live qualifier reference page |
| Source A has no outcome columns | Live Zenodo card column list (17 columns) + GHALogs §II statement — *not* from the paper's limitations |
