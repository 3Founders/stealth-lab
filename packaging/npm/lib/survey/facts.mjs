// Deterministic facts: what the manifests, version pins, CI files and build configs state, each cited to
// the exact `path:line` it comes from. No LLM, so no hallucination; the agent adds the rest (purpose,
// features, decisions, issues) on top, per unit, from the worklist.
//
// A fact: {unit, topic, statement, source, key}. `key` is stable across runs ("script:test") so a
// re-survey updates the same line (same id) instead of adding a new one.
import path from "node:path";
import { GENERATED_FILE_RE } from "./units.mjs";
import {
  ciCommands, findLine, jsonKeyLine, lineAt, makeTargets, parseJsonLoose, parseToml, propertiesGet, tomlGet, xmlTag, yamlScalar,
} from "./parse.mjs";

const posixDir = (p) => { const d = path.posix.dirname(p); return d === "." ? "." : d; };
const at = (dir, base) => (dir === "." ? base : `${dir}/${base}`);

const LANG_BY_EXT = {
  py: "Python", pyi: "Python", ts: "TypeScript", tsx: "TypeScript", mts: "TypeScript", cts: "TypeScript",
  js: "JavaScript", jsx: "JavaScript", mjs: "JavaScript", cjs: "JavaScript", go: "Go", rs: "Rust", java: "Java",
  kt: "Kotlin", kts: "Kotlin", scala: "Scala", cs: "C#", fs: "F#", vb: "Visual Basic", c: "C", h: "C/C++ header",
  cc: "C++", cpp: "C++", cxx: "C++", hpp: "C++", hh: "C++", m: "Objective-C", mm: "Objective-C++", swift: "Swift",
  rb: "Ruby", php: "PHP", ex: "Elixir", exs: "Elixir", erl: "Erlang", dart: "Dart", lua: "Lua", r: "R", R: "R",
  jl: "Julia", hs: "Haskell", ml: "OCaml", clj: "Clojure", sh: "Shell", bash: "Shell", zsh: "Shell", ps1: "PowerShell",
  vue: "Vue", svelte: "Svelte", sql: "SQL", tf: "Terraform", hcl: "HCL", nix: "Nix", zig: "Zig", sol: "Solidity",
  ipynb: "Jupyter notebook", proto: "Protocol Buffers", groovy: "Groovy", pl: "Perl", elm: "Elm", nim: "Nim",
  ino: "Arduino", v: "Verilog", sv: "SystemVerilog", vhd: "VHDL", cu: "CUDA", f90: "Fortran", mdx: "MDX",
};

// Frameworks and tools people search for, by ecosystem: [manifest dep name, display name, topic].
const NPM_NOTABLE = [
  ["next", "Next.js", "stack"], ["react", "React", "stack"], ["vue", "Vue", "stack"], ["nuxt", "Nuxt", "stack"],
  ["svelte", "Svelte", "stack"], ["@sveltejs/kit", "SvelteKit", "stack"], ["@angular/core", "Angular", "stack"],
  ["solid-js", "Solid", "stack"], ["astro", "Astro", "stack"], ["@remix-run/react", "Remix", "stack"],
  ["express", "Express", "stack"], ["fastify", "Fastify", "stack"], ["@nestjs/core", "NestJS", "stack"],
  ["hono", "Hono", "stack"], ["koa", "Koa", "stack"], ["electron", "Electron", "stack"], ["react-native", "React Native", "stack"],
  ["expo", "Expo", "stack"], ["typescript", "TypeScript", "stack"], ["prisma", "Prisma", "deps"], ["@prisma/client", "Prisma Client", "deps"],
  ["drizzle-orm", "Drizzle ORM", "deps"], ["typeorm", "TypeORM", "deps"], ["mongoose", "Mongoose", "deps"],
  ["tailwindcss", "Tailwind CSS", "deps"], ["graphql", "GraphQL", "deps"], ["@trpc/server", "tRPC", "deps"], ["zod", "Zod", "deps"],
  ["vitest", "Vitest", "test"], ["jest", "Jest", "test"], ["mocha", "Mocha", "test"], ["@playwright/test", "Playwright", "test"],
  ["cypress", "Cypress", "test"], ["ava", "AVA", "test"], ["eslint", "ESLint", "lint"], ["@biomejs/biome", "Biome", "lint"],
  ["prettier", "Prettier", "lint"], ["vite", "Vite", "build"], ["webpack", "webpack", "build"], ["esbuild", "esbuild", "build"],
  ["rollup", "Rollup", "build"], ["tsup", "tsup", "build"], ["turbo", "Turborepo", "build"], ["nx", "Nx", "build"],
  ["lerna", "Lerna", "build"], ["@changesets/cli", "Changesets", "conventions"], ["husky", "husky", "conventions"],
];
const PY_NOTABLE = [
  ["django", "Django", "stack"], ["flask", "Flask", "stack"], ["fastapi", "FastAPI", "stack"], ["starlette", "Starlette", "stack"],
  ["aiohttp", "aiohttp", "stack"], ["tornado", "Tornado", "stack"], ["streamlit", "Streamlit", "stack"],
  ["sqlalchemy", "SQLAlchemy", "deps"], ["pydantic", "Pydantic", "deps"], ["celery", "Celery", "deps"], ["asyncpg", "asyncpg", "deps"],
  ["psycopg", "psycopg", "deps"], ["psycopg2", "psycopg2", "deps"], ["torch", "PyTorch", "stack"], ["tensorflow", "TensorFlow", "stack"],
  ["jax", "JAX", "stack"], ["numpy", "NumPy", "deps"], ["pandas", "pandas", "deps"], ["scikit-learn", "scikit-learn", "deps"],
  ["transformers", "Hugging Face Transformers", "deps"], ["requests", "requests", "deps"], ["httpx", "HTTPX", "deps"],
  ["click", "Click", "deps"], ["typer", "Typer", "deps"], ["pytest", "pytest", "test"], ["hypothesis", "Hypothesis", "test"],
  ["ruff", "Ruff", "lint"], ["black", "Black", "lint"], ["mypy", "mypy", "lint"], ["flake8", "flake8", "lint"], ["pylint", "Pylint", "lint"],
];
const CARGO_NOTABLE = [["tokio", "Tokio", "stack"], ["axum", "axum", "stack"], ["actix-web", "Actix Web", "stack"], ["rocket", "Rocket", "stack"],
  ["serde", "Serde", "deps"], ["clap", "clap", "deps"], ["anyhow", "anyhow", "deps"], ["sqlx", "SQLx", "deps"], ["diesel", "Diesel", "deps"],
  ["tauri", "Tauri", "stack"], ["bevy", "Bevy", "stack"], ["wasm-bindgen", "wasm-bindgen", "stack"]];
const GO_NOTABLE = [["github.com/gin-gonic/gin", "Gin", "stack"], ["github.com/labstack/echo", "Echo", "stack"],
  ["github.com/go-chi/chi", "chi", "stack"], ["github.com/gofiber/fiber", "Fiber", "stack"], ["github.com/spf13/cobra", "Cobra", "deps"],
  ["google.golang.org/grpc", "gRPC", "deps"], ["gorm.io/gorm", "GORM", "deps"], ["github.com/stretchr/testify", "testify", "test"]];

const SCRIPT_TOPIC = (k) => {
  if (/^(test|tests|test:.*|unit|e2e|coverage|cov|spec)$/i.test(k) || /^test[:-]/.test(k)) return "test";
  if (/^(lint|lint:.*|format|fmt|format:.*|typecheck|type-check|tsc|check|prettier|eslint)$/i.test(k)) return "lint";
  // release/publish scripts are not how work is checked or built locally; leave them to the agent.
  if (/^(build|build:.*|compile|bundle|dev|start|serve|preview|watch)$/i.test(k)) return "build";
  return null;
};
const MAKE_TOPIC = (t) => {
  if (/^(test|tests|check|unit|integration|e2e|coverage)$/.test(t)) return "test";
  if (/^(lint|fmt|format|typecheck|vet|style)$/.test(t)) return "lint";
  if (/^(build|all|install|dev|run|serve|start|docs|dist|release|setup|bootstrap)$/.test(t)) return "build";
  return null;
};

const TEST_FILE_RE = /(^|\/)(tests?|__tests__|specs?)\/|(^|\/)test_[^/]+\.py$|_test\.(py|go|exs?)$|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(java|kt|cs|swift)$|_spec\.rb$|_test\.rs$/;

