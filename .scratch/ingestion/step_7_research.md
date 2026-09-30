# Step 7 Research — Codemod / Recipe Catalogs as Checkable Procedures

**Deliverable:** research report only. No code written, no repo files modified besides this one.
**All sources fetched:** 2026-09-28 unless otherwise noted. Every number below was read off a page I
actually loaded in this session; anything I could not read is marked `UNVERIFIED`.

**Framing.** Product direction is "routing with a check": a Procedure is trustworthy only if it carries
a CHECKABLE OUTCOME. Codemods are attractive because each one ships its own before/after fixtures, so
the check is *executable* rather than model-asserted, at zero LLM cost. This report tests whether that
premise survives contact with the two catalogs.

---

## 1. Executive summary — the 5 things that decide the design

1. **The Node.js registry is not a registry of npm packages; it is a hosted service.** There is exactly
   one source repo, `github.com/nodejs/userland-migrations`, with **40 recipe directories**, one root
   `LICENSE` (MIT), and per-recipe `codemod.yaml` manifests that self-declare `license: MIT`. I queried
   the npm registry for 16 of the `@nodejs/*` package names and **all 16 returned 404** (control:
   `@types/node` resolved fine in the same session). So there is no published npm artifact whose
   license could differ from the repo's. The "registry" is `app.codemod.com/registry`, which claims
   **1,220 packages** and is queryable via `npx codemod search --format json`.
   *(https://github.com/nodejs/userland-migrations · https://app.codemod.com/registry · https://docs.codemod.com/platform/registry)*

2. **The catalog is 40 recipes, not the 28 the Node.js docs claim.** I machine-counted the `recipes/`
   tree: 40 directories. The Mintlify site says "28 Migration Recipes" on two separate pages. Stale
   docs are the first quality signal.
   *(https://nodejs-userland-migrations.mintlify.app/ · https://api.github.com/repos/nodejs/userland-migrations/contents/recipes)*

3. **These codemods are not jscodeshift. They are JavaScript ast-grep ("jssg") via a Rust CLI.** The
   test command in every recipe's `package.json` is `npx codemod jssg test -l typescript ./src/workflow.ts ./tests`.
   `jscodeshift` (17.4.0, MIT) is not in the dependency graph of this repo at all. This matters because
   the plan named jscodeshift.
   *(https://raw.githubusercontent.com/nodejs/userland-migrations/main/recipes/ansi-colors-to-styletext/package.json · https://docs.codemod.com/jssg/testing)*

4. **The OpenRewrite license reversal is real, and it is severe.** Of 52 modules in the official table,
   **30 are Apache-2.0 and 22 are Moderne Source Available**. The Moderne license text explicitly names
   *"Sourcegraph and Sourcegraph Batch Changes, Amazon Q Code Transformer, Broadcom Application
   Advisor"* as prohibited third parties and forbids making the functionality available to third
   parties as a service. Serving these recipes to agents over an API is exactly the prohibited use.
   **Verdict: reject all 22.**
   *(https://docs.openrewrite.org/reference/latest-versions-of-every-openrewrite-module · https://raw.githubusercontent.com/openrewrite/rewrite-spring/main/LICENSE/moderne-source-available-license.md)*

5. **The premise "the check is executable" is weaker than it looks, and the reason is epistemic, not
   engineering.** A codemod's before/after fixture is a **self-consistency** check: it proves the
   transform reproduces its own committed output. It does not prove the output is *correct*. OpenRewrite
   issue-tracking and the Node.js tracker both contain live cases where the committed `expected` output
   is itself broken — `node-url-to-whatwg-url` ships a fixture that throws `ERR_INVALID_URL` at runtime
   (nodejs/userland-migrations#249). Ingesting these as "verified" would launder a self-consistency
   check into a correctness claim. **This is the single most important finding in the report.**
   *(https://github.com/nodejs/userland-migrations/issues/249)*

**Supporting numbers:**
- Apache-2.0 OpenRewrite recipe doc pages: **≈1,150** of 4,481 total (method in §3.4). Target of ≥50 is
  met with ~23× headroom; the single largest Apache module (`rewrite-quarkus`) alone has 211.
- Node.js recipes with a real executable fixture set: **37 of 40**. Three do not: `v22-to-v24` (the
  flagship recipe) has `"test": "echo \"no test necessary\""`, `correct-ts-specifiers` uses a different
  harness, and `chalk-to-util-styletext` has 3 test dirs but only 1 input/expected pair.
- Feasibility verdict: **Node.js = executable with setup** (needs `npx` to fetch the CLI once, then
  offline). **OpenRewrite = executable with setup and credentials** (JDK 21, Gradle, Code Genome token).
  Neither is "fully executable offline" on day one.

---

## 2. Source A — Node.js userland codemod registry

### 2.1 What exactly is the current registry?

**There is no single live "userland-migrations registry" repo.** The state is:

| Thing | Status | Evidence |
|---|---|---|
| `github.com/codemod/codemod-registry` | **ARCHIVED.** README: "⚠️ This repository is deprecated and no longer maintained. Please Note: This repository was migrated to a monorepo called codemod." | https://github.com/codemod/codemod-registry (GitHub API reports `archived: true`) |
| `github.com/codemod/codemod` | **Live.** The monorepo. Rust crates + pnpm workspace, `codemod-cli` releases, 147 releases. Root `LICENSE` present. | https://github.com/codemod/codemod |
| `github.com/nodejs/userland-migrations` | **Live.** 40 recipes. Root `LICENSE` = MIT. 78 stars, 53 forks, **65 open issues**. Created 2024-11-15. | https://github.com/nodejs/userland-migrations |
| `app.codemod.com/registry` | **Live hosted registry.** Claims "1,220 packages". Contains `nodejs/util-is v1.0.2` (1,836 downloads, "7 months ago"). | https://app.codemod.com/registry |
| `nodejs-userland-migrations.mintlify.app` | **Stale docs.** Claims 28 recipes; actual count is 40. | https://nodejs-userland-migrations.mintlify.app/introduction |

**How codemods are published / named / versioned:**
- Named by **npm scope + recipe dir**: `@nodejs/<recipe-dir-name>`. Registry docs confirm scoped
  packages such as `@nodejs/create-require-from-path`.
- Published to the **Codemod Registry** (hosted), *not* npm. Access is declared in each
  `codemod.yaml` as `registry: {access: public, visibility: public}`.
- Versioned in two places that **disagree**: `recipes/v22-to-v24/codemod.yaml` says `version: 0.0.1`
  while `recipes/v22-to-v24/package.json` says `"version": "1.0.1"`. Any provenance schema must pick one
  and record the discrepancy.

**Machine-readable index?** There is no static JSON/API file to vendor. The enumeration options are:
1. `npx codemod search --format json` — documented to support `--language`, `--framework`,
   `--category`, `--scope`, `--format json|yaml`, `--limit`, `--offset`.
   (https://github.com/codemod/codemod/blob/main/docs/cli.mdx)
2. `https://app.codemod.com/registry` — HTML.
3. **The best option for us: the repo tree itself.** `git clone --depth 1` gives us 40 `codemod.yaml`
   files, each a self-describing manifest. No network dependency at ingest time, fully reproducible at
   a pinned commit.
   **UNVERIFIED:** whether `codemod search --format json` requires login. Docs say "Packages with
   `public` access are discoverable and runnable by anyone. They appear in public search results",
   which implies no login, but I did not execute it.

**Fields per `codemod.yaml` entry** (real file, `recipes/ansi-colors-to-styletext/codemod.yaml`,
522 bytes): `schema_version`, `name`, `version`, `capabilities[]` (`fs`, `child_process`),
`description`, `author`, `license`, `workflow`, `category`, `repository`, `targets.languages[]`,
`keywords[]`, `registry.access`, `registry.visibility`. This is a genuinely good provenance surface.

### 2.2 Per-codemod licensing — the critical gate

**Structural finding that resolves most of the gate: there is exactly ONE `LICENSE` file in the entire
repo** (verified: root-level `LICENSE` only; zero per-recipe `LICENSE` files across 1,779 tree
entries). It is **MIT**, 1,096 bytes, at commit `48b9b9a1d1385e7f1d2de8a8482d557447f512c0`.
Every recipe additionally self-declares `"license": "MIT"` in its `package.json` and `license: MIT` in
its `codemod.yaml`. All 40 inherit MIT from the root.

This is the *opposite* of the failure mode the plan anticipated ("many npm codemods in repos with no
LICENSE file"). For this specific catalog, that risk does not materialize.

| # | Recipe | Repo / path | LICENSE present? | SPDX | Verdict |
|---|---|---|---|---|---|
| 1 | `ansi-colors-to-styletext` | `nodejs/userland-migrations` (root) | ✅ `LICENSE` @ `48b9b9a` | `MIT` | **ALLOW** |
| 2 | `axios-to-whatwg-fetch` | same | ✅ | `MIT` | **ALLOW** |
| 3 | `buffer-atob-btoa` | same | ✅ | `MIT` | **ALLOW** |
| 4 | `chalk-to-util-styletext` | same | ✅ | `MIT` | **ALLOW** (but see §2.3 — fixture set is degenerate) |
| 5 | `colors-to-util-styletext` | same | ✅ | `MIT` | **ALLOW** |
| 6 | `correct-ts-specifiers` | same (uses `.codemodrc.json`, excluded from npm workspaces) | ✅ | `MIT` | **ALLOW** (different harness) |
| 7 | `create-require-from-path` | same | ✅ | `MIT` | **ALLOW** |
| 8 | `createCredentials-to-createSecureContext` | same | ✅ | `MIT` | **ALLOW** |
| 9 | `crypto-createcipheriv-migration` | same | ✅ | `MIT` | **ALLOW** |
| 10 | `crypto-fips-to-getFips` | same | ✅ | `MIT` | **ALLOW** |
| 11 | `crypto-rsa-pss-update` | same | ✅ | `MIT` | **ALLOW** |
| 12 | `dirent-path-to-parent-path` | same | ✅ | `MIT` | **ALLOW** |
| 13 | `dns-lookup-options-coercion` | same | ✅ | `MIT` | **ALLOW** |
| 14 | `err-invalid-callback` | same | ✅ | `MIT` | **ALLOW** |
| 15 | `fs-access-mode-constants` | same | ✅ | `MIT` | **ALLOW** (known cross-file bug, §2.5) |
| 16 | `fs-truncate-fd-deprecation` | same | ✅ | `MIT` | **ALLOW** |
| 17 | `http-classes-with-new` | same | ✅ | `MIT` | **ALLOW** |
| 18 | `http-outgoingmessage-headers` | same | ✅ | `MIT` | **ALLOW** |
| 19 | `http2-priority-signaling` | same | ✅ | `MIT` | **ALLOW** |
| 20 | `import-assertions-to-attributes` | same | ✅ | `MIT` | **ALLOW** |
| 21 | `kleur-to-util-styletext` | same | ✅ | `MIT` | **ALLOW** |
| 22 | `mocha-to-node-test-runner` | same | ✅ | `MIT` | **ALLOW** |
| 23 | `mock-module-exports` | same | ✅ | `MIT` | **ALLOW** |
| 24 | `node-url-to-whatwg-url` | same | ✅ | `MIT` | **ALLOW license / ⚠️ known-broken fixture (#249)** |
| 25 | `process-assert-to-node-assert` | same | ✅ | `MIT` | **ALLOW** |
| 26 | `process-main-module` | same | ✅ | `MIT` | **ALLOW** |
| 27 | `repl-builtin-modules` | same | ✅ | `MIT` | **ALLOW** |
| 28 | `repl-classes-with-new` | same | ✅ | `MIT` | **ALLOW** |
| 29 | `rmdir` | same | ✅ | `MIT` | **ALLOW** |
| 30 | `slow-buffer-to-buffer-alloc-unsafe-slow` | same | ✅ | `MIT` | **ALLOW** |
| 31 | `timers-deprecations` | same | ✅ | `MIT` | **ALLOW** |
| 32 | `tls-create-secure-pair-to-tls-socket` | same | ✅ | `MIT` | **ALLOW** |
| 33 | `tmpdir-to-tmpdir` | same | ✅ | `MIT` | **ALLOW** |
| 34 | `types-is-native-error` | same | ✅ | `MIT` | **ALLOW** |
| 35 | `util-extend-to-object-assign` | same | ✅ | `MIT` | **ALLOW** |
| 36 | `util-is` | same | ✅ | `MIT` | **ALLOW** (v1.0.2 in registry, 1,836 dl) |
| 37 | `util-log-to-console-log` | same | ✅ | `MIT` | **ALLOW** |
| 38 | `util-print-to-console-log` | same | ✅ | `MIT` | **ALLOW** |
| 39 | `v22-to-v24` | same | ✅ | `MIT` | **ALLOW license / ❌ REJECT on check (no tests)** |
| 40 | `zlib-bytesread-to-byteswritten` | same | ✅ | `MIT` | **ALLOW** |

**Published-npm-package license:** *not applicable — no npm package exists.* 16/16 `@nodejs/*` queries
404. The scope is essentially unclaimed on npm, so there is no second, divergent license to reconcile.
**This is a genuine simplification the plan did not anticipate.**

**The wider registry is a different risk surface.** The 1,220-package hosted registry is community
contributed, and the archived `codemod-registry` README notes contributors must sign a **CLA**
("once you create a pull request, you will be asked to sign our Contributor License Agreement").
Per-package licensing in the hosted registry is **UNVERIFIED** — I did not audit it, and the
`codemod.yaml` `license:` field is self-asserted metadata, not a detected SPDX id.
**Recommendation: restrict ingestion to `nodejs/userland-migrations` at a pinned commit, and reject
the rest of the hosted registry until a per-package license gate exists.**

### 2.3 How the before/after tests are expressed — real on-disk layout

I enumerated all 1,779 paths in the repo tree. **There are ZERO jest snapshots and ZERO
`__testfixtures__` directories.** The plan's hypothesis about the popular framework is wrong for this
catalog. There are **three distinct layouts**:

**Layout A — flat single-file pairs (dominant; 30 recipes).** One directory per test case, with
`input.<ext>` and `expected.<ext>` side by side. Real paths from `ansi-colors-to-styletext` (36 cases):

```
recipes/ansi-colors-to-styletext/tests/basic-color/input.js
recipes/ansi-colors-to-styletext/tests/basic-color/expected.js
recipes/ansi-colors-to-styletext/tests/chained-styles/input.js
recipes/ansi-colors-to-styletext/tests/chained-styles/expected.js
recipes/ansi-colors-to-styletext/tests/no-match/input.js          # sha == expected/input.js: NO-OP case
recipes/ansi-colors-to-styletext/tests/no-match/expected.js
recipes/ansi-colors-to-styletext/tests/unsupported-named-esm/input.js
recipes/ansi-colors-to-styletext/tests/unsupported-named-esm/expected.js
```

Test-case naming is itself a quality signal: `no-match`, `unsupported-api`, `unsupported-property-*`,
`shadowed-local`, `shadowed-nested`, `unshadowed-default-expression` — these are negative and
shadowing cases, which is a good sign for a deprecation codemod.

**Layout B — nested `input/` + `expected/` directory snapshots (multi-transform recipes).** Used when
one recipe runs several transforms, each with its own case set. Real paths:

```
recipes/timers-deprecations/tests/enroll/input/dep0095-basic.js
recipes/timers-deprecations/tests/enroll/expected/dep0095-basic.js
recipes/timers-deprecations/tests/unenroll/input/dep0096-destructured.js
recipes/timers-deprecations/tests/unenroll/expected/dep0096-destructured.js
recipes/timers-deprecations/tests/unref/input/unref_import-variants.js
recipes/timers-deprecations/tests/unref/expected/unref_import-variants.js

recipes/node-url-to-whatwg-url/tests/url-format/input/file-4.mjs
recipes/node-url-to-whatwg-url/tests/url-format/expected/file-4.mjs
recipes/node-url-to-whatwg-url/tests/import-process/input/file-7.js
recipes/node-url-to-whatwg-url/tests/import-process/expected/file-7.js
```

**Layout C — jscodeshift-era, colocated fixtures + `node --test` (1 recipe: `correct-ts-specifiers`).**
97 files under `src/`, no `tests/` dir at all, uses `.codemodrc.json` instead of `workflow.yaml`, and
is explicitly **excluded from the npm workspaces** (`"workspaces": ["./recipes/*", "utils",
"!./recipes/correct-ts-specifiers"]`). Real paths:

```
recipes/correct-ts-specifiers/.codemodrc.json
recipes/correct-ts-specifiers/src/fexists.test.ts
recipes/correct-ts-specifiers/src/fexists.ts
recipes/correct-ts-specifiers/src/fixtures/ambiguous.js
recipes/correct-ts-specifiers/src/fixtures/ambiguous.ts
recipes/correct-ts-specifiers/src/fixtures/d/ambiguous/index.d.cts
recipes/correct-ts-specifiers/src/fixtures/d/ambiguous/index.d.mts
recipes/correct-ts-specifiers/src/fixtures/d/ambiguous/index.d.ts
recipes/correct-ts-specifiers/src/fixtures/d/unambiguous/cts/index.d.cts
```

Its test command is a bare-Node test runner, **not** the codemod CLI:
```
node --no-warnings --experimental-import-meta-resolve --experimental-test-module-mocks \
  --experimental-test-snapshots --experimental-strip-types --import='@nodejs/codemod-utils/snapshots' \
  --test --experimental-test-coverage ... './**/*.test.ts'
```
`engines.node: ">=22.15.0"`.

**Fixture extension census** (all files under any `tests/` dir): `.js` 656, no-extension 405, `.mjs` 142,
`.cjs` 38, `.ts` 20, `.json` 10, `.tsx` 2. The 10 `.json` files are all in
`ansi-colors-to-styletext/tests/remove-dependencies/remove-ansi-colors/` (a separate `package.json`
transform).

**Runnable standalone? Partly.** Each recipe has its own `package.json` test script, and the root has
`"test": "npm run test --workspaces"`, so `npm test` at the root runs all of them. But every script
begins with `npx codemod ...`, which fetches the CLI from npm on first use. After that it is offline.
**No full framework (jest/vitest/mocha) is needed** — this is the single best property of this catalog.

**Three recipes do NOT have a usable check:**
- `v22-to-v24` — the flagship Node 24 migration recipe — has **only** `README.md`, `codemod.yaml`,
  `package.json`, `workflow.yaml`. **No `src/`, no `tests/`.** Its test script is literally
  `echo "no test necessary"`. This is the most-requested migration class in the catalog and it is
  entirely unverified.
- `chalk-to-util-styletext` — 3 test dirs but only **1** `input`/`expected` pair (nested layout).
- `correct-ts-specifiers` — has tests, but a bespoke harness, and issue #267 records it as
  "*(need to be updated to new codemod paradigm)*".

### 2.4 Tooling reality check

| Tool | Latest | License | Engines | Last modified | Notes |
|---|---|---|---|---|---|
| `codemod` (npm) | **1.18.3** | **Apache-2.0** | `>= 16.0.0` | 2026-09-11 | 243 versions. **This is the live CLI** (`npx codemod`). Rust binary (monorepo has `crates/`, `Cargo.toml`, `rust-toolchain.toml`). |
| `codemod-cli` | 3.2.0 | MIT | `10.* \|\| 12.* \|\| >= 14` | **2025-09-23** | 23 versions. **Superseded / near-abandoned.** The GitHub releases page shows `codemod-cli@1.12.3` "Latest, Jun 4 2026" — npm's `latest` tag disagrees with the GitHub release tag. Version confusion risk. |
| `@codemod/cli` | 3.3.0 | Apache-2.0 | `>=12.0.0` | **2023-02-18** | 27 versions. **ABANDONED — 3.5 years stale.** Do not use. |
| `jscodeshift` | 17.4.0 | MIT | `>=16` | 2026-07-27 | 72 versions since 2015. **Not used by this catalog.** |
| `ast-grep` (npm) | 0.1.0 | MIT | — | **2022-04-11** | 3 versions, abandoned placeholder. The real engine is bundled inside the `codemod` binary ("jssg replicates the ast-grep NAPI… built into the CLI"). |

*Source: `https://registry.npmjs.org/<pkg>` REST API, queried 2026-09-28.*

**Can a transform run on a fixture with NO network?**
- **Yes, after a one-time install.** `npm i -g codemod` (or a warm `npx` cache) then
  `codemod jssg test -l typescript ./src/workflow.ts ./tests` runs entirely locally.
- `codemod workflow run -w ./recipes/<name>/workflow.yaml` is the documented offline path and needs no
  registry login. Only `npx codemod @nodejs/<recipe>` (pulling *from* the hosted registry) needs network.
- **Security note in our favour:** jssg runs transforms in a **sandbox** with explicit capability
  grants. Default `fs` is sandboxed; `--allow-fs`, `--allow-fetch`, `--allow-child-process` escalate.
  Two recipes declare `capabilities: [fs, child_process]` and their test scripts pass
  `--allow-child-process --allow-fs --strictness cst`. This is a real containment boundary, which suits
  a substrate that executes third-party code.
  (https://docs.codemod.com/jssg/security)

**Machine-readable check output: yes.** `codemod jssg test` supports
`--output-format console | json | terse`, plus `--filter`, `--sequential`, `--update-snapshots`,
`--fail-fast`, and a 30s default per-test timeout. A `json` result payload is exactly the shape a
check-gate wants.

### 2.5 Quality problems (all read, all real)

- **❌ A committed `expected` fixture is itself broken.**
  `node-url-to-whatwg-url` — issue **#249**, opened 2025-10-27 by `styfle`: "node-url-to-whatwg-url
  codemod causes app to crash". The reporter demonstrates that
  `tests/url-parse/input/file-10.js` → `tests/url-parse/expected/file-10.js` transforms cleanly, but
  **running the expected output throws** `Uncaught TypeError: Invalid URL … ERR_INVALID_URL, input:
  '/path?query=string#hash'`. A maintainer replies that relative-URL handling is
  "not something we can replicate with `new URL`" and proposes emitting a warning instead.
  **The fixture test passes. The code is wrong.** This is the archetype of what our check does *not*
  prove. (https://github.com/nodejs/userland-migrations/issues/249)

- **⚠️ Cross-file bleed — a codemod modifying files it was not asked to touch.**
  PR **#382** `fix(fs-access-mode-constants): don't modify other thing`: "Alex (codemod) reported that
  this codemod make changes on other file." The vendor themselves found it. Note also that this
  transform touches `**/package.json` (a second file type), which is why its `workflow.yaml` has two
  nodes. (https://github.com/nodejs/userland-migrations/pull/382)

- **⚠️ Known-broken shared utility.** PR **#168** `fix(getNodeRequireCalls-utility)`: when a
  `call_expression` is wrapped in an `expression_statement` — i.e. the extremely common
  `require('module').property` shape — `getNodeRequireCalls` fails to detect it. Scope analysis bug in
  shared code means it potentially affected *many* recipes, not just one.
  (https://github.com/nodejs/userland-migrations/pull/168)

- **⚠️ Stale relative to a declared framework version.** Issue **#267** ("Codemod to help adoption of
  types stripping") tracks `@nodejs/correct-ts-specifiers` with the checkbox annotated
  "*(need to be updated to new codemod paradigm)*". Also: `.nvmrc` is `lts/*` (floating), and
  `devDependencies` pin `"typescript": "^7.0.2"` and `"@types/node": "^26.6.2"` — a repo whose test
  harness tracks a floating caret range against TypeScript 7.
  (https://github.com/nodejs/userland-migrations/issues/267)

- **⚠️ 65 open issues on a 40-recipe repo.** Ratio >1.5 open issues per recipe.

- **❌ Stale official docs.** Mintlify says 28 recipes; there are 40.

- **❌ Two npm CLI lineages, both confusing.** The live package is `codemod@1.18.3` (Apache-2.0). The
  plan's mental model of "`@codemod/cli`" resolves to a package **last published 2023-02-18**.

- **jscodeshift-specific hazards** (relevant if we ever use the jscodeshift ecosystem, not this
  catalog): `facebook/jscodeshift#567` — recast silently rewrites a JS setter `set field(num) {}` into
  `set field function(num) {}` when a parameter is added, i.e. it emits **syntactically valid,
  semantically wrong** code. `facebook/jscodeshift#513` — `recast` drops the TS `override` keyword
  from `ClassMethod` when setting `returnType` (root cause: recast's `printMethod` has no `override`
  flag). `facebook/jscodeshift#263` — ES6 block scoping broken in `renameTo`, open since 2018; root
  cause traced into `eslint-scope`/`ast-types` scope construction
  (`eslint/eslint-scope#110`, `recast#397`, `ast-types#133/#154/#944`). A practitioner note is blunt:
  "Using the builder API with jscodeshift methods does not necessarily ensure that the resulting code
  will be valid! So always test your codemod, double-check what it does."
  (https://github.com/facebook/jscodeshift/issues/567 · #513 · #263 · https://nec.is/writing/transform-your-codebase-using-codemods)

---

## 3. Source B — OpenRewrite

### 3.1 Catalog structure and enumeration options

**Two repos, 77 total (23 archived, 54 live).**
- `openrewrite/rewrite` — 32 `rewrite*` Gradle modules. Root `LICENSE` = **Apache-2.0** (11,357 bytes),
  fetched at `main` 2026-09-28. Contains `rewrite-core`, `rewrite-java`, `rewrite-maven`,
  `rewrite-gradle`, `rewrite-groovy`, `rewrite-kotlin`, `rewrite-hcl`, `rewrite-json`, `rewrite-yaml`,
  `rewrite-xml`, `rewrite-properties`, `rewrite-toml`, `rewrite-protobuf`, `rewrite-docker`,
  `rewrite-polyglot`, `rewrite-test`, `rewrite-templating`, plus per-JDK `rewrite-java-8/11/17/21/25`.
- `openrewrite/openrewrite-recipes`-style repos — **~30 separate recipe repos**, one per framework.
  NOTE: `openrewrite/openrewrite-rewrite` does **not** exist (404). The real repo is
  `openrewrite/rewrite-rewrite` = *"Migrate OpenRewrite Recipe projects. Automatically."*
  (under Moderne SA).

**Enumeration options, with honest verdicts:**

| Option | Endpoint / mechanism | Verdict |
|---|---|---|
| **Docs sitemap** | `https://docs.openrewrite.org/sitemap.xml` — I parsed it: **4,680 URLs, of which 4,481 are recipe pages**, across 99 namespaces. | ✅ **Fully offline-free, no auth, machine-readable. Best discovery surface.** |
| **`META-INF/rewrite/*.yml` in git** | Declarative recipes live at `<module>/src/main/resources/META-INF/rewrite/*.yml`. Format is `type: specs.openrewrite.org/v1beta/recipe`. | ✅ Works, but **only covers declarative recipes** — the minority. |
| **`RecipeMarketplace` / `RecipeListing` classes** | `rewrite-core` contains `RecipeMarketplace.java`, `RecipeMarketplaceReader.java`, `RecipeListing.java`, `YamlRecipeBundleReader.java`, `RecipeBundleResolver.java` (16 files under `org/openrewrite/marketplace/`). This is the modern successor to `rewrite.yml`. | ⚠️ Requires the JVM to read a jar. Not offline. |
| **Moderne REST** | Documented as `GET /api/recipes` returning a `recipes-v5.csv` with columns `ecosystem, packageName, requestedVersion, version, name, displayName, description, recipeCount, category1–6, …, options, dataTables`. Also `mod config recipes import csv`. | ❌ **I tested `https://app.moderne.io/api/recipes` unauthenticated → 404.** Requires a Moderne login. **UNVERIFIED** whether a free account suffices. |
| **Code Genome Project** | `https://artifacts.codegenomeproject.org/maven` | ❌ **"Every download from the Code Genome Project requires authentication."** Hard credential wall. |
| **Running Gradle** | `gradle rewriteRun` / `mvn rewrite:run` | ❌ Full toolchain + network. |
| **`rewrite.yml` project file** | Recipe *declaration* file, format documented at `/reference/yaml-format-reference`. | ✅ Readable, but it declares *your* recipes, it does not enumerate the catalog. |

`RecipeMarketplaceCompletenessValidator` / `RecipeMarketplaceContentValidator` existing in
`rewrite-core` is notable: OpenRewrite itself now has machinery for validating marketplace
completeness — a hook we could point at our own catalog.

### 3.2 How recipe tests express before/after — a real quoted example

There are **two** test surfaces, and only one of them is file-based.

**(a) Declarative YAML recipe declaration** — real file,
`openrewrite/rewrite-quarkus/src/main/resources/META-INF/rewrite/quarkus.yml` (via GitHub):

```yaml
---
type: specs.openrewrite.org/v1beta/recipe
name: org.openrewrite.quarkus.Quarkus1to1_13Migration
displayName: Quarkus 1.13 migration from Quarkus 1.11
description: |
  Migrates Quarkus 1.11 to 1.13.
recipeList:
  - org.openrewrite.quarkus.ConfigPropertiesToConfigMapping
  - org.openrewrite.quarkus.MultiTransformHotStreamToMultiHotStream
  - org.openrewrite.properties.ChangePropertyKey:
      oldPropertyKey: quarkus.dev.instrumentation
      newPropertyKey: quarkus.live-reload.instrumentation
  - org.openrewrite.java.ChangeMethodName:
      methodPattern: io.smallrye.mutiny.Multi collectItems()
      newMethodName: collect
  - org.openrewrite.java.ChangeMethodName:
      methodPattern: io.smallrye.mutiny.Uni subscribeOn(java.util.concurrent.Executor)
      newMethodName: runSubscriptionOn
  - org.openrewrite.maven.ChangeParentPom:
      oldGroupId: io.quarkus
      oldArtifactId: quarkus-universe-bom
      newVersion: 2.x
  - org.openrewrite.java.dependencies.UpgradeDependencyVersion:
      groupId: io.quarkus
      artifactId: quarkus-universe-bom
      newVersion: 2.x
  - org.openrewrite.java.ChangePackage:
      oldPackageName: io.vertx.core.http.HttpMethod
      newPackageName: io.quarkus.vertx.web.Route.HttpMethod
      recursive: false
```

Documented top-level keys: `type`, `name`, `displayName`, `description`, `tags`,
`estimatedEffortPerOccurrence`, `causesAnotherCycle`, `recipeList`, `preconditions`. A file may contain
any number of recipes separated by `---`.

**(b) The Java `RewriteTest` interface — where the actual before/after lives.** Real excerpt from
`openrewrite/rewrite`'s own `RewriteTest.java` and the official `Recipe testing` docs:

```java
class SayHelloRecipeTest implements RewriteTest {
    @Override public void defaults(RecipeSpec spec) { spec.recipe(new SayHelloRecipe("com.yourorg.FooBar")); }

    @Test void addsHelloToFooBar() {
        rewriteRun(
            java(  // before
                """
                package com.yourorg;
                class FooBar {
                }
                """,
                // after
                """
                package com.yourorg;
                class FooBar {
                    public String hello() {
                        return "Hello from com.yourorg.FooBar!";
                    }
                }
                """));
    }

    @Test void doesNotChangeExistingHello() {
        rewriteRun(
            java("""
                package com.yourorg;
                class FooBar { public String hello() { return ""; } }
                """));   // single arg => assert UNCHANGED
    }
}
```

The **absence of a second `java(...)` argument is the "no change expected" assertion** — the OpenRewrite
equivalent of the `no-match/input.js == no-match/expected.js` case in the Node.js catalog. Other
notable mechanics read from `RewriteTest.java`:
- Every test additionally asserts **recipe serializability** (round-trip through `RecipeSerializer`),
  **`RecipeLoader` null-instantiation**, and `validateRecipeNameAndDescription` / `validateRecipeOptions`.
- Trailing newlines are trimmed by default; `noTrim()` opts out. "This can cause issues when testing
  recipes that specifically handle end-of-file formatting."
- `afterRecipe` callbacks assert non-source-visible state (e.g. file *path* changes).
- Classpath is part of the test: `JavaParser.fromJavaVersion().classpath("spring-core")`. Without
  it you get `LST contains missing or invalid type information`.
- Declarative recipes are tested via `spec.recipeFromResources("com.yourorg.FooToBar")`, which requires
  the YAML to live in `src/main/resources/META-INF/rewrite`.

For `Quarkus1to1_13Migration` the docs page lists its example tests by name:
`Quarkus1to113MigrationTest#quarkus…`, `#changeMultiTransformAndByTakingFirst`, etc.

### 3.3 ⚡ "Can we validate without Gradle?" — the single most important practical question

**Direct answer: NO for the transformation. YES for the declaration. And that distinction is sharp
enough to build a two-tier ingestion around.**

**Tier 1 — statically parseable, fully offline, no JVM, no auth.** A declarative recipe's YAML is a
plain file with a documented schema (`specs.openrewrite.org/v1beta/recipe`). We can, with zero
dependencies: parse the file; assert the `type` const; extract `name` / `displayName` /
`description` / `tags` / `estimatedEffortPerOccurrence`; walk `recipeList` and `preconditions`
recursively; and resolve every referenced recipe id against a known-id set. That is a real, useful
check — it catches dangling references, malformed option names, missing `displayName`, recipes that
require configuration parameters and so are not directly activatable. The docs confirm the last point
is a genuine property: *"Recipes with required configuration parameters cannot be activated directly."*

**Tier 2 — requires the JVM and, for anything beyond Maven Central artifacts, credentials.** Asserting
that a recipe actually *transforms* before→after needs `RewriteTest`, which needs:
- **JDK 21** — and explicitly *"A JRE alone is insufficient since OpenRewrite uses compiler internals
  and tools only found in the JDK."* Building from source needs JDK **8 + 11 + 17 + 21 + 25**
  installed simultaneously for the `rewrite-java-*` modules.
- **Gradle 4.0+ or Maven 3.2+**, plus the `rewrite-recipe-bom` platform and `rewrite-test` +
  JUnit 5.
- **Network for dependency resolution** — and the *before* code must **compile**, which means
  resolving real third-party classpaths (e.g. `.classpath("spring-core")`). A recipe that adds
  dependencies will hit the network *at test time*, not just at build time. One maintainer commit
  exists purely to widen HTTP timeouts because *"The version-resolving tests fetch distribution checksums
  from downloads.gradle.org during the recipe run"* (commit `1b1804a` in `openrewrite/rewrite`).
- **Code Genome credentials** for the build plugins and recipe artifacts. Confirmed by the docs:
  *"The OpenRewrite build plugins and recipe artifacts are distributed through the Code Genome Project,
  which requires authentication."*
- Multi-version source sets: `src/test`, `src/testWithSpringBoot_2_7`, `src/testWithMaven`… so "one
  test" is not always one file.

**Implication for us:** an OpenRewrite Procedure's check payload can be a *recipe declaration +
resolved reference graph + the committed test method names*, all verifiable offline. It **cannot** be
a self-verifying executable assertion unless we accept JVM + Gradle + a Code Genome token as standing
infrastructure. That is a real, recurring cost we should decide about explicitly rather than discover
mid-ingest.

**Also note the composition problem.** A single recipe can be a composite of thousands. Moderne's own
marketing states *"The Spring Boot 4 upgrade is over 4,000 recipes all composed together."* If our
extraction unit is "recipe," then most of the high-value user-facing migrations are composites whose
own before/after is a whole-program property, not a fixture. The unit of extraction should probably be
the **leaf** recipe (which has a crisp, testable behavior) plus a separate composite-routing layer.

### 3.4 Per-module license table — the reversal, verified

**Method:** the authoritative table is `docs.openrewrite.org/reference/latest-versions-of-every-openrewrite-module`
(fetched 2026-09-28, versions 8.90.1 / 3.37.0). I cross-checked repo-level SPDX via the GitHub API for
all 77 org repos, and read the raw `LICENSE` and `LICENSE.md` files at `main` for three modules.

| Module (Maven coords) | Ver | License per docs | Repo SPDX (GitHub) | Commercial-use restriction? | Verdict |
|---|---|---|---|---|---|
| `org.openrewrite:rewrite-bom` | 8.90.1 | Apache-2.0 | — | No | ALLOW (BOM) |
| `org.openrewrite:rewrite-maven-plugin` | 6.46.1 | Apache-2.0 | *(no license detected)* | No | ALLOW |
| `org.openrewrite:rewrite-gradle-plugin` | 7.39.0 | Apache-2.0 | *(no license detected)* | No | ALLOW |
| `org.openrewrite.recipe:rewrite-recipe-bom` | 3.37.0 | Apache-2.0 | Apache-2.0 | No | ALLOW (BOM) |
| `org.openrewrite:rewrite-core` | 8.90.1 | **Apache-2.0** | `Apache-2.0` (raw `LICENSE`, 11,357 B) | No | **ALLOW** |
| `org.openrewrite:rewrite-docker` | 8.90.1 | Apache-2.0 | *(archived repo; `NOASSERTION`)* | No | ALLOW (module) |
| `org.openrewrite:rewrite-gradle` | 8.90.1 | Apache-2.0 | *(archived repo; none)* | No | ALLOW (module) |
| `org.openrewrite:rewrite-groovy` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-hcl` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-java` | 8.90.1 | **Apache-2.0** | Apache-2.0 | No | **ALLOW** |
| `org.openrewrite:rewrite-json` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-kotlin` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-maven` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-polyglot` | 2.11.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-properties` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-protobuf` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-templating` | 1.44.0 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-toml` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-xml` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite:rewrite-yaml` | 8.90.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite.meta:rewrite-analysis` | 2.37.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite.recipe:rewrite-all` | 1.28.1 | Apache-2.0 | Apache-2.0 | No | ALLOW |
| `org.openrewrite.recipe:rewrite-jackson` | 1.29.0 | **Apache-2.0** | Apache-2.0 | No | **ALLOW** |
| `org.openrewrite.recipe:rewrite-java-dependencies` | 1.60.2 | **Apache-2.0** | Apache-2.0 | No | **ALLOW** |
| `org.openrewrite.recipe:rewrite-liberty` | 1.26.1 | **Apache-2.0** | Apache-2.0 | No | **ALLOW** |
| `org.openrewrite.recipe:rewrite-micronaut` | 2.36.0 | **Apache-2.0** | Apache-2.0 | No | **ALLOW** |
| `org.openrewrite.recipe:rewrite-netty` | 0.11.1 | **Apache-2.0** | Apache-2.0 | No | **ALLOW** |
| `org.openrewrite.recipe:rewrite-openapi` | 0.33.1 | **Apache-2.0** | Apache-2.0 | No | **ALLOW** |
| `org.openrewrite.recipe:rewrite-quarkus` | 2.34.1 | **Apache-2.0** | Apache-2.0 | No | **ALLOW** |
| `org.openrewrite.recipe:rewrite-third-party` | 0.45.0 | **Apache-2.0** | Apache-2.0 | No | **ALLOW** |
| `org.openrewrite:rewrite-cobol` | 2.22.0 | Moderne SA | — | **YES** | **REJECT** |
| `org.openrewrite:rewrite-csharp` | 8.90.1 | Moderne SA | *(archived; `NOASSERTION`)* | **YES** | **REJECT** |
| `org.openrewrite:rewrite-javascript` | 8.90.1 | Moderne SA | *(archived; none)* | **YES** | **REJECT** |
| `org.openrewrite:rewrite-python` | 8.90.1 | Moderne SA | *(archived; none)* | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-apache` | 2.30.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-cucumber-jvm` | 2.15.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-feature-flags` | 1.23.1 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-github-actions` | 3.29.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-gitlab` | 0.24.1 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-hibernate` | 2.25.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-jenkins` | 0.37.1 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-joda` | 0.10.1 | Moderne SA | *(no license detected)* | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-logging-frameworks` | 3.32.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-micrometer` | 0.30.1 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-migrate-java` | 3.42.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-okhttp` | 0.24.1 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-prethink` | 1.2.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-rewrite` | 0.30.0 | Moderne SA | `NOASSERTION` / **"Other"** | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-spring` | 6.37.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-spring-to-quarkus` | 0.11.1 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-static-analysis` | 2.41.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |
| `org.openrewrite.recipe:rewrite-testing-frameworks` | 3.44.0 | Moderne SA | `NOASSERTION` | **YES** | **REJECT** |

**Totals: 30 Apache-2.0 rows (26 code modules + 4 BOM/plugins) · 22 Moderne Source Available.**

**The restrictions, quoted from the actual license text**
(`openrewrite/rewrite-spring@main` → `LICENSE/moderne-source-available-license.md`, 6,658 bytes — I
read the whole thing):

> You may not and will not make the functionality of the Software or a Modified version available to
> third parties **as a service** or distribute the Software or a Modified version in a manner that
> makes the functionality of the Software directly or indirectly available to third parties.

> …offering a product or service, the value of which derives to any extent from the value of the
> Software or Modified version…
>
> Examples of third parties that are prohibited under this limitation include but are not limited to:
> - **Sourcegraph and Sourcegraph Batch Changes**
> - **Amazon Q Code Transformer**
> - **Broadcom Application Advisor**

> You may not and will not alter, remove, or obscure any licensing, copyright, or other notices…

Also: **non-sublicensable, non-transferable**, Delaware law/jurisdiction, automatic termination on
violation with a 30-day cure. It is *not* OSI open source (OSI's definition excludes field-of-use
restrictions).

**Verdict against our allowlist (accepts Apache-2.0; rejects non-commercial / share-alike): REJECT all
22.** Serving a Procedure that *is* a Moderne-licensed recipe to coding agents over an API is
precisely "making the functionality available to third parties as a service," and re-publishing recipe
bodies is "distributing the Software."

**Structural gotcha for the gate:** Moderne-licensed repos do **not** carry an SPDX-detectable license at
the root. They carry a 43-byte pointer file:
```
LICENSE.md  →  "LICENSE/moderne-source-available-license.md"
```
That is why GitHub's licensee reports `NOASSERTION` / "Other". **An automated license gate that
inspects only the root `LICENSE`/`LICENSE.md` text will mis-classify these.** It must (a) follow
`LICENSE.md` pointers, and (b) treat `NOASSERTION` as a **reject**, never as a pass.

**Change in the last ~18 months: confirmed, and it is a large regression in coverage.**
- The Spring license change is dated: `spring-projects/spring-tools#1443` — *"On December 13th
  [2024], several OpenRewrite recipes have gone through a license change from Apache 2.0 to either
  Moderne Source Available License or Moderne Proprietary License. In particular, the `rewrite-spring`
  recipe package went from Apache 2.0 to Moderne Source Available License."* Spring Tools responded by
  **removing** `rewrite-spring` entirely in 5.0.0.RELEASE: *"we removed the direct upgrade support for
  Spring applications and therefore do not include rewrite-spring anymore."*
- The namespace counts show the damage quantitatively: `java/spring` = 348 doc pages and
  `java/migrate` = 486, `java/testing` = 296, `java/logging` = 133 — **1,263 recipe pages
  (28% of the catalog) sit in Moderne-licensed namespaces.**
- **A newer, stricter tier has appeared.** `docs.moderne.io` now lists
  `io.moderne.recipe:rewrite-spring 0.42.0 | **Moderne Proprietary License**` and
  `io.moderne.recipe:rewrite-devcenter 1.32.1 | Moderne Source Available License`. So there are now
  **three** tiers (Apache-2.0 / Moderne Source Available / Moderne Proprietary), and the proprietary
  tier overlaps Spring. Anything we build must key off the *module* license, not the ecosystem name.
  (https://docs.moderne.io/user-documentation/recipes/lists/latest-versions-of-every-openrewrite-module.md)
- **23 of 77 org repos are ARCHIVED** — including `rewrite-csharp`, `rewrite-javascript`,
  `rewrite-python`, `rewrite-docker`, `rewrite-gradle`, `rewrite-kotlin`, `rewrite-houston-jug`,
  `rewrite-sandbox`, `rewrite-generative-ai`, `rewrite-checkstyle`, `rewrite-java-8`,
  `rewrite-testcontainers`, `rewrite-jhipster`, `rewrite-recommendations`, `rewrite-cloud-suitability-analyzer`.
  Modules marked Apache-2.0 in the docs table can have **archived** repos, so "Apache-2.0" and
  "actively maintained" are independent axes. Gate on both.
- **Documentation/table inconsistencies found** (flag, don't resolve): (a) `rewrite-go` appears in
  `docs.moderne.io` as Moderne SA at 8.92.6 but is **absent entirely** from the
  `docs.openrewrite.org` 52-row table; (b) the docs prose says newer language modules including
  "Kotlin" are source-available, while the table lists `rewrite-kotlin` as Apache-2.0; (c) version
  drift between the two docs sites (8.88.0 vs 8.90.1 vs 8.92.6); (d) `rewrite-maven-plugin` and
  `rewrite-gradle-plugin` are Apache-2.0 per the table but have **no detectable license** in the repo.
  **The `docs.openrewrite.org` table is the most authoritative per-module license source; the raw
  repo file is the fallback and must be re-resolved at ingest time.**

### 3.5 A defensible count of Apache-2.0 recipes

**I could not produce one single authoritative total, and I will not invent one.** Here is what I
*can* defend, with method, plus the floors that are rock solid.

**Verified floors:**
- **Declarative (YAML) recipes in Apache-2.0 modules: 37.** Method: listed
  `src/main/resources/META-INF/rewrite/*.yml` per repo via the GitHub contents API.
  `rewrite-core` 1, `rewrite-java-dependencies` 1, `rewrite-quarkus` 4, `rewrite-micronaut` 5,
  `rewrite-openapi` 3, `rewrite-jackson` 6, `rewrite-liberty` 3, `rewrite-netty` 4,
  `rewrite-third-party` 4, `rewrite-dropwizard` 5, `rewrite-templating` 1. *(Not counted: these are
  composites; the real leaf-recipe count is much higher.)*
- **Total recipe documentation pages: 4,481** (parsed from `docs.openrewrite.org/sitemap.xml`, 4,680
  total URLs, 199 non-recipe, across 99 namespaces).

**Namespace → module attribution, and the Apache-2.0 total.** Recipe FQCNs encode their module
(`org.openrewrite.quarkus.*` → `rewrite-quarkus`), so I grouped the 4,481 sitemap URLs by namespace
and mapped each to the license table:

| Apache-2.0 modules (namespace → count) | Subtotal |
|---|---|
| `apache/*` (201+51+33+16+5+3+2+2), `oracle/weblogic` 125, `amazon/awssdk` 46, `codehaus/plexus` 21, `timefold/solver` 18, `java/springdoc` 11, `sh/stubborn` 8, `java/flyway` 5, `hibernate/validator` 3, `axonframework/migration` 2, `google/guava` 1, `java/camel` 2 — all `rewrite-third-party` | **~556** |
| `quarkus/updates` 211, `quarkus/quarkus2` 7, `quarkus/migrate` 6, `quarkus/search` 2 — `rewrite-quarkus` | **226** |
| `java/search` 37, `java/recipes` 40, `java/format` 16, `java/dependencies` 19, `java/jspecify` 9, `java/ai` 2 — `rewrite-core`/`rewrite-java` | **~123** |
| `java/jackson` 48 — `rewrite-jackson` | **48** |
| `java/micronaut` 38 — `rewrite-micronaut` | **38** |
| `gradle/*` 14+14+12+1+2+1 — `rewrite-gradle` | **44** |
| `maven/*` 16+7+1+1+1+1 — `rewrite-maven` | **27** |
| `xml/*` 21, `yaml/*` 4, `hcl/*` 8, `json/*` 4, `properties/search` 1, `groovy/format` 4, `docker/search` 6, `kotlin/*` 8, `openapi/swagger` 17, `java/netty` 11, `java/liberty` 6, `xml/liberty` 6, `maven/liberty` 1 | **~118** |
| **TOTAL Apache-2.0 recipe doc pages** | **≈1,150** |

**Moderne-licensed, must reject:** `java/migrate` 486, `java/spring` 348, `java/testing` 296,
`java/logging` 133, `quarkus/spring` 67, `cucumber/jvm` 24, `github/security` 21,
`featureflags/*` 30, `java/joda` 13, `micrometer/*` 2, `jenkins/*` 3, `gitlab/search` 5,
`prethink/calm` 1, `staticanalysis/*` 2, `recipes/rewrite` 2, `recipe/rewrite-static-analysis` 1,
`cobol/*` 6, `javascript/*` 13, `scala/migrate` 1, `hibernate/*` 3 → **~1,464**.

**❌ Largest namespace, license UNVERIFIED:** `picnic/errorprone` = **1,098 pages** (24% of the whole
catalog). This is `tech.picnic:error-prone-support`, a **third-party** module referenced from
`rewrite-migrate-java`'s `build.gradle.kts` as
`runtimeOnly("tech.picnic.error-prone-support:error-prone-contrib:latest.release:recipes")`. It is not
an OpenRewrite repo, its license was not verified (GitHub API returned 403 on the request), and it is
**not covered by the 52-row table**. **Must be rejected by default.**

**Verdict on the ≥50 target: MET, with large margin.** Two independent single-module floors already
exceed it: `rewrite-quarkus` alone = **211**, `rewrite-third-party` (`org.openrewrite.apache.camel`)
alone = **201**. The ≈1,150 total is a documentation-page count, which is a good proxy for leaf recipes
but not identical to them — I flag that rather than assert equivalence. **Exact leaf-recipe count:
UNVERIFIED** (see §6 for the cheap way to settle it).

### 3.6 Execution reality

| Requirement | Value | Source |
|---|---|---|
| JDK | **21** for authoring; **JRE explicitly insufficient** (compiler internals); **8+11+17+21+25 all installed** to build from source | `/authoring-recipes/recipe-development-environment`, `/reference/building-openrewrite-from-source` |
| Build tool | Gradle 4.0+ or Maven 3.2+; `rewrite-recipe-bom` platform; `rewrite-test` + JUnit 5 | same |
| Network | **Required.** Dependency resolution; plus recipe-under-test classpath resolution (`.classpath("spring-core")`); plus *recipe-time* HTTP for version-resolving recipes (commit `1b1804a` documents 1s-connect-timeout failures from `downloads.gradle.org`) | commit `1b1804a` |
| Credentials | **Code Genome Project token required** for build plugins and recipe artifacts. Source-available modules are additionally withheld from `mod config recipes jar install` "because the Code Genome Project serves them to Moderne customers only" | `/reference/latest-versions-of-every-openrewrite-module` |
| Time per recipe | **UNVERIFIED — I did not run one.** The closest citable datapoint is the closest prior work: `glebmish/rewrite-claude-assisted` reports **16.7 minutes average, $5.42 average cost per full 6-phase run** including Gradle-based recipe execution in isolated environments. A single `RewriteTest` is much cheaper than that, but a full Gradle configuration + JUnit run with classpath resolution is plausibly **tens of seconds to a few minutes** per recipe, dominated by cold Gradle configuration and dependency download. Do not treat any of these as measured. | https://github.com/glebmish/rewrite-claude-assisted |
| **$2/day + CI-offline verdict** | **❌ NOT COMPATIBLE as a steady-state per-recipe check.** JDK 21 (a JRE will not do) + Gradle + network dependency resolution + a paid/authenticated artifact repository is a standing infrastructure dependency, and it cannot be made hermetic because recipe tests must resolve real third-party classpaths. The two workarounds are (a) accept it as an occasional, credentialed, networked verification run outside the cheap tier, or (b) restrict OpenRewrite Procedures to the **Tier-1 static check** (declaration + reference graph) and never claim an executable assertion. | — |

---

## 4. Can these checks actually be executed here? — blunt verdict

| | Node.js userland | OpenRewrite |
|---|---|---|
| **Verdict** | **✅ Executable with setup** | **⚠️ Executable with setup + credentials** (Tier 1 only ⇒ static-only) |
| Runtime deps | `codemod` npm CLI (Apache-2.0, Rust binary). `node >= 16` per `engines`; recipes use `--experimental-strip-types`, so realistically **Node ≥ 22.15** | **JDK 21 (JDK, not JRE)**, Gradle 4.0+/Maven 3.2+, `rewrite-recipe-bom`, `rewrite-test`, JUnit 5 |
| Network at first setup | **Yes, once** — `npm i -g codemod` (or warm `npx`) | **Yes, always.** Classpath resolution at test time; Code Genome auth for build plugins |
| Network after setup | **No.** `codemod jssg test -l typescript ./src/workflow.ts ./tests` and `codemod workflow run -w ./recipes/<n>/workflow.yaml` are fully local | **No.** Recipe execution needs resolved classpaths; several recipes fetch at run time |
| Registry needed at run time? | **No**, for local paths. **Yes**, only for `npx codemod @nodejs/<recipe>` (hosted pull) | n/a — we clone the repos |
| Credentials | None for local test execution | **Code Genome token required** for plugin + recipe artifacts |
| Machine-readable result | **Yes** — `--output-format json` | JUnit XML / Gradle test reports (parseable, but only Tier 2) |
| Time per check | **UNVERIFIED** (did not execute). Fixture sets are 1–36 cases; ast-grep is documented as "extremely fast"; 30s default per-test timeout → plausibly **seconds per recipe** | **UNVERIFIED** (did not execute). Dominated by Gradle configuration; see §3.6 |
| $2/day + CI-offline fit | **✅ Yes**, comfortably. Setup is one npm install; marginal cost ≈ 0 | **❌ No** for Tier 2. **✅ Yes** for Tier 1 static parsing |
| **Hard ceiling** | **37 of 40 recipes have a real fixture set.** `v22-to-v24` has none. | **The check proves self-consistency, not correctness** — and the catalog contains committed *broken* expected outputs. |

**The honest summary.** We *can* execute the Node.js checks cheaply and hermetically, and we *can*
statically parse the OpenRewrite recipe graph. What we **cannot** do — with either catalog — is claim
that a passing check means the migration is *correct*. The best available correctness signal is
independent: whether the `expected` output is itself a well-formed, runnable program. That is a
strictly stronger check than fixture self-consistency, it is cheap, and for the Node.js catalog it
would have **caught #249** (`node-url-to-whatwg-url`'s crashing `expected` output). I recommend making
it a first-class second gate.

---

## 5. Recommended ingestion design

### 5.1 Extraction unit

| Catalog | Unit | Rationale |
|---|---|---|
| **Node.js** | **One `recipes/<name>/` directory = one Procedure.** Sub-split multi-transform recipes (`timers-deprecations`, `node-url-to-whatwg-url`, `ansi-colors-to-styletext`) into one Procedure per `src/*.ts` transform, since each has its own fixture directory. | The directory is the natural, self-describing unit: it has exactly one `codemod.yaml`, one `package.json`, one `LICENSE` inheritance chain, and one test command. |
| **OpenRewrite** | **One leaf recipe = one Procedure**, keyed by fully-qualified recipe id (e.g. `org.openrewrite.quarkus.ChangeMethodName`). Composites become a **separate routing artifact**, not Procedures. | Leaf recipes have crisp, testable behavior. A composite like the "Spring Boot 4 upgrade" is >4,000 recipes and its before/after is a whole-program property with no fixture. |

### 5.2 Provenance fields (what actually exists)

**Node.js — rich, use all of it:**
`repo_url`, `commit_sha` (I used `48b9b9a1d1385e7f1d2de8a8482d557447f512c0`; pin this),
`path_in_repo`, `recipe_name` (`@nodejs/<dir>`), `version_codemod_yaml`, `version_package_json`
(**keep both — they disagree**), `declared_license` (`MIT`, from `codemod.yaml` + `package.json`),
`detected_spdx` (`MIT`, from the single root `LICENSE` at the pinned commit), `author`,
`schema_version`, `category`, `targets_languages[]`, `keywords[]`, `capabilities[]` (`fs`,
`child_process` — the sandbox-escalation surface), `node_engine`, `transform_entrypoint` (`src/*.ts`),
`test_command`, `fixture_count`, `fixture_layout` (`flat-pair` | `dir-snapshot` | `node-test`).

**OpenRewrite — thinner, and the gaps must be explicit:**
`repo_url`, `commit_sha`, `maven_group_id`, `maven_artifact_id`, `maven_version`,
`recipe_id` (FQCN), `display_name`, `description`, `tags[]`, `estimated_effort_per_occurrence`,
`module_license_source` (which artifact the license verdict came from — docs table vs raw file),
`module_spdx` (`Apache-2.0` or `NOASSERTION`), `repo_archived` (bool), `source_availability_tier`
(`apache2` | `moderne-sa` | `moderne-proprietary` | `unverified`), `doc_url`,
`recipe_kind` (`declarative-yaml` | `imperative-java` | `refaster-template`),
`recipe_list` / `preconditions` (resolved ids), `requires_config` (bool), `test_class`,
`test_methods[]`, `check_tier` (`static` | `executable`).
**Missing and must be marked `UNVERIFIED`:** exact leaf-recipe count; whether the specific `expected`
output is correct; third-party module licenses (e.g. `tech.picnic`).

### 5.3 Check payload

**Node.js (Tier: executable, offline, ~free).** Two gates, both required:
1. **Gate A — fixture self-consistency.** `codemod jssg test -l <lang> ./src/<transform>.ts ./tests
   --output-format json`. Proves the transform reproduces its committed output. Parse the JSON; require
   **0 failures** across **N ≥ 1** cases, and require at least one *negative* case
   (`input == expected`, i.e. a no-op fixture) to exist — otherwise the check is vacuous.
2. **Gate B — output well-formedness.** Parse each `expected.*` file with the matching parser
   (ast-grep/tsc for `.ts`/`.tsx`/`.js`/`.mjs`/`.cjs`; a real JSON parser for `.json`). Require **0
   syntax errors**. Cheap, hermetic, and independently catches the `#249` class. Note explicitly: this
   proves *well-formedness*, not *runtime* semantics — for the Node.js builtin-API recipes, runtime
   semantics genuinely require executing against a Node version, which we should not do.

**OpenRewrite (Tier 1: static, offline, ~free).**
Parse the declarative YAML; assert the `type` const; require `name`, `displayName`, `description`;
walk `recipeList` + `preconditions`; resolve every referenced recipe id against the Apache-2.0 module
id set; require the module to be Apache-2.0 **and** its repo non-archived; record `test_methods[]` from
the `docs.openrewrite.org` page as *evidence that a test exists* (NOT as an executed assertion).
**Never** set the Procedure's check status to "executed" on the basis of a Tier-1 pass.

### 5.4 What the license gate must reject, with counts

| Reject rule | Rejects | Count |
|---|---|---|
| `NOASSERTION` / missing / unrecognized root license | Moderne-SA repos (pointer-file indirection defeats naive detection) | **22 OpenRewrite modules** |
| "May not be commercialized / may not be made available to third parties as a service" | All 22 Moderne SA modules | **22** |
| Moderne Proprietary tier | `io.moderne.recipe:rewrite-spring` 0.42.0, `rewrite-devcenter`, etc. | **~15+ `io.moderne.*` modules** (UNVERIFIED count) |
| Third-party module, license unverified | `tech.picnic:error-prone-support` (`picnic/errorprone`, **1,098 doc pages**) | **1 module, ~1,098 pages** |
| Archived upstream repo | 23 of 77 `openrewrite` org repos | **23** |
| No executable check | `nodejs/v22-to-v24` (`"test": "echo \"no test necessary\""`), `chalk-to-util-styletext` (1 fixture), stale `correct-ts-specifiers` | **3 of 40** |
| Self-asserted-only license (no detected file) | the whole 1,220-package hosted Codemod Registry outside `nodejs/userland-migrations` | **~1,180 packages** |
| Abandoned package | `@codemod/cli` (2023-02-18), `ast-grep@0.1.0` (2022-04-11) | **2** |
| **Net ingestible now** | **37 Node.js recipes** (executable) + **≈1,150 OpenRewrite Apache-2.0 recipe doc pages** (static-only) | — |

### 5.5 The gate we must add, which the plan does not mention

**Do not label a codemod Procedure "verified" on the strength of its own fixture.** The fixture is
maintained by the same party as the transform, and it is demonstrably capable of encoding a bug
(`#249`). Two cheap mitigations, both recommended:
1. **Gate B** (output well-formedness) as above — catches the observed failure mode.
2. Record `check_semantics: "self-consistency"` in the evidence, and never surface
   "verified correct migration" language. The Procedure's claim is bounded to: *"this transform
   deterministically maps these N input shapes to these N output shapes."* That is a genuinely useful,
   genuinely checkable claim — and it is honest.

---

## 6. Open questions we could settle cheaply ourselves

Ordered by value per unit of effort. Items 1–4 need no external network beyond what we already use.

1. **Does the Node.js fixture set run green today, at a pinned commit?** ~15 min. `git clone` +
   `npm i -g codemod@1.18.3` + `npm test` at the root. **This converts "40 recipes exist" into "N
   recipes actually pass", which is the number that matters.** Highest value per minute in this report.
2. **The exact Apache-2.0 leaf-recipe count.** ~30 min. Clone the ~11 Apache-2.0 recipe repos, count
   `src/main/java/**/*Test.java` files and `META-INF/rewrite/*.yml` recipes, and count *referenced*
   recipe ids. Or: crawl all 4,481 `docs.openrewrite.org/recipes/*` pages and count the per-page license
   banner — that yields a per-recipe license verdict directly, at ~4,500 fetches. I would do the crawl:
   it produces a **per-recipe** license map, not a per-module one, which is what our gate actually needs.
3. **What is `tech.picnic:error-prone-support`'s license?** ~2 min, one GitHub API call (mine returned
   403 on rate limit). If Apache-2.0, ~1,098 recipes become ingestable; if not, they are permanently out.
4. **Does `npx codemod search --format json` work without login?** ~2 min. Determines whether the hosted
   registry is enumerable by us at all, and whether a 1,220-package ingest is even on the table.
5. **Per-package license audit of the hosted registry.** Only if 4 is positive. Sample ~30 packages,
   resolve each `codemod.yaml` `repository` → clone → detect SPDX. Expect a high `NOASSERTION` rate
   (community contributions, CLA'd).
6. **Does the `#249` breakage pattern repeat?** ~10 min. Script: for every `expected.*` in the repo,
   parse it; separately, grep the GitHub issues for "crash"/"broken"/"does not work" per recipe. If the
   rate is high, Gate B needs promoting from "recommended" to "the primary check".
7. **Is `rewrite-maven-plugin` / `rewrite-gradle-plugin` really Apache-2.0?** ~5 min. The docs table says
   Apache-2.0; GitHub detects no license in the repo. Read the raw `LICENSE`/headers directly. A
   discrepancy here would weaken the whole "trust the docs table" rule.
8. **Time-to-run one OpenRewrite `RewriteTest`, measured.** ~1–2 h, needs a Code Genome token. Until
   measured, the "$2/day" verdict in §3.6 is an inference from the 16.7-min figure in prior work, not a
   measurement. Flag it as such in any band-closure document.
9. **`rewrite-go` and `rewrite-kotlin` license discrepancy.** ~5 min. `rewrite-go` is missing from the
   52-row table entirely; `rewrite-kotlin` is Apache-2.0 in the table but the prose calls Kotlin
   source-available. Per our hard rule 3 (spec/schema are frozen, discrepancies become board notes),
   this is a **board note, not an edit** — but resolve it before either module enters the allowlist.

---

## 7. Bibliography

All fetched **2026-09-28**.

| # | URL | What it actually establishes |
|---|---|---|
| 1 | https://github.com/nodejs/userland-migrations | The live Node.js codemod repo: 40 recipes, root `LICENSE` MIT, 78★/53 forks/**65 open issues**, created 2024-11-15. Its README points at the hosted registry, not a local index. |
| 2 | https://api.github.com/repos/nodejs/userland-migrations/git/trees/main?recursive=1 | Authoritative full file tree (1,779 entries). Source of the 40-recipe count, the zero-snapshot / zero-`__testfixtures__` finding, the three fixture layouts, the single root LICENSE, and the degenerate recipes. |
| 3 | https://raw.githubusercontent.com/nodejs/userland-migrations/main/recipes/ansi-colors-to-styletext/package.json | The real test command: `npx codemod jssg test -l typescript ./src/workflow.ts ./tests`. Proves ast-grep/jssg, not jscodeshift. Also `"license": "MIT"`, `"@codemod.com/jssg-types": "^1.6.3"`, dep on `@nodejs/codemod-utils`. |
| 4 | https://raw.githubusercontent.com/nodejs/userland-migrations/main/recipes/ansi-colors-to-styletext/codemod.yaml | The per-recipe manifest schema: `schema_version`, `name`, `version`, `capabilities`, `description`, `author`, `license`, `workflow`, `category`, `repository`, `targets.languages`, `keywords`, `registry.access/visibility`. |
| 5 | https://raw.githubusercontent.com/nodejs/userland-migrations/main/recipes/ansi-colors-to-styletext/workflow.yaml | The transform's include/exclude globs and per-step `capabilities` (`child_process`, `fs`). |
| 6 | https://raw.githubusercontent.com/nodejs/userland-migrations/main/recipes/v22-to-v24/package.json | `"test": "echo \"no test necessary\""` and version `1.0.1`. The flagship recipe has no executable check. |
| 7 | https://raw.githubusercontent.com/nodejs/userland-migrations/main/recipes/correct-ts-specifiers/package.json | The `node --test` + `--experimental-test-snapshots` harness, `engines.node >=22.15.0`, and exclusion from npm workspaces. |
| 8 | https://raw.githubusercontent.com/nodejs/userland-migrations/main/package.json | Root workspace config: `"./recipes/*"` with `"!./recipes/correct-ts-specifiers"`; `typescript ^7.0.2`; `test: npm run test --workspaces`. |
| 9 | https://github.com/codemod/codemod-registry | **Archived.** "⚠️ This repository is deprecated and no longer maintained… migrated to a monorepo called codemod." Plus the CLA requirement for contributors. |
| 10 | https://github.com/codemod/codemod | The live monorepo (Rust `crates/`, pnpm workspace, 147 releases). Root `LICENSE` present. |
| 11 | https://github.com/codemod/codemod/blob/main/docs/cli.mdx | `codemod search` supports `--language`, `--framework`, `--category`, `--scope`, `--format json|yaml`, `--limit`, `--offset`. The documented enumeration path. |
| 12 | https://app.codemod.com/registry | "1,220 packages"; `nodejs/util-is v1.0.2`, 1,836 downloads, "7 months ago". |
| 13 | https://docs.codemod.com/platform/registry | Scoped packages (`@nodejs/create-require-from-path`), `public`/`private` access, `npx codemod search`. |
| 14 | https://docs.codemod.com/jssg/testing | `npx codemod jssg test -l <lang> <file> [testdir]`; test dir must be the *parent* of the case dirs; `--filter`; `-u`; `--strictness`; snapshot semantics for `input.*`/`expected.*` **and** `input/`+`expected/`. |
| 15 | https://docs.codemod.com/cli | `--output-format console\|json\|terse`, 30s default test timeout, `--sequential`, `--fail-fast`, `--update-snapshots`, `--strictness loose`. The machine-readable check payload. |
| 16 | https://docs.codemod.com/jssg/security | The capability sandbox: default sandboxed `fs`; `--allow-fs` / `--allow-fetch` / `--allow-child-process` escalate. Evidence of a real containment boundary. |
| 17 | https://github.com/nodejs/userland-migrations/issues/249 | **The key negative finding.** `node-url-to-whatwg-url` ships an `expected` fixture that throws `ERR_INVALID_URL` at runtime. Fixture passes, code broken. |
| 18 | https://github.com/nodejs/userland-migrations/pull/382 | `fs-access-mode-constants` "make changes on other file" — cross-file bleed, reported by the vendor. |
| 19 | https://github.com/nodejs/userland-migrations/pull/168 | `getNodeRequireCalls` fails on `require('module').property` (expression_statement wrapping). Shared-utility scope bug. |
| 20 | https://github.com/nodejs/userland-migrations/issues/267 | `correct-ts-specifiers` annotated "*(need to be updated to new codemod paradigm)*". Staleness admitted. |
| 21 | https://nodejs-userland-migrations.mintlify.app/introduction | Official docs claiming "28 migration recipes" — **stale**, actual count is 40. |
| 22 | https://registry.npmjs.org/jscodeshift · /codemod · /codemod-cli · /@codemod%2fcli · /ast-grep | Versions, licenses, engines, and last-modified dates. Establishes `codemod@1.18.3` (Apache-2.0) as live, `@codemod/cli` (3.3.0) abandoned since 2023-02-18, `ast-grep@0.1.0` abandoned since 2022-04-11, `jscodeshift@17.4.0` (MIT, node ≥16). |
| 23 | https://docs.openrewrite.org/reference/latest-versions-of-every-openrewrite-module | **The authoritative per-module license table.** 52 rows: 30 Apache-2.0, 22 Moderne SA, at versions 8.90.1 / 3.37.0. Also: Code Genome requires auth; source-available modules excluded from `jar install`; the `/api/recipes` CSV columns. |
| 24 | https://raw.githubusercontent.com/openrewrite/rewrite-spring/main/LICENSE/moderne-source-available-license.md | **The actual Moderne license text (6,658 B).** "may not … make the functionality … available to third parties as a service"; names Sourcegraph / Amazon Q Code Transformer / Broadcom Application Advisor as prohibited. Non-sublicensable, non-transferable, Delaware. The basis for the REJECT verdict. |
| 25 | https://raw.githubusercontent.com/openrewrite/rewrite-spring/main/LICENSE.md | The 43-byte pointer file `LICENSE/moderne-source-available-license.md`. Why GitHub reports `NOASSERTION`, and why a naive root-license gate fails. |
| 26 | https://raw.githubusercontent.com/openrewrite/rewrite/main/LICENSE | Apache-2.0 full text (11,357 B) at `openrewrite/rewrite@main`. |
| 27 | https://raw.githubusercontent.com/openrewrite/rewrite-quarkus/main/README.md · https://github.com/openrewrite/rewrite-rewrite/blob/main/README.md | Confirms `rewrite-rewrite` = "Migrate OpenRewrite Recipe projects. Automatically." and that `openrewrite/openrewrite-rewrite` does not exist. |
| 28 | https://api.github.com/orgs/openrewrite/repos | 77 repos: 54 live, **23 archived**. Repo-level SPDX per repo. The archived list (incl. `rewrite-csharp`, `rewrite-javascript`, `rewrite-python`, `rewrite-kotlin`, `rewrite-generative-ai`, `rewrite-testcontainers`). |
| 29 | https://raw.githubusercontent.com/openrewrite/rewrite-rewrite/main/LICENSE · https://raw.githubusercontent.com/openrewrite/rewrite-rewrite/main/LICENSE.md | 404 at root, but `LICENSE.md` exists → GitHub `NOASSERTION`/"Other". Confirms pointer-file indiction. |
| 30 | https://github.com/openrewrite/rewrite/blob/main/rewrite-test/src/main/java/org/openrewrite/test/RewriteTest.java | The actual test harness source: `defaults()`/`rewriteRun()`, serialization round-trip assertion, `RecipeLoader` null-instantiation assertion, `noTrim`, `afterRecipe`, parser grouping, expected-cycle derivation. |
| 31 | https://docs.openrewrite.org/authoring-recipes/recipe-testing | The `RewriteTest` contract: one-arg `java(...)` = assert unchanged; two-arg = before/after; `recipeFromResources` for declarative recipes; classpath-as-test-input; `noTrim` rationale. |
| 32 | https://docs.openrewrite.org/authoring-recipes/writing-a-java-refactoring-recipe | The canonical `SayHelloRecipeTest` before/after example, and "recipes that don't do anything yet" (test-first). |
| 33 | https://github.com/openrewrite/rewrite-quarkus/blob/main/src/main/resources/META-INF/rewrite/quarkus.yml | A real declarative recipe file: `type: specs.openrewrite.org/v1beta/recipe`, `org.openrewrite.quarkus.Quarkus1to1_13Migration`, 20+ `recipeList` entries. |
| 34 | https://docs.openrewrite.org/reference/yaml-format-reference | The full YAML key schema (`type`, `name`, `displayName`, `description`, `tags`, `estimatedEffortPerOccurrence`, `causesAnotherCycle`, `recipeList`, `preconditions`), the `---` multi-doc rule, and the named precondition recipes. |
| 35 | https://docs.openrewrite.org/recipes/quarkus/quarkus1to1_13migration.md | The rendered recipe page, including its `Example 1..4` test method names (`Quarkus1to113MigrationTest#…`). |
| 36 | https://docs.openrewrite.org/licensing/openrewrite-licensing | The two stated limitations of Moderne SA; the "concert ticket" non-resale framing; the third (Proprietary) tier and its five groups. |
| 37 | https://github.com/spring-projects/spring-tools/issues/1443 | **Dates the reversal**: "On December 13th [2024]… several OpenRewrite recipes… from Apache 2.0 to either Moderne Source Available or Moderne Proprietary"; and Spring Tools removing `rewrite-spring` in 5.0.0.RELEASE. |
| 38 | https://docs.moderne.io/user-documentation/recipes/lists/latest-versions-of-every-openrewrite-module.md | The newer, stricter picture: `io.moderne.recipe:rewrite-spring 0.42.0 | Moderne Proprietary License`; `rewrite-go` present (absent from the openrewrite table); version drift 8.92.6 vs 8.90.1. |
| 39 | https://docs.openrewrite.org/sitemap.xml | Parsed: 4,680 URLs, **4,481 recipe pages**, 99 namespaces. Basis of every count in §3.5, incl. `picnic/errorprone` = 1,098. |
| 40 | https://docs.openrewrite.org/authoring-recipes/recipe-development-environment | **JDK 21; "A JRE alone is insufficient since OpenRewrite uses compiler internals"**; Gradle 4.0+/Maven 3.2+; IntelliJ 2024.1+; the `rewrite-recipe-bom` + `rewrite-test` dependency set. |
| 41 | https://docs.openrewrite.org/reference/building-openrewrite-from-source | Building from source needs **JDK 8, 11, 17, 21, 25 installed simultaneously** for the `rewrite-java-*` modules. |
| 42 | https://github.com/openrewrite/rewrite/commit/1b1804a5af7692612398fcce034a846b48b5b8cf | Recipe tests fetch from `downloads.gradle.org` **at recipe run time**; 1s connect timeout causes `SocketTimeoutException`. Direct evidence that OpenRewrite checks are not hermetic. |
| 43 | https://docs.openrewrite.org/reference/gradle-plugin-configuration | `rewriteRun` / `rewriteDryRun`; `failOnDryRunResults` as a **CI gate**. The closest thing to a natively-supported check-gate, and it requires the plugin + credentials. |
| 44 | https://github.com/glebmish/rewrite-claude-assisted | **The closest prior work to this project.** RAG over 1,000+ OpenRewrite recipe embeddings (pgvector, all-MiniLM-L6-v2), **multi-query RRF**, empirical precision/recall validation, Gradle-based execution. Reports **0.85 F1, $5.42/run, 16.7 min/run**. Also uses RRF — same composition rule as our retrieval. Its stated limits: JVM-only, Gradle-only, composition-only. |
| 45 | https://docs.moderne.io/user-documentation/agent-tools/mcp/overview | **The incumbent design, and a warning.** Moderne's MCP gives agents *tools* (`edit_code`, `learn_recipe`, `run_recipe`, `analyze_code`, `query_datatable`) over a pre-built LST — the agent calls a recipe, it does not read a catalog as memorized knowledge. |
| 46 | https://moderne.ai/recipes | Marketing claim: "10,000+ deterministic, type-aware recipes… across 340+ frameworks"; "The Spring Boot 4 upgrade is over 4,000 recipes all composed together." **Treat as a claim, not a measurement** — it counts proprietary and third-party recipes too. |
| 47 | https://mcp.directory/servers/jakarta-migration | Community MCP server (by `adrianmikula`) exposing OpenRewrite recipes as agent tools for JavaEE→Jakarta. Second instance of the "recipes as agent tools" pattern. |
| 48 | https://github.com/egn88/refactor-mcp | MCP server that **generates OpenRewrite recipe YAML dynamically** and executes it via the Maven/Gradle plugin. Confirms declarative YAML is the agent-friendly surface. |
| 49 | https://github.com/facebook/jscodeshift/issues/567 · /513 · /263 | jscodeshift/recast emitting **syntactically valid but semantically wrong** output: setter rewritten to `function(...)`; TS `override` silently dropped; ES6 block scoping broken in `renameTo` since 2018 (traced to `eslint-scope`/`ast-types`). |
| 50 | https://nec.is/writing/transform-your-codebase-using-codemods | Practitioner warning: "Using the builder API with jscodeshift methods does not necessarily ensure that the resulting code will be valid! So always test your codemod, double-check what it does." |
| 51 | https://github.com/addozhang/spring-rest-to-mcp | An OpenRewrite recipe collection that converts Spring REST controllers into MCP tools — the codemod-recipes→agent-tools loop, closing. |
| 52 | https://github.com/brownrl/recipes_mcp | A plain SQLite/FTS5 "recipe knowledge base" MCP server, no codemod execution. The *low* end of the design space: a Procedure store with no executable check — useful contrast. |