const clip = (s, n = 140) => { s = String(s).replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
// A code span; a value that itself contains backticks (`ctest -j `nproc``) uses a double-backtick span so the
// command is quoted exactly, not rewritten.
const tick = (s) => (String(s).includes("`") ? "`` " + String(s) + " ``" : "`" + String(s) + "`");
const cap = (s) => (s && /^[a-z]/.test(s) ? s[0].toUpperCase() + s.slice(1) : s);

/**
 * @param {object} ctx {files, fileSet, units (resolved), zones, read, unitFiles: Map<unitPath,string[]>}
 * @returns {object[]} facts
 */
export function buildFacts(ctx) {
  const { units, read } = ctx;
  const facts = [];
  const add = (unit, topic, key, statement, file, line) => {
    if (!line || line < 1) return;
    // An empty marker file (BUILD, WORKSPACE, .gitkeep-style) has no line to cite: the fact is its existence.
    const t = text(file);
    if (t != null && line > t.split("\n").filter((l, i, a) => i < a.length - 1 || l !== "").length) {
      facts.push({ unit, topic, key, statement: clip(statement, 400), source: `search:${file} exists (empty file)` });
      return;
    }
    facts.push({ unit, topic, key, statement: clip(statement, 400), source: `${file}:${line}` });
  };
  const addSearch = (unit, topic, key, statement, what) => facts.push({ unit, topic, key, statement, source: `search:${what}` });
  add.search = (unit, statement) => addSearch(unit, "layout", "unit", statement, "package manifests at the repository root");
  const has = (p) => ctx.fileSet.has(p);
  const cache = new Map();
  const text = (p) => { if (!cache.has(p)) cache.set(p, has(p) ? read(p) : null); return cache.get(p); };
  const unitByPath = new Map(units.map((u) => [u.path, u]));
  const subject = (u) => (u.path === "." ? "the repository root" : tick(u.path));
  const multi = units.length > 1;

  // ---------------- repository-level ----------------
  repoLayout(ctx, add, addSearch, text);
  versionPins(ctx, add, text);
  ciFacts(ctx, add, addSearch, text);
  envFacts(ctx, add, text);
  conventionFacts(ctx, add, text);

  // ---------------- per unit ----------------
  for (const u of units) {
    if (u.path !== "." || !multi) unitIdentity(u, ctx, add, text, unitByPath);
    const pm = packageManager(u.path, ctx, text);
    for (const f of u.manifests) {
      const base = f.slice(f.lastIndexOf("/") + 1);
      try {
        if (base === "package.json") npmFacts(u, f, pm, add, text, subject);
        else if (base === "pyproject.toml") pyprojectFacts(u, f, add, text, subject);
        else if (base === "setup.py") setupPyFacts(u, f, add, text, subject);
        else if (base === "Cargo.toml") cargoFacts(u, f, add, text, subject, ctx);
        else if (base === "go.mod") goFacts(u, f, add, text, subject, ctx);
        else if (base === "pom.xml") mavenFacts(u, f, add, text, subject, ctx);
        else if (/^build\.gradle(\.kts)?$/.test(base)) gradleFacts(u, f, add, text, subject, ctx);
        else if (/\.(cs|fs|vb)proj$/.test(base)) dotnetFacts(u, f, add, text, subject);
        else if (base === "mix.exs") mixFacts(u, f, add, text, subject);
        else if (base === "pubspec.yaml") pubspecFacts(u, f, add, text, subject);
        else if (base === "Package.swift") add(u.path, "runtime", "swift-tools", `${cap(subject(u))} is a Swift package (swift-tools-version ${(text(f) || "").match(/swift-tools-version:\s*([\d.]+)/)?.[1] || "?"}); tests run with ${tick("swift test")}`, f, findLine(text(f) || "", /swift-tools-version/));
        else if (base === "composer.json") composerFacts(u, f, add, text, subject);
        else if (base.startsWith("deno.json")) denoFacts(u, f, add, text, subject);
        else if (base === "Gemfile") gemfileFacts(u, f, add, text, subject, ctx);
        else if (/\.gemspec$/.test(base)) gemspecFacts(u, f, add, text, subject);
        else if (base === "build.sbt") sbtFacts(u, f, add, text, subject, ctx);
        else if (base === "CMakeLists.txt") cmakeFacts(u, f, add, text, subject);
        else if (["BUILD", "BUILD.bazel", "BUCK", "TARGETS", "MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel", ".buckconfig", "pants.toml"].includes(base)) monoBuildFacts(u, f, add, text, subject, ctx);
      } catch { /* one unparseable manifest never stops the survey */ }
    }
    for (const t of ["Makefile", "makefile", "GNUmakefile", "justfile", "Justfile"]) {
      const f = at(u.path, t);
      if (has(f)) makeFacts(u, f, add, text, subject, t.toLowerCase().startsWith("just") ? "just" : "make");
    }
    for (const t of ["tox.ini", "pytest.ini", "noxfile.py"]) {
      const f = at(u.path, t);
      if (has(f)) pyToolFileFacts(u, f, t, add, text, subject);
    }
    if (u.kind === "docs") docsFacts(u, ctx, add, text, subject);
    languageFacts(u, ctx, add, addSearch);
    testPresence(u, ctx, addSearch, subject, facts);
  }
  return facts;
}

// ---------------------------------------------------------------------------------------------
function repoLayout(ctx, add, addSearch, text) {
  const { units, workspaces, zones, aux } = ctx;
  const nonRoot = units.filter((u) => u.path !== ".");
  for (const w of workspaces) {
    if (!w.members.length) continue;
    const label = { pnpm: "pnpm workspace", "npm-workspaces": "npm/yarn/bun workspace", lerna: "Lerna workspace", rush: "Rush monorepo",
      "cargo-workspace": "Cargo workspace", "go.work": "Go workspace (go.work)", "uv-workspace": "uv workspace", "hatch-workspace": "Hatch workspace",
      "pdm-workspace": "PDM workspace", "gradle-settings": "Gradle multi-project build", "maven-modules": "Maven multi-module build",
      sln: ".NET solution", "mix-umbrella": "Elixir umbrella project", melos: "Melos (Dart) workspace", "pub-workspace": "Dart pub workspace",
      "cmake-subdirectories": "CMake project with subprojects", "bazel-packages": "Bazel monorepo", "buck-packages": "Buck monorepo",
      "pants-packages": "Pants monorepo", "deno-workspace": "Deno workspace", "sbt-projects": "sbt multi-project build" }[w.by] || w.by;
    const where = w.root === "." ? "The repository" : tick(w.root);
    const sample = w.members.slice(0, 6).map(tick).join(", ") + (w.members.length > 6 ? `, and ${w.members.length - 6} more` : "");
    add(w.root, "layout", `workspace:${w.by}`, `${where} is a ${label} with ${w.members.length} member${w.members.length === 1 ? "" : "s"} declared in ${w.file}: ${sample}`, w.file, w.line);
  }
  if (nonRoot.length) {
    const kinds = {};
    for (const u of nonRoot) kinds[u.kind] = (kinds[u.kind] || 0) + 1;
    const desc = Object.entries(kinds).map(([k, n]) => `${n} ${k === "package" ? (n === 1 ? "package" : "packages") : k}`).join(", ");
    addSearch(".", "layout", "units", `The repository has ${nonRoot.length} unit${nonRoot.length === 1 ? "" : "s"} besides the root (${desc}); .stealth/index/units.idx lists each with its path, kind and claims page`, "manifests and workspace declarations (stealthlab-mcp survey)");
  }
  for (const z of zones) {
    const what = { vendored: "vendored third-party code", generated: "generated or build output", submodule: "a git submodule" }[z.kind];
    addSearch(".", "layout", `zone:${z.path}`, `${tick(z.path)} holds ${what}; it is excluded from these facts`, `${z.kind} directory ${z.path}`);
  }
  if (aux.length) {
    const sample = aux.slice(0, 5).map((a) => tick(a.path)).join(", ");
    addSearch(".", "layout", "aux", `${aux.length} manifest director${aux.length === 1 ? "y is" : "ies are"} fixtures, examples or undeclared packages and are not surveyed as units: ${sample}${aux.length > 5 ? ", …" : ""}`, "manifests not declared by any workspace");
  }
}

function versionPins(ctx, add, text) {
  const pins = [
    [".nvmrc", "Node.js"], [".node-version", "Node.js"], [".python-version", "Python"], [".ruby-version", "Ruby"],
    [".java-version", "Java"], [".bazelversion", "Bazel"], [".terraform-version", "Terraform"], [".go-version", "Go"],
  ];
  for (const u of ctx.units) {
    for (const [file, tool] of pins) {
      const f = at(u.path, file);
      const t = text(f);
      if (t == null) continue;
      const lines = t.split(/\r?\n/);
      const i = lines.findIndex((l) => l.trim() && !l.trim().startsWith("#"));
      if (i < 0) continue;
      add(u.path, "runtime", `pin:${file}`, `${tool} ${lines[i].trim()} is pinned for ${u.path === "." ? "the repository" : tick(u.path)} in ${file}`, f, i + 1);
    }
    const tv = text(at(u.path, ".tool-versions"));
    if (tv != null) {
      tv.split(/\r?\n/).forEach((l, i) => {
        const m = l.match(/^\s*([A-Za-z0-9_-]+)\s+(\S+)/);
        if (m && !l.trim().startsWith("#")) add(u.path, "runtime", `asdf:${m[1]}`, `${m[1]} ${m[2]} is pinned in .tool-versions (asdf/mise) for ${u.path === "." ? "the repository" : tick(u.path)}`, at(u.path, ".tool-versions"), i + 1);
      });
    }
    for (const rt of ["rust-toolchain.toml", "rust-toolchain"]) {
      const f = at(u.path, rt);
      const t = text(f);
      if (t == null) continue;
      const line = rt.endsWith(".toml") ? findLine(t, /^\s*channel\s*=/) : findLine(t, /\S/);
      const val = rt.endsWith(".toml") ? (t.match(/channel\s*=\s*["']([^"']+)/)?.[1]) : t.trim().split(/\s+/)[0];
      if (val) add(u.path, "runtime", "rust-toolchain", `The Rust toolchain is pinned to ${val} in ${rt}`, f, line);
    }
    // Gradle: the wrapper's distribution and the version catalog's Kotlin version (held-out okio).
    const gw = at(u.path, "gradle/wrapper/gradle-wrapper.properties");
    const dist = propertiesGet(text(gw), "distributionUrl");
    const gv = dist && dist.value.match(/gradle-(\d+(?:\.\d+)+(?:-[\w.]+)?)-(?:bin|all)\.zip/);
    if (gv) add(u.path, "build", "gradle-wrapper", `The Gradle wrapper pins Gradle ${gv[1]} for ${u.path === "." ? "the repository" : tick(u.path)}`, gw, dist.line);
    const vc = at(u.path, "gradle/libs.versions.toml");
    const vct = text(vc);
    if (vct) {
      const kl = findLine(vct, /^\s*kotlin\s*=\s*["']/);
      const kv = kl && vct.split(/\r?\n/)[kl - 1].match(/=\s*["']([^"']+)/);
      if (kv) add(u.path, "runtime", "kotlin-version", `Kotlin ${kv[1]} (gradle/libs.versions.toml version catalog)`, vc, kl);
    }
    const gj = text(at(u.path, "global.json"));
    if (gj) {
      const v = parseJsonLoose(gj)?.sdk?.version;
      if (v) add(u.path, "runtime", "dotnet-sdk", `.NET SDK ${v} is pinned in global.json`, at(u.path, "global.json"), jsonKeyLine(gj, ["sdk", "version"]));
    }
  }
}

function ciFacts(ctx, add, addSearch, text) {
  const ci = ctx.files.filter((f) => /^\.github\/workflows\/[^/]+\.ya?ml$/.test(f) || /^\.circleci\/config\.ya?ml$/.test(f) ||
    /^(\.gitlab-ci\.yml|azure-pipelines\.ya?ml|\.travis\.yml|bitbucket-pipelines\.yml|Jenkinsfile|\.drone\.yml|appveyor\.ya?ml|cloudbuild\.ya?ml|\.buildkite\/pipeline\.ya?ml)$/.test(f));
  if (!ci.length) {
    addSearch(".", "ci", "ci:none", "No CI configuration was found (looked for .github/workflows, .gitlab-ci.yml, .circleci, azure-pipelines.yml, Jenkinsfile, .travis.yml, bitbucket-pipelines.yml, .buildkite)", "CI configuration files");
    return;
  }
  const interesting = /\b(test|pytest|jest|vitest|mocha|tox|nox|lint|eslint|ruff|flake8|mypy|black|prettier|biome|tsc|typecheck|build|compile|cargo|go (test|build|vet)|mvn|gradlew?|dotnet|bazel|make|cmake|ctest|mix|flutter|swift|rspec|rake|phpunit|deno|clippy|fmt|check)\b/i;
  for (const f of ci.slice(0, 30)) {
    const t = text(f);
    if (t == null) continue;
    if (f === "Jenkinsfile") {
      const lines = t.split(/\r?\n/);
      let n = 0;
      lines.forEach((l, i) => {
        const m = l.match(/\b(?:sh|bat|powershell)\s*\(?\s*['"]{1,3}([^'"]+)['"]/);
        if (m && interesting.test(m[1]) && n++ < 8) add(".", "ci", `ci:${f}:${clip(m[1], 60)}`, `CI (Jenkinsfile) runs ${tick(clip(m[1], 120))}`, f, i + 1);
      });
      continue;
    }
    const kind = f.startsWith(".github/") ? "github" : "generic";
    const { cmds, setups } = ciCommands(t, kind);
    const label = f.startsWith(".github/workflows/") ? `CI workflow ${f}` : `CI config ${f}`;
    let n = 0;
    const seen = new Set();
    for (const c of cmds) {
      if (!interesting.test(c.cmd) || CI_NOISE.test(c.cmd)) continue;
      const k = c.cmd.replace(/\s+/g, " ");
      if (seen.has(k) || n >= 10) continue;
      seen.add(k);
      n++;
      add(".", "ci", `ci:${f}:${clip(k, 60)}`, `${label}${c.job ? ` (job ${tick(c.job)})` : ""} runs ${tick(clip(c.cmd, 140))}${c.wd ? ` in ${tick(c.wd)}` : ""}`, f, c.line);
    }
    for (const s of setups) {
      const m = s.action.match(/^actions\/setup-(node|python|go|java|dotnet|ruby)|^(dtolnay\/rust-toolchain|actions-rs\/toolchain|subosito\/flutter-action|erlef\/setup-beam|pnpm\/action-setup|oven-sh\/setup-bun|astral-sh\/setup-uv)/);
      if (!m) continue;
      const w = Object.entries(s.with)[0];
      // The `with` key says what is set up: astral-sh/setup-uv `python-version: 3.14` sets up Python 3.14, not setup-uv 3.14.
      const KEY_TOOL = { "python-version": "Python", "node-version": "Node.js", "go-version": "Go", "java-version": "Java",
        "dotnet-version": ".NET", "ruby-version": "Ruby", "bun-version": "Bun", "otp-version": "Erlang/OTP", "elixir-version": "Elixir" };
      const tool = (w && KEY_TOOL[w[0]]) || (m[1] ? { node: "Node.js", python: "Python", go: "Go", java: "Java", dotnet: ".NET", ruby: "Ruby" }[m[1]] : s.action.split("@")[0]);
      if (w && /\$\{\{\s*matrix\.([\w-]+)\s*\}\}/.test(w[1].value)) {
        // A build matrix: cite the matrix line and list its values instead of the ${{ }} expression.
        const key = w[1].value.match(/matrix\.([\w-]+)/)[1];
        const tl = t.split(/\r?\n/);
        // `key: [a, b]` (inline) or `key:` followed by `- a` lines (block list), inside a `matrix:`.
        const keyRe = new RegExp(`^(\\s*)${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*:\\s*(.*)$`);
        let ml = -1;
        let vals = [];
        for (let i = 0; i < tl.length && ml < 0; i++) {
          const km = tl[i].match(keyRe);
          if (!km || (i > 0 && !tl.slice(Math.max(0, i - 8), i).some((l) => /^\s*matrix\s*:/.test(l)) && !km[2].startsWith("["))) continue;
          if (km[2].startsWith("[")) vals = km[2].replace(/^\[|\].*$/g, "").split(",");
          else if (!km[2].trim()) {
            for (let j = i + 1; j < tl.length; j++) {
              const it = tl[j].match(/^\s*-\s*(.+?)\s*$/);
              if (!it) break;
              vals.push(it[1]);
            }
          }
          if (vals.length) ml = i;
        }
        if (ml < 0) continue;
        vals = vals.map((v) => v.replace(/\s+#.*$/, "").trim().replace(/^["']|["']$/g, "")).filter(Boolean);
        add(".", "ci", `ci-setup:${f}:${s.action.split("@")[0]}:${w[0]}`, `${label} tests on ${tool} ${vals.join(", ")} (build matrix ${key})`, f, ml + 1);
        continue;
      }
      if (w && /\$\{\{/.test(w[1].value)) continue; // an env/input expression: the version is not in this file
      if (w && /^[|>][-+]?$/.test(w[1].value.trim())) {
        // `dotnet-version: |` followed by one version per line.
        const tl = t.split(/\r?\n/);
        const ind = tl[w[1].line - 1].match(/^\s*/)[0].length;
        const vals = [];
        for (let j = w[1].line; j < tl.length; j++) {
          if (!tl[j].trim()) continue;
          if (tl[j].match(/^\s*/)[0].length <= ind) break;
          vals.push(tl[j].trim());
        }
        if (vals.length) add(".", "ci", `ci-setup:${f}:${s.action.split("@")[0]}:${w[0]}`, `${label} sets up ${tool} ${vals.join(", ")} (${s.action.split("@")[0]} ${w[0]})`, f, w[1].line);
        continue;
      }
      if (w) add(".", "ci", `ci-setup:${f}:${s.action.split("@")[0]}:${w[0]}`, `${label} sets up ${tool} ${w[1].value} (${s.action.split("@")[0]} ${w[0]})`, f, w[1].line);
      else add(".", "ci", `ci-setup:${f}:${s.action.split("@")[0]}`, `${label} uses ${tick(s.action)}`, f, s.line);
    }
  }
}

function envFacts(ctx, add, text) {
  for (const f of ctx.files) {
    const base = f.slice(f.lastIndexOf("/") + 1);
    const dir = posixDir(f);
    if (ctx.zoneOf(dir) || isAuxPath(f)) continue;
    const unit = nearestUnit(dir, ctx);
    if (/^\.env\.(example|sample|template|dist|defaults)$|^example\.env$|^env\.example$/.test(base)) {
      const t = text(f);
      if (!t) continue;
      const names = [];
      let first = 0;
      t.split(/\r?\n/).forEach((l, i) => {
        const m = l.match(/^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=/);
        if (m) { names.push(m[1]); if (!first) first = i + 1; }
      });
      if (names.length) add(unit, "env", `env-example:${f}`, `${f} lists ${names.length} environment variable${names.length === 1 ? "" : "s"} (names only): ${names.slice(0, 25).join(", ")}${names.length > 25 ? ", …" : ""}`, f, first);
    } else if (/^Dockerfile(\.[\w-]+)?$|\.Dockerfile$/.test(base) && !/\.(eex|tpl|tmpl|j2|jinja|template|erb|mustache|hbs)$/i.test(base) && !isAuxPath(f)) {
      const t = text(f);
      if (!t) continue;
      const i = findLine(t, /^\s*FROM\s+/i);
      let img = t.split(/\r?\n/)[i - 1]?.match(/^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)/i)?.[1];
      // `FROM ${BASE}` with `ARG BASE=python:3.12` above it: state the default it builds from; otherwise the
      // image is not in this file and nothing is stated.
      const v = img?.match(/^\$\{?(\w+)\}?$/)?.[1];
      if (v) {
        const arg = t.match(new RegExp(`^\\s*ARG\\s+${v}\\s*=\\s*["']?([^"'\\s]+)`, "m"))?.[1];
        img = arg && !arg.includes("$") ? arg : null;
      }
      if (img && !img.includes("$")) add(unit, "env", `docker:${f}`, `${f} builds from base image ${tick(img)}${v ? ` (ARG ${v} default)` : ""}`, f, i);
    } else if (/^(docker-)?compose(\.[\w-]+)?\.ya?ml$/.test(base) && !isAuxPath(f)) {
      const t = text(f);
      if (!t) continue;
      const lines = t.split(/\r?\n/);
      const si = lines.findIndex((l) => /^services\s*:/.test(l));
      if (si < 0) continue;
      const svcs = [];
      for (let j = si + 1; j < lines.length; j++) {
        if (/^\S/.test(lines[j])) break;
        const m = lines[j].match(/^ {2}([A-Za-z0-9_.-]+)\s*:/);
        if (m) svcs.push(m[1]);
      }
      if (svcs.length) add(unit, "env", `compose:${f}`, `${f} defines services: ${svcs.slice(0, 15).join(", ")}`, f, si + 1);
    } else if (base === "Chart.yaml") {
      const n = yamlScalar(text(f) || "", "name");
      if (n) add(unit, "env", `helm:${f}`, `Helm chart ${tick(n.value)} is defined in ${f}`, f, n.line);
    } else if (base.endsWith(".tf") && !isAuxPath(f)) {
      const t = text(f) || "";
      const m = t.match(/^provider\s+"([^"]+)"/m);
      if (m) add(unit, "env", `tf-provider:${dir}:${m[1]}`, `Terraform in ${dir === "." ? "the repository root" : tick(dir)} configures the ${tick(m[1])} provider`, f, findLine(t, new RegExp(`^provider\\s+"${m[1]}"`)));
    }
  }
}

function conventionFacts(ctx, add, text) {
  for (const f of ["AGENTS.md", "CLAUDE.md", ".cursorrules", "CONTRIBUTING.md", ".github/CONTRIBUTING.md", ".github/copilot-instructions.md"]) {
    if (ctx.fileSet.has(f)) add(".", "conventions", `doc:${f}`, `${f} holds contributor or agent instructions for this repository; read it before changing code`, f, 1);
  }
  const ec = text(".editorconfig");
  if (ec) {
    const i = findLine(ec, /^\s*indent_style\s*=/);
    if (i) add(".", "conventions", "editorconfig", `.editorconfig sets ${ec.split(/\r?\n/)[i - 1].trim()} (the first section it applies to)`, ".editorconfig", i);
  }
  const pc = text(".pre-commit-config.yaml");
  if (pc) {
    const ids = [...pc.matchAll(/^\s*-\s*id\s*:\s*([\w.-]+)/gm)].map((m) => m[1]);
    if (ids.length) add(".", "lint", "pre-commit", `pre-commit hooks run: ${[...new Set(ids)].slice(0, 15).join(", ")} (.pre-commit-config.yaml)`, ".pre-commit-config.yaml", findLine(pc, /-\s*id\s*:/));
  }
  for (const f of ["commitlint.config.js", "commitlint.config.cjs", "commitlint.config.ts", ".commitlintrc", ".commitlintrc.json"]) {
    if (ctx.fileSet.has(f)) { add(".", "conventions", "commitlint", `Commit messages are linted by commitlint (${f})`, f, 1); break; }
  }
  for (const f of ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"]) {
    if (ctx.fileSet.has(f)) { add(".", "conventions", "codeowners", `Code ownership is declared in ${f}`, f, 1); break; }
  }
  for (const [f, tool] of [[".golangci.yml", "golangci-lint"], [".golangci.yaml", "golangci-lint"], [".rubocop.yml", "RuboCop"], ["clippy.toml", "Clippy"],
    [".swiftlint.yml", "SwiftLint"], ["ruff.toml", "Ruff"], [".ruff.toml", "Ruff"], [".flake8", "flake8"], ["biome.json", "Biome"], [".stylelintrc.json", "Stylelint"]]) {
    if (ctx.fileSet.has(f)) add(".", "lint", `lintcfg:${f}`, `${tool} is configured at the repository root (${f})`, f, 1);
  }
}

// ---------------------------------------------------------------------------------------------
function unitIdentity(u, ctx, add, text) {
  const subject = u.path === "." ? "This repository" : tick(u.path);
  // Cite the manifest the name actually comes from (petclinic: name from pom.xml, not build.gradle).
  // A project declared by its build's settings file (sbt build.sbt, Gradle settings) with no manifest of its own
  // carries that settings file as its manifest: cite the declaring line, not the file's first line.
  const ownManifests = u.manifests.filter((f) => posixDir(f) === u.path);
  const m = (u.name && ownManifests.find((f) => (text(f) || "").includes(u.name))) || ownManifests[0];
  const decl = u.declaredBy?.[0];
  const ecos = u.ecosystems.join(", ") || "no package manifest";
  const KIND_WORD = { package: "package", root: "package", mobile: "mobile app", embedded: "embedded project", data: "data project",
    docs: "documentation site", notebooks: "notebooks folder" };
  const kindWord = KIND_WORD[u.kind] || u.kind;
  const member = decl ? `, a member of the ${decl.by} workspace declared in ${decl.file}` : "";
  const nameLine = m ? manifestNameLine(m, text) : 0;
  const otherTags = (u.tags || []).filter((t) => t !== u.kind);
  const tags = otherTags.length ? ` (also: ${otherTags.join(", ")})` : "";
  const named = u.name && u.name !== u.path && !u.nameFromPath ? ` named ${tick(u.name)}` : "";
  if (m && nameLine) {
    add(u.path, "layout", "unit", `${subject} is a ${kindWord}${named} (${ecos})${member}${tags}`, m, nameLine);
  } else if (decl) {
    add(u.path, "layout", "unit", `${subject} is a ${kindWord}${named} (${ecos})${member}${tags}`, decl.file, decl.line);
  } else if (u.path === "." && !u.manifests.length) {
    const hint = u.tags?.includes("dotfiles") ? "; it looks like a dotfiles repository" : "";
    add.search(u.path, `No package manifest was found at the repository root${hint}; facts come from the Makefile, CI, README and scripts`);
  }
  // Description fields are the package's own statement of purpose.
  for (const mf of u.manifests) {
    const t = text(mf);
    if (!t) continue;
    const base = mf.slice(mf.lastIndexOf("/") + 1);
    let desc = null;
    let line = 0;
    if (base === "package.json" || base === "composer.json") { desc = parseJsonLoose(t)?.description; line = jsonKeyLine(t, ["description"]); }
    else if (base === "pyproject.toml") { const p = parseToml(t); desc = tomlGet(p.data, "project.description") || tomlGet(p.data, "tool.poetry.description"); line = p.lineOf.get("project.description") || p.lineOf.get("tool.poetry.description") || 0; }
    else if (base === "Cargo.toml") { const p = parseToml(t); desc = tomlGet(p.data, "package.description"); line = p.lineOf.get("package.description") || 0; }
    else if (base === "pom.xml") { const d = xmlTag(t.replace(/<parent>[\s\S]*?<\/parent>/, (s) => s.replace(/[^\n]/g, " ")), "description"); if (d) { desc = d.value; line = d.line; } }
    else if (/\.(cs|fs|vb)proj$/.test(base)) { const d = xmlTag(t, "Description"); if (d) { desc = d.value; line = d.line; } }
    else if (base === "pubspec.yaml") { const d = yamlScalar(t, "description"); if (d) { desc = d.value; line = d.line; } }
    if (typeof desc === "string" && desc.trim().length >= 8 && line) {
      add(u.path, "purpose", "description", `${u.path === "." ? "The repository's" : tick(u.path) + "'s"} manifest describes it as: "${clip(desc, 200)}"`, mf, line);
      break;
    }
  }
}

function manifestNameLine(m, text) {
  const t = text(m);
  if (!t) return 0;
  const base = m.slice(m.lastIndexOf("/") + 1);
  if (base === "package.json" || base === "composer.json" || base === "project.json" || base.startsWith("deno.json")) return jsonKeyLine(t, ["name"]) || 1;
  if (base === "pyproject.toml") { const p = parseToml(t); return p.lineOf.get("project.name") || p.lineOf.get("tool.poetry.name") || p.lineOf.get("project") || 1; }
  if (base === "Cargo.toml") { const p = parseToml(t); return p.lineOf.get("package.name") || p.lineOf.get("workspace") || 1; }
  if (base === "go.mod") return findLine(t, /^module\s/) || 1;
  if (base === "pom.xml") return findLine(t.replace(/<parent>[\s\S]*?<\/parent>/, (s) => s.replace(/[^\n]/g, " ")), /<artifactId>/) || 1;
  if (base === "pubspec.yaml" || base === "mix.exs") return findLine(t, /^\s*(name:|app:)/) || 1;
  if (base === "setup.py") return findLine(t, /name\s*=/) || 1;
  return 1;
}

function packageManager(dir, ctx, text) {
  let d = dir;
  for (;;) {
    // The packageManager field is exact (name and version, enforced by corepack); a lockfile names only the tool.
    const pj = text(at(d, "package.json"));
    const field = pj && parseJsonLoose(pj)?.packageManager;
    if (typeof field === "string") return { pm: field.split("@")[0], file: at(d, "package.json"), field };
    for (const [lock, pm] of [["pnpm-lock.yaml", "pnpm"], ["yarn.lock", "yarn"], ["bun.lockb", "bun"], ["bun.lock", "bun"], ["package-lock.json", "npm"]]) {
      if (ctx.fileSet.has(at(d, lock))) return { pm, file: at(d, lock) };
    }
    if (d === ".") return { pm: "npm", file: null };
    d = posixDir(d);
  }
}

export function scriptCommand(pm, key) {
  if (key === "test" || key === "start") return `${pm === "bun" ? "bun run" : pm} ${key}`;
  return `${pm} run ${key}`;
}

function npmFacts(u, f, pmInfo, add, text, subject) {
  const t = text(f);
  const pj = parseJsonLoose(t || "");
  if (!pj) return;
  const pm = pmInfo.pm;
  // Stated once, where it is decided: the unit whose own directory holds the lockfile / packageManager field.
  if (pmInfo.file && posixDir(pmInfo.file) === u.path) {
    const lockBase = pmInfo.file.slice(pmInfo.file.lastIndexOf("/") + 1);
    if (pmInfo.field) add(u.path, "stack", "package-manager", `${cap(subject(u))} uses ${pmInfo.field} (package.json packageManager)`, f, jsonKeyLine(t, ["packageManager"]));
    else add(u.path, "stack", "package-manager", `${cap(subject(u))} uses ${pm} as its package manager (${lockBase} is committed)`, pmInfo.file, 1);
  }
  const engines = pj.engines || {};
  for (const [k, v] of Object.entries(engines)) {
    if (typeof v === "string") add(u.path, "runtime", `engines:${k}`, `${cap(subject(u))} declares engines.${k} ${tick(v)} in package.json`, f, jsonKeyLine(t, ["engines", k]));
  }
  if (pj.type === "module") add(u.path, "conventions", "esm", `${cap(subject(u))} is an ES module package ("type": "module" in package.json)`, f, jsonKeyLine(t, ["type"]));
  const scripts = pj.scripts || {};
  for (const [k, v] of Object.entries(scripts)) {
    const topic = SCRIPT_TOPIC(k);
    if (!topic || typeof v !== "string") continue;
    const where = u.path === "." ? "At the repository root" : `In ${tick(u.path)}`;
    add(u.path, topic, `script:${k}`, `${where}, ${tick(scriptCommand(pm, k))} runs ${tick(clip(v, 160))} (package.json script ${tick(k)})`, f, jsonKeyLine(t, ["scripts", k]));
  }
  const deps = { ...(pj.dependencies || {}), ...(pj.peerDependencies || {}) };
  const dev = pj.devDependencies || {};
  for (const [name, label, topic] of NPM_NOTABLE) {
    const sect = name in deps ? (pj.dependencies && name in pj.dependencies ? "dependencies" : "peerDependencies") : name in dev ? "devDependencies" : null;
    if (!sect) continue;
    const ver = (deps[name] ?? dev[name]);
    // A runtime framework that is only a devDependency is a test/dev tool here, not what the package is built on.
    const devOnly = sect === "devDependencies" && (topic === "stack" || topic === "deps") && name !== "typescript";
    const verb = devOnly ? `has ${label} as a development dependency` : `uses ${label}`;
    add(u.path, topic, `dep:${name}`, `${cap(subject(u))} ${verb} (${tick(name)} ${ver} in ${sect})`, f, jsonKeyLine(t, [sect, name]));
  }
  if (pj.bin) {
    const names = typeof pj.bin === "string" ? [pj.name] : Object.keys(pj.bin);
    add(u.path, "features", "bin", `${cap(subject(u))} installs the command${names.length === 1 ? "" : "s"} ${names.slice(0, 6).map(tick).join(", ")} (package.json bin)`, f, jsonKeyLine(t, ["bin"]));
  }
}

function pyDepName(d) { return String(d).split(/[\s<>=!~;[\]()@,]/)[0].toLowerCase().replace(/_/g, "-"); }

function pyprojectFacts(u, f, add, text, subject) {
  const t = text(f);
  if (!t) return;
  const { data, lineOf } = parseToml(t);
  const rp = tomlGet(data, "project.requires-python");
  if (rp) add(u.path, "runtime", "requires-python", `${cap(subject(u))} requires Python ${tick(rp)} (pyproject.toml requires-python)`, f, lineOf.get("project.requires-python"));
  const poetryPy = tomlGet(data, "tool.poetry.dependencies.python");
  if (poetryPy) add(u.path, "runtime", "poetry-python", `${cap(subject(u))} requires Python ${tick(poetryPy)} (pyproject.toml [tool.poetry.dependencies])`, f, lineOf.get("tool.poetry.dependencies.python"));
  const backend = tomlGet(data, "build-system.build-backend");
  if (backend) add(u.path, "build", "build-backend", `${cap(subject(u))} is packaged with the ${tick(backend)} build backend (pyproject.toml [build-system])`, f, lineOf.get("build-system.build-backend"));
  if (tomlGet(data, "tool.poetry")) add(u.path, "stack", "poetry", `${cap(subject(u))} is managed with Poetry (pyproject.toml [tool.poetry])`, f, lineOf.get("tool.poetry") || findLine(t, /^\[tool\.poetry/));
  // [tool.pytest.ini_options] (classic) or [tool.pytest] (pytest 9 native TOML configuration).
  const ptKey = tomlGet(data, "tool.pytest.ini_options") ? "tool.pytest.ini_options" : tomlGet(data, "tool.pytest") ? "tool.pytest" : null;
  const pt = ptKey && tomlGet(data, ptKey);
  if (pt) {
    const tp = pt.testpaths ? ` with testpaths ${[].concat(pt.testpaths).map(tick).join(", ")}` : "";
    add(u.path, "test", "pytest-config", `pytest is configured for ${subject(u)} in pyproject.toml [${ptKey}]${tp}`, f, lineOf.get(ptKey) || findLine(t, /^\[tool\.pytest/));
  }
  for (const [sect, label] of [["tool.ruff", "Ruff"], ["tool.black", "Black"], ["tool.mypy", "mypy"], ["tool.isort", "isort"], ["tool.pylint", "Pylint"], ["tool.pyright", "Pyright"]]) {
    if (tomlGet(data, sect)) add(u.path, "lint", `pyproject:${sect}`, `${label} is configured for ${subject(u)} in pyproject.toml [${sect}]`, f, lineOf.get(sect) || findLine(t, new RegExp(`^\\[${sect.replace(".", "\\.")}`)));
  }
  if (tomlGet(data, "tool.uv")) add(u.path, "stack", "uv", `${cap(subject(u))} uses uv (pyproject.toml [tool.uv])`, f, lineOf.get("tool.uv") || findLine(t, /^\[tool\.uv/));
  // A committed Python lockfile names the package manager the repository actually uses.
  for (const [lock, tool] of [["uv.lock", "uv"], ["poetry.lock", "Poetry"], ["pdm.lock", "PDM"], ["Pipfile.lock", "Pipenv"]]) {
    const lp = u.path === "." ? lock : `${u.path}/${lock}`;
    if (text(lp) != null) { add(u.path, "stack", `pylock:${lock}`, `${cap(subject(u))} locks its Python dependencies with ${tool} (${lock} is committed)`, lp, 1); break; }
  }
  const deps = [...(tomlGet(data, "project.dependencies") || [])];
  const depLine = lineOf.get("project.dependencies");
  const poetryDeps = Object.keys(tomlGet(data, "tool.poetry.dependencies") || {});
  const optional = tomlGet(data, "project.optional-dependencies") || {};
  const groups = tomlGet(data, "dependency-groups") || {};
  const all = new Map();
  for (const d of deps) all.set(pyDepName(d), { spec: d, line: lineForDep(t, d, depLine) });
  for (const d of poetryDeps) if (d !== "python") all.set(d.toLowerCase(), { spec: d, line: lineOf.get(`tool.poetry.dependencies.${d}`) || 0 });
  for (const [g, list] of [...Object.entries(optional), ...Object.entries(groups)]) for (const d of [].concat(list)) if (typeof d === "string" && !all.has(pyDepName(d))) all.set(pyDepName(d), { spec: d, line: lineForDep(t, d, 0), group: g });
  for (const [name, label, topic] of PY_NOTABLE) {
    const hit = all.get(name);
    if (hit && hit.line) add(u.path, topic, `dep:${name}`, `${cap(subject(u))} uses ${label} (${tick(String(hit.spec).trim())}${hit.group ? ` in the ${hit.group} extra/group` : ""}, pyproject.toml)`, f, hit.line);
  }
  const scripts = tomlGet(data, "project.scripts") || tomlGet(data, "tool.poetry.scripts");
  if (scripts && typeof scripts === "object") {
    const names = Object.keys(scripts);
    if (names.length) add(u.path, "features", "console-scripts", `${cap(subject(u))} installs the console command${names.length === 1 ? "" : "s"} ${names.slice(0, 6).map(tick).join(", ")} (pyproject.toml scripts)`, f, lineOf.get("project.scripts") || lineOf.get("tool.poetry.scripts"));
  }
}

function lineForDep(t, spec, fallback) {
  const i = findLine(t, new RegExp(`["']${String(spec).trim().replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}["']`));
  return i || fallback || 0;
}

function setupPyFacts(u, f, add, text, subject) {
  const t = text(f);
  if (!t) return;
  const m = t.match(/python_requires\s*=\s*["']([^"']+)["']/);
  if (m) add(u.path, "runtime", "python_requires", `${cap(subject(u))} requires Python ${tick(m[1])} (setup.py python_requires)`, f, findLine(t, /python_requires\s*=/));
}

function pyToolFileFacts(u, f, base, add, text, subject) {
  const t = text(f) || "";
  if (base === "tox.ini") {
    const i = findLine(t, /^\s*envlist\s*=/);
    const env = i ? t.split(/\r?\n/)[i - 1].split("=")[1].trim() : "";
    add(u.path, "test", "tox", `tox is configured for ${subject(u)} (tox.ini${env ? `, envlist ${env}` : ""}); run ${tick("tox")}`, f, i || 1);
  } else if (base === "pytest.ini") {
    add(u.path, "test", "pytest-ini", `pytest is configured for ${subject(u)} in pytest.ini`, f, findLine(t, /^\[pytest\]/) || 1);
  } else if (base === "noxfile.py") {
    const sessions = [...t.matchAll(/@nox\.session[^\n]*\n\s*def\s+(\w+)/g)].map((m) => m[1]);
    add(u.path, "test", "nox", `nox sessions are defined for ${subject(u)} in noxfile.py${sessions.length ? `: ${sessions.slice(0, 8).join(", ")}` : ""}`, f, findLine(t, /@nox\.session/) || 1);
  }
}

function cargoFacts(u, f, add, text, subject, ctx) {
  const t = text(f);
  if (!t) return;
  const { data, lineOf } = parseToml(t);
  const pkg = tomlGet(data, "package");
  // `rust-version.workspace = true` inherits [workspace.package]: only literal strings are stated here.
  const wsPkg = tomlGet(data, "workspace.package") || {};
  if (typeof wsPkg["rust-version"] === "string") add(u.path, "runtime", "ws-rust-version", `${cap(subject(u))} requires Rust ${wsPkg["rust-version"]} or newer for every workspace member (Cargo.toml [workspace.package] rust-version)`, f, lineOf.get("workspace.package.rust-version"));
  if (typeof wsPkg.edition === "string") add(u.path, "stack", "ws-edition", `${cap(subject(u))} uses Rust edition ${wsPkg.edition} for every workspace member (Cargo.toml [workspace.package])`, f, lineOf.get("workspace.package.edition"));
  if (pkg) {
    if (typeof pkg["rust-version"] === "string") add(u.path, "runtime", "rust-version", `${cap(subject(u))} requires Rust ${pkg["rust-version"]} or newer (Cargo.toml rust-version)`, f, lineOf.get("package.rust-version"));
    if (typeof pkg.edition === "string") add(u.path, "stack", "edition", `${cap(subject(u))} is a Rust crate on edition ${pkg.edition} (Cargo.toml)`, f, lineOf.get("package.edition"));
    const inWs = u.declaredBy?.some((d) => d.by === "cargo-workspace");
    const cmd = inWs && u.name ? `cargo test -p ${u.name}` : "cargo test";
    add(u.path, "test", "cargo-test", `${cap(subject(u))} is tested with ${tick(cmd)} (standard Cargo command for this crate)`, f, lineOf.get("package.name") || lineOf.get("package"));
  }
  if (tomlGet(data, "workspace")) {
    add(u.path, "test", "cargo-test-ws", `${cap(subject(u))} is a Cargo workspace; ${tick("cargo test --workspace")} tests every member (standard Cargo command)`, f, lineOf.get("workspace"));
  }
  const deps = { ...(tomlGet(data, "dependencies") || {}), ...(tomlGet(data, "workspace.dependencies") || {}) };
  for (const [name, label, topic] of CARGO_NOTABLE) {
    if (!(name in deps)) continue;
    const v = typeof deps[name] === "string" ? deps[name] : deps[name]?.version || (deps[name]?.workspace ? "workspace" : "");
    add(u.path, topic, `dep:${name}`, `${cap(subject(u))} uses ${label} (${tick(name)} ${v} in Cargo.toml)`, f, lineOf.get(`dependencies.${name}`) || lineOf.get(`workspace.dependencies.${name}`) || findLine(t, new RegExp(`^\\s*${name}\\s*=`)));
  }
  const bins = (tomlGet(data, "bin") || []).map((b) => b.name).filter(Boolean);
  if (bins.length) add(u.path, "features", "bins", `${cap(subject(u))} builds the binar${bins.length === 1 ? "y" : "ies"} ${bins.slice(0, 6).map(tick).join(", ")} (Cargo.toml [[bin]])`, f, lineOf.get("bin") || findLine(t, /^\[\[bin\]\]/));
  void ctx;
}

function goFacts(u, f, add, text, subject, ctx) {
  const t = text(f);
  if (!t) return;
  const gl = findLine(t, /^go\s+\d/);
  if (gl) add(u.path, "runtime", "go-version", `${cap(subject(u))} targets Go ${t.split(/\r?\n/)[gl - 1].trim().split(/\s+/)[1]} (go.mod go directive)`, f, gl);
  const tl = findLine(t, /^toolchain\s+/);
  if (tl) add(u.path, "runtime", "go-toolchain", `${cap(subject(u))} pins the Go toolchain ${t.split(/\r?\n/)[tl - 1].trim().split(/\s+/)[1]} (go.mod)`, f, tl);
  add(u.path, "test", "go-test", `${cap(subject(u))} is tested with ${tick("go test ./...")} run in ${u.path === "." ? "the repository root" : tick(u.path)} (standard Go command for module ${tick(u.name)})`, f, findLine(t, /^module\s/) || 1);
  for (const [mod, label, topic] of GO_NOTABLE) {
    const i = findLine(t, new RegExp(`^\\s*(require\\s+)?${mod.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}(/v\\d+)?\\s+v`));
    // `// indirect` is a transitive dependency: the module does not use it directly.
    if (i && /\/\/\s*indirect\b/.test(t.split(/\r?\n/)[i - 1])) continue;
    if (i) add(u.path, topic, `dep:${mod}`, `${cap(subject(u))} uses ${label} (${tick(t.split(/\r?\n/)[i - 1].trim().replace(/^require\s+/, "").replace(/\s*\/\/.*$/, ""))} in go.mod)`, f, i);
  }
  const cmds = [...new Set(ctx.files.filter((p) => p.startsWith(u.path === "." ? "cmd/" : `${u.path}/cmd/`) && /\/main\.go$/.test(p)).map((p) => p.split("/").slice(-2, -1)[0]))];
  if (cmds.length) facts_cmds(u, cmds, add, ctx);
}
function facts_cmds(u, cmds, add, ctx) {
  const first = ctx.files.find((p) => p.startsWith(u.path === "." ? `cmd/${cmds[0]}/` : `${u.path}/cmd/${cmds[0]}/`) && p.endsWith("/main.go"));
  if (first) add(u.path, "features", "go-cmds", `${u.path === "." ? "The repository" : tick(u.path)} builds the command${cmds.length === 1 ? "" : "s"} ${cmds.slice(0, 8).map(tick).join(", ")} (cmd/<name>/main.go)`, first, 1);
}

function mavenFacts(u, f, add, text, subject, ctx) {
  const t = text(f);
  if (!t) return;
  for (const prop of ["maven.compiler.release", "maven.compiler.source", "java.version", "kotlin.version"]) {
    const v = xmlTag(t, prop.replace(/\./g, "\\."));
    if (v) { add(u.path, "runtime", `maven:${prop}`, `${cap(subject(u))} sets ${prop} to ${v.value} (pom.xml)`, f, v.line); if (prop !== "kotlin.version") break; }
  }
  const parent = t.match(/<parent>[\s\S]*?<artifactId>\s*spring-boot-starter-parent\s*<\/artifactId>[\s\S]*?<version>\s*([^<]+)<\/version>/);
  if (parent) add(u.path, "stack", "spring-boot", `${cap(subject(u))} is a Spring Boot ${parent[1].trim()} project (spring-boot-starter-parent in pom.xml)`, f, findLine(t, /spring-boot-starter-parent/));
  const wrapper = ctx.fileSet.has("mvnw") ? "./mvnw" : "mvn";
  const cmd = u.path === "." ? `${wrapper} test` : `${wrapper} -pl ${u.path} -am test`;
  add(u.path, "test", "maven-test", `${cap(subject(u))} is tested with ${tick(cmd)} from the repository root (standard Maven command)`, f, findLine(t, /<artifactId>/) || 1);
}

function gradleFacts(u, f, add, text, subject, ctx) {
  const t = text(f);
  if (!t) return;
  const plugins = [
    // Plugins APPLIED to this project: by id, by kotlin("x"), or as a version-catalog alias
    // (`alias(libs.plugins.x.android.application)`). A line ending in `apply false` only declares the plugin for
    // subprojects, and `libs.plugins.x.android.application.get().pluginId` in a convention plugin registers it.
    [/org\.springframework\.boot/, "Spring Boot"],
    [/com\.android\.application|alias\(\s*libs\.plugins\.[\w.]*android\.application\s*\)/, "an Android application"],
    [/com\.android\.library|alias\(\s*libs\.plugins\.[\w.]*android\.library\s*\)/, "an Android library"],
    [/kotlin\(\s*"jvm"\s*\)|org\.jetbrains\.kotlin\.jvm|alias\(\s*libs\.plugins\.kotlin\.jvm\s*\)/, "Kotlin/JVM"],
    [/kotlin\(\s*"multiplatform"\s*\)|org\.jetbrains\.kotlin\.multiplatform|alias\(\s*libs\.plugins\.kotlin\.multiplatform\s*\)/, "Kotlin Multiplatform"],
    [/id\s*\(?\s*["']java-library["']|^\s*`java-library`/m, "a Java library"], [/id\s*\(?\s*["']application["']|^\s*application\s*$/m, "a JVM application"],
  ];
  const tlines = t.split(/\r?\n/);
  for (const [re, label] of plugins) {
    const i = tlines.findIndex((l) => re.test(l) && !/\bapply\s*\(?\s*false\b/.test(l)) + 1;
    if (i) add(u.path, "stack", `gradle-plugin:${label}`, `${cap(subject(u))} is built as ${label} with Gradle (${f.slice(f.lastIndexOf("/") + 1)})`, f, i);
  }
  // The version and the cited line come from the SAME match (toolchain, languageVersion, or sourceCompatibility in
  // any of its spellings: 17, "17", JavaVersion.VERSION_17, JvmTarget.JVM_17.target).
  const tc = t.match(/jvmToolchain\(\s*(\d+)\s*\)|JavaLanguageVersion\.of\(\s*(\d+)\s*\)|sourceCompatibility\s*=\s*(?:JavaVersion\.VERSION_|JvmTarget\.JVM_)?['"]?([\d_.]+)/);
  if (tc) add(u.path, "runtime", "jvm-version", `${cap(subject(u))} compiles for Java ${(tc[1] || tc[2] || tc[3]).replace(/_/g, ".")} (Gradle ${tc[1] ? "jvmToolchain" : tc[2] ? "toolchain languageVersion" : "sourceCompatibility"})`, f, lineAt(t, tc.index));
  // Inside a multi-project build the task is addressed from the build root; a standalone build runs in its own dir.
  const decl = u.declaredBy?.find((d) => d.by === "gradle-settings");
  const buildRoot = decl ? decl.root : u.path;
  const hasWrapper = ctx.fileSet.has(buildRoot === "." ? "gradlew" : `${buildRoot}/gradlew`);
  const wrapper = hasWrapper ? "./gradlew" : "gradle";
  const rel = decl && u.path !== buildRoot ? (buildRoot === "." ? u.path : u.path.slice(buildRoot.length + 1)) : "";
  const proj = rel ? ":" + rel.replace(/\//g, ":") + ":" : "";
  const where = buildRoot === "." ? "the repository root" : tick(buildRoot);
  // A java-platform (BOM) or version-catalog project has no `test` task (held-out retrofit-bom).
  const platformLine = tlines.findIndex((l) => /['"`]java-platform['"`]|['"`]version-catalog['"`]/.test(l)) + 1;
  if (platformLine) {
    add(u.path, "build", "gradle-platform", `${cap(subject(u))} is a Gradle platform (BOM) project with no tests of its own`, f, platformLine);
    return;
  }
  add(u.path, "test", "gradle-test", `${cap(subject(u))} is tested with ${tick(`${wrapper} ${proj}test`)} from ${where} (standard Gradle task)`, f, 1);
}

function dotnetFacts(u, f, add, text, subject) {
  const t = text(f);
  if (!t) return;
  // <TargetFramework(s)> may carry a Condition and compose with $(TargetFrameworks): state the unconditional
  // element's literal frameworks (found on held-out serilog: `$(TargetFrameworks);net10.0;...;netstandard2.0`).
  const tfs = [...t.matchAll(/<TargetFrameworks?(\s[^>]*)?>([^<]+)<\/TargetFrameworks?>/g)]
    .map((m) => ({ cond: /Condition=/.test(m[1] || ""), values: m[2].split(";").map((v) => v.trim()).filter((v) => v && !v.includes("$(")), line: lineAt(t, m.index) }))
    .filter((x) => x.values.length);
  const tf = tfs.find((x) => !x.cond) || tfs[0];
  if (tf) add(u.path, "runtime", "target-framework", `${cap(subject(u))} targets ${tf.values.join(", ")}${tf.cond ? " (conditionally)" : ""} (${f.slice(f.lastIndexOf("/") + 1)})`, f, tf.line);
  for (const [pkg, label] of [["xunit", "xUnit"], ["NUnit", "NUnit"], ["MSTest.TestFramework", "MSTest"], ["Microsoft.AspNetCore", "ASP.NET Core"], ["Microsoft.EntityFrameworkCore", "Entity Framework Core"]]) {
    const i = findLine(t, new RegExp(`Include="${pkg.replace(/\./g, "\\.")}`));
    if (i) add(u.path, /Unit|MSTest/.test(label) ? "test" : "stack", `nuget:${pkg}`, `${cap(subject(u))} uses ${label} (PackageReference in ${f.slice(f.lastIndexOf("/") + 1)})`, f, i);
  }
  if (/Sdk="Microsoft\.NET\.Sdk\.Web"/.test(t)) add(u.path, "stack", "aspnet-sdk", `${cap(subject(u))} is an ASP.NET Core web project (Microsoft.NET.Sdk.Web)`, f, findLine(t, /Microsoft\.NET\.Sdk\.Web/));
}

function mixFacts(u, f, add, text, subject) {
  const t = text(f) || "";
  const i = findLine(t, /elixir:\s*["']/);
  if (i) add(u.path, "runtime", "elixir", `${cap(subject(u))} requires Elixir ${t.split(/\r?\n/)[i - 1].match(/elixir:\s*["']([^"']+)/)[1]} (mix.exs)`, f, i);
  else {
    // `elixir: @elixir_requirement` with `@elixir_requirement "~> 1.15"` defined above it: cite the attribute.
    const attr = t.match(/elixir:\s*@(\w+)/)?.[1];
    const al = attr ? findLine(t, new RegExp(`^\\s*@${attr}\\s+["']`)) : 0;
    if (al) add(u.path, "runtime", "elixir", `${cap(subject(u))} requires Elixir ${t.split(/\r?\n/)[al - 1].match(/["']([^"']+)/)[1]} (mix.exs @${attr})`, f, al);
  }
  // A dependency tuple `{:phoenix, "~> 1.7"}` -- not `app: :phoenix`, which is Phoenix itself.
  const p = findLine(t, /\{\s*:phoenix\s*,/);
  if (p) add(u.path, "stack", "phoenix", `${cap(subject(u))} uses Phoenix (mix.exs deps)`, f, p);
  add(u.path, "test", "mix-test", `${cap(subject(u))} is tested with ${tick("mix test")} (standard Mix command)`, f, findLine(t, /defmodule/) || 1);
}

function pubspecFacts(u, f, add, text, subject) {
  const t = text(f) || "";
  const sdk = yamlScalar(t, "sdk");
  if (sdk) add(u.path, "runtime", "dart-sdk", `${cap(subject(u))} requires the Dart SDK ${tick(sdk.value)} (pubspec.yaml environment)`, f, sdk.line);
  const fl = findLine(t, /^\s*flutter\s*:\s*$/);
  const isFlutter = findLine(t, /^\s{2}flutter\s*:\s*$/) || /sdk:\s*flutter/.test(t);
  if (isFlutter) add(u.path, "stack", "flutter", `${cap(subject(u))} is a Flutter package; tests run with ${tick("flutter test")}`, f, findLine(t, /sdk:\s*flutter/) || fl || 1);
  else add(u.path, "test", "dart-test", `${cap(subject(u))} is a Dart package; tests run with ${tick("dart test")}`, f, findLine(t, /^name:/) || 1);
}

function composerFacts(u, f, add, text, subject) {
  const t = text(f);
  const cj = parseJsonLoose(t || "");
  if (!cj) return;
  const php = cj.require?.php;
  if (php) add(u.path, "runtime", "php", `${cap(subject(u))} requires PHP ${tick(php)} (composer.json)`, f, jsonKeyLine(t, ["require", "php"]));
  for (const [name, label] of [["laravel/framework", "Laravel"], ["symfony/framework-bundle", "Symfony"], ["phpunit/phpunit", "PHPUnit"]]) {
    const sect = cj.require?.[name] ? "require" : cj["require-dev"]?.[name] ? "require-dev" : null;
    if (sect) add(u.path, name.includes("phpunit") ? "test" : "stack", `dep:${name}`, `${cap(subject(u))} uses ${label} (${tick(name)} ${cj[sect][name]} in composer.json)`, f, jsonKeyLine(t, [sect, name]));
  }
  for (const [k, v] of Object.entries(cj.scripts || {})) {
    const topic = SCRIPT_TOPIC(k);
    if (topic) add(u.path, topic, `composer:${k}`, `In ${u.path === "." ? "the repository" : tick(u.path)}, ${tick(`composer ${k}`)} runs ${tick(clip([].concat(v).join(" && "), 140))}`, f, jsonKeyLine(t, ["scripts", k]));
  }
}

function denoFacts(u, f, add, text, subject) {
  const t = text(f);
  const dj = parseJsonLoose(t || "");
  if (!dj) return;
  for (const [k, v] of Object.entries(dj.tasks || {})) {
    const topic = SCRIPT_TOPIC(k);
    if (topic && typeof v === "string") add(u.path, topic, `task:${k}`, `In ${u.path === "." ? "the repository" : tick(u.path)}, ${tick(`deno task ${k}`)} runs ${tick(clip(v, 140))} (${f.slice(f.lastIndexOf("/") + 1)})`, f, jsonKeyLine(t, ["tasks", k]));
  }
  void subject;
}

function gemfileFacts(u, f, add, text, subject, ctx) {
  const t = text(f) || "";
  const r = findLine(t, /^\s*ruby\s+["']/);
  if (r) add(u.path, "runtime", "ruby", `${cap(subject(u))} requires Ruby ${t.split(/\r?\n/)[r - 1].match(/ruby\s+["']([^"']+)/)[1]} (Gemfile)`, f, r);
  const rails = findLine(t, /^\s*gem\s+["']rails["']/);
  if (rails) add(u.path, "stack", "rails", `${cap(subject(u))} is a Ruby on Rails application (Gemfile)`, f, rails);
  const rspec = findLine(t, /^\s*gem\s+["']rspec(-rails)?["']/);
  if (rspec && ctx.files.some((p) => p.startsWith(u.path === "." ? "spec/" : `${u.path}/spec/`))) add(u.path, "test", "rspec", `${cap(subject(u))} is tested with RSpec; run ${tick("bundle exec rspec")}`, f, rspec);
}

function gemspecFacts(u, f, add, text, subject) {
  const t = text(f) || "";
  const r = findLine(t, /\.required_ruby_version\s*=/);
  const v = r && t.split(/\r?\n/)[r - 1].match(/required_ruby_version\s*=\s*\[?\s*["']([^"']+)["']/);
  if (v) add(u.path, "runtime", "ruby", `${cap(subject(u))} requires Ruby ${v[1]} (${f.slice(f.lastIndexOf("/") + 1)})`, f, r);
}

// sbt: the sbt version (project/build.properties), the Scala versions the build names, and how its tests run. A
// project declared in a multi-project build.sbt is tested from the build root (held-out zio).
function sbtFacts(u, f, add, text, subject, ctx) {
  const buildRoot = posixDir(f);
  const isRoot = u.path === buildRoot;
  if (!isRoot) {
    const decl = u.declaredBy?.find((d) => d.by === "sbt-projects");
    if (decl) add(u.path, "test", "sbt-test", `${cap(subject(u))} is a project of the sbt build in ${decl.file}; run ${tick("sbt test")} from ${buildRoot === "." ? "the repository root" : tick(buildRoot)} (it tests every project) or the project's own ${tick("<id>/test")} task`, decl.file, decl.line);
    return;
  }
  const props = at(buildRoot, "project/build.properties");
  const sv = propertiesGet(text(props), "sbt.version");
  if (sv) add(u.path, "build", "sbt-version", `${cap(subject(u))} builds with sbt ${sv.value} (project/build.properties)`, props, sv.line);
  const t = text(f) || "";
  const lit = t.match(/scalaVersion\s*:=\s*"(\d+\.\d+\.\d+[^"]*)"/);
  if (lit) add(u.path, "runtime", "scala-version", `${cap(subject(u))} compiles with Scala ${lit[1]} (build.sbt scalaVersion)`, f, lineAt(t, lit.index));
  else {
    // Versions named in project/*.scala (`val Scala3: String = "3.3.8"`), as many builds keep them.
    const dir = buildRoot === "." ? "project/" : `${buildRoot}/project/`;
    for (const pf of ctx.files.filter((p) => p.startsWith(dir) && /^[^/]+\.scala$/.test(p.slice(dir.length))).sort()) {
      const pt = text(pf) || "";
      const vals = [...pt.matchAll(/\bval\s+Scala\w*\s*(?::\s*String\s*)?=\s*"(\d+\.\d+\.\d+[^"]*)"/g)];
      if (!vals.length) continue;
      add(u.path, "runtime", "scala-version", `${cap(subject(u))} cross-builds for Scala ${vals.map((m) => m[1]).join(", ")} (${pf.slice(pf.lastIndexOf("/") + 1)})`, pf, lineAt(pt, vals[0].index));
      break;
    }
  }
  add(u.path, "test", "sbt-test", `${cap(subject(u))} is tested with ${tick("sbt test")} (standard sbt task)`, f, 1);
}

function cmakeFacts(u, f, add, text, subject) {
  const t = text(f) || "";
  const mv = findLine(t, /cmake_minimum_required/i);
  if (mv) add(u.path, "build", "cmake-min", `${cap(subject(u))} builds with CMake (${t.split(/\r?\n/)[mv - 1].trim()})`, f, mv);
  const std = t.match(/CMAKE_CXX_STANDARD\s+(\d+)/);
  if (std) add(u.path, "runtime", "cxx-standard", `${cap(subject(u))} compiles as C++${std[1]} (CMAKE_CXX_STANDARD)`, f, findLine(t, /CMAKE_CXX_STANDARD\s+\d/));
  const et = findLine(t, /enable_testing\s*\(|include\s*\(\s*CTest\s*\)/i);
  if (et) add(u.path, "test", "ctest", `${cap(subject(u))} registers tests with CTest; run ${tick("ctest")} in the build directory`, f, et);
}

function monoBuildFacts(u, f, add, text, subject, ctx) {
  const base = f.slice(f.lastIndexOf("/") + 1);
  if (u.path === ".") {
    const tool = base === "pants.toml" ? "Pants" : base === ".buckconfig" ? "Buck" : "Bazel";
    add(".", "build", "monobuild", `The repository builds with ${tool} (${base} at the root)`, f, 1);
    return;
  }
  const tool = ctx.fileSet.has("pants.toml") ? "pants" : ctx.fileSet.has(".buckconfig") ? "buck2" : "bazel";
  const cmd = tool === "pants" ? `pants test ${u.path}::` : `${tool} test //${u.path}/...`;
  add(u.path, "test", "monobuild-test", `${cap(subject(u))} is a ${tool} package group; ${tick(cmd)} tests it`, f, 1);
  void text;
}

function makeFacts(u, f, add, text, subject, tool) {
  const t = text(f) || "";
  const lines = t.split(/\r?\n/);
  for (const { target, line } of makeTargets(t)) {
    const topic = MAKE_TOPIC(target);
    if (!topic) continue;
    // The whole recipe (every indented line). "runs X" only for a one-command recipe; a longer one is just a
    // target -- its first line alone ("echo mode: count > coverage.out") would misstate what it does.
    const recipe = [];
    for (let j = line; j < lines.length; j++) {
      const l = lines[j];
      if (tool === "make" ? l.startsWith("\t") : /^\s+\S/.test(l)) { recipe.push(l.trim().replace(/^[@-]+/, "")); continue; }
      if (!l.trim() || l.trim().startsWith("#")) { if (recipe.length) break; continue; }
      break;
    }
    const one = recipe.length === 1 && !recipe[0].endsWith("\\") && !recipe[0].includes("$(") ? recipe[0] : null;
    const where = u.path === "." ? "At the repository root" : `In ${tick(u.path)}`;
    add(u.path, topic, `${tool}:${target}`, `${where}, ${tick(`${tool} ${target}`)}${one ? ` runs ${tick(clip(one, 140))}` : " is a target"} (${f.slice(f.lastIndexOf("/") + 1)})`, f, line);
  }
  void subject;
}

function docsFacts(u, ctx, add, text, subject) {
  for (const [base, label, cmd] of [["mkdocs.yml", "MkDocs", "mkdocs build"], ["mkdocs.yaml", "MkDocs", "mkdocs build"], ["book.toml", "mdBook", "mdbook build"],
    ["docusaurus.config.js", "Docusaurus", null], ["docusaurus.config.ts", "Docusaurus", null], ["conf.py", "Sphinx", null], ["source/conf.py", "Sphinx", null], ["antora.yml", "Antora", null]]) {
    const f = at(u.path, base);
    // Only what the file shows: the site generator. The build command is the repository's choice
    // (fastapi builds its mkdocs.yml with zensical), so it is not asserted here.
    void cmd;
    if (ctx.fileSet.has(f)) add(u.path, "build", `docs:${base}`, `${cap(subject(u))} is a ${label} documentation site (${base})`, f, 1);
  }
  void text;
}

function languageFacts(u, ctx, add, addSearch) {
  const files = (ctx.unitFiles.get(u.path) || []).filter((f) => !GENERATED_FILE_RE.test(f) && !ctx.zoneOf(posixDir(f)));
  const counts = {};
  let total = 0;
  for (const f of files) {
    const ext = f.slice(f.lastIndexOf(".") + 1);
    const lang = LANG_BY_EXT[ext] || LANG_BY_EXT[ext.toLowerCase()];
    if (!lang || lang === "C/C++ header") continue;
    counts[lang] = (counts[lang] || 0) + 1;
    total++;
  }
  if (!total) return;
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const pct = (n) => Math.round((100 * n) / total);
  const parts = top.filter(([, n], i) => i === 0 || pct(n) >= 10).slice(0, 3).map(([l, n]) => `${l} (~${pct(n)}%)`);
  addSearch(u.path, "stack", "languages", `${u.path === "." ? "Source files in the repository" : `Source files in ${tick(u.path)}`} are ${parts.join(", ")} by file count (vendored and generated files excluded)`, `file extensions in ${u.path} (git ls-files)`);
  void add;
}

function testPresence(u, ctx, addSearch, subject, facts) {
  const files = ctx.unitFiles.get(u.path) || [];
  const n = files.filter((f) => TEST_FILE_RE.test(f)).length;
  const hasTestFact = facts.some((f) => f.unit === u.path && f.topic === "test");
  if (!n && !hasTestFact && u.kind !== "docs" && (files.some((f) => /\.\w+$/.test(f) && LANG_BY_EXT[f.slice(f.lastIndexOf(".") + 1)]))) {
    addSearch(u.path, "absent", "no-tests", `No test files or test configuration were found in ${subject(u)}`, `test files in ${u.path} (test_*.py, *_test.go, *.test.ts, tests/, __tests__/, spec/)`);
  }
}

function nearestUnit(dir, ctx) {
  let d = dir;
  for (;;) {
    if (ctx.unitPaths.has(d)) return d;
    if (d === ".") return ".";
    d = posixDir(d);
  }
}

function isAuxPath(p) {
  return p.split("/").slice(0, -1).some((s) => /^(test|tests|__tests__|testdata|fixtures|__fixtures__|examples|example|samples|demo|e2e|templates?|scaffolds?|boilerplate)$/i.test(s) || (s.startsWith(".") && s.length > 1 && s !== ".github"));
}
// Setup, plumbing and shell-control lines in CI: not how this repository's work is checked.
const CI_NOISE = /^(echo|cd|export|set|if|fi|then|else|done|for|do|test\s|\[|sudo|apt(-get)?\s|brew\s|curl\s|wget\s|git\s|mkdir|cp\s|mv\s|rm\s|chmod|cat\s|ls\b|pip3?\s+install|python3? -m pip install|npm (ci|install)\b|pnpm install|yarn( install)?$|corepack|docker (login|push|tag|pull)|gh\s|aws\s|gcloud\s)/;
