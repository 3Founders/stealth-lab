// Unit resolution: which parts of this repository are separate packages ("units"), how they nest, which
// are vendored or generated, and which are built from the same template. Deterministic: the same tree
// gives the same units, so facts and ids stay stable across re-surveys.
//
// Rules (docs/plan_2026-10_priors_library_survey.md §4.1, §4.6):
//  * Declared workspaces are expanded exactly, negations included. A manifest inside a declared
//    workspace of the same ecosystem that no pattern matches is NOT a unit (fixtures, nested examples).
//  * The nearest enclosing unit is a unit's parent, so the most specific declaration wins and a nested
//    workspace (an Nx project inside a pnpm workspace, a Gradle includeBuild, a Cargo workspace inside
//    a polyglot repo) becomes a child unit -- one unit per directory, never a duplicate.
//  * Bazel / Buck / Pants: a unit is a top-level package group, not every BUILD file.
//  * No manifest anywhere: a single root unit.
export const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
import path from "node:path";
import crypto from "node:crypto";
import { gitGlobToRegExp } from "./files.mjs";
import {
  cmakeInfo, gitmodulesPaths, goWorkUses, gradleSettings, jsonKeyLine, linguistPatterns, mavenModules,
  mixAppsPath, parseJsonLoose, parseToml, sbtProjects, slnProjects, tomlGet, yamlTopList, xmlTag,
} from "./parse.mjs";

const posixDir = (p) => { const d = path.posix.dirname(p); return d === "." ? "." : d; };
const join = (dir, rel) => {
  const j = path.posix.normalize(dir === "." ? rel : `${dir}/${rel}`).replace(/\/+$/, "");
  return j === "" ? "." : j.replace(/^\.\//, "");
};
const segs = (p) => (p === "." ? [] : p.split("/"));

// Directory names that hold copies of other projects, not this one. Unambiguous names count anywhere.
const VENDOR_DIRS = new Set(["third_party", "third-party", "thirdparty", "_vendor", "vendored",
  "node_modules", "bower_components", "Pods", "Carthage"]);
// Ambiguous names count only where a package manager actually puts vendored code (the Linux kernel's
// arch/*/vendor is about CPU vendors; vite's playground/external is a test package): `vendor/` next to the
// manifest of a tool that vendors into it, `subprojects/` next to meson.build, and `external/` / `deps/`
// only at the repository root.
const VENDOR_DIRS_AT_PACKAGE_ROOT = new Map([
  ["vendor", ["go.mod", "composer.json", "Gemfile", "Podfile", "Cargo.toml"]],
  ["subprojects", ["meson.build"]],
  ["external", []],
  ["deps", ["mix.exs", "rebar.config"]],
]);
// Directory names whose undeclared manifests are fixtures/examples, not packages of this repository.
const AUX_DIRS = new Set(["test", "tests", "__tests__", "testdata", "test-data", "test_data", "fixtures", "__fixtures__",
  "fixture", "examples", "example", "samples", "sample", "demo", "demos", "testing", "benchmarks", "e2e", "spec",
  // ("playground" is deliberately absent: ruff's playground/ is a real app with its own workspace.)
  "templates", "template", "scaffold", "scaffolds", "boilerplate", "integration_test", "integration_tests",
  "integration-tests"]);
// The same roles as a name suffix: `zio-examples`, `core-tests`, `test-junit-tests` (held-out set 3). A DECLARED
// member keeps its unit whatever its name; only undeclared manifests under such a folder are dropped.
const AUX_SUFFIX_RE = /[-_](tests?|specs?|fixtures?|examples?|samples?|demos?)$/i;
// Tracked build output.
const GENERATED_DIRS = new Set(["dist", "out", "__generated__", "generated", ".next", ".nuxt", "coverage", "site-packages"]);
export const GENERATED_FILE_RE = /(_pb2(_grpc)?\.pyi?|\.pb\.go|\.pb\.(cc|h)|_grpc\.pb\.go|\.g\.dart|\.freezed\.dart|\.min\.(js|css)|\.generated\.\w+|_generated\.\w+|\.designer\.cs|\.lock|lock\.json|-lock\.ya?ml|\.snap)$/i;

const BUILD_FILES = new Set(["BUILD", "BUILD.bazel", "BUCK", "TARGETS"]);
const DOCS_MARKERS = new Set(["mkdocs.yml", "mkdocs.yaml", "docusaurus.config.js", "docusaurus.config.ts", "book.toml",
  "antora.yml", "_config.yml", "hugo.toml", "hugo.yaml", "conf.py"]);
const INDEXED_BASES = new Set([...BUILD_FILES, ...DOCS_MARKERS, "pnpm-workspace.yaml", "lerna.json", "rush.json", "go.work",
  "settings.gradle", "settings.gradle.kts", "melos.yaml", "AndroidManifest.xml", "project.pbxproj", "CMakeLists.txt", "meson.build"]);

/** The ecosystem a manifest basename belongs to, or null. */
export function manifestKind(base) {
  switch (base) {
    case "package.json": return "npm";
    case "pyproject.toml": case "setup.py": case "setup.cfg": return "python";
    case "Cargo.toml": return "cargo";
    case "go.mod": return "go";
    case "pom.xml": return "maven";
    case "build.gradle": case "build.gradle.kts": return "gradle";
    case "mix.exs": return "elixir";
    case "pubspec.yaml": return "dart";
    case "Package.swift": return "swift";
    case "composer.json": return "php";
    case "deno.json": case "deno.jsonc": return "deno";
    case "build.sbt": return "sbt";
    case "project.json": return "nx";
    case "Gemfile": return "ruby";
    case "stack.yaml": return "haskell";
    case "platformio.ini": return "platformio";
    case "dbt_project.yml": return "dbt";
    default:
      if (/\.(cs|fs|vb)proj$/.test(base)) return "dotnet";
      if (/\.gemspec$/.test(base)) return "ruby";
      if (/\.cabal$/.test(base)) return "haskell";
      return null;
  }
}

/**
 * @param {string[]} files posix paths relative to the scan root
 * @param {(rel:string)=>string|null} read bounded text reader
 * @param {{submodules?: string[], previous?: Map<string,string>}} opts previous: path -> slug from the last units.idx
 */
export function resolveUnits(files, read, opts = {}) {
  const fileSet = opts.fileSet || new Set(files);
  // Only basenames the resolver looks up are indexed: at 1M files, most basenames are unique source files.
  const byBase = new Map();
  const dirs = new Set(["."]);
  for (const f of files) {
    const base = f.slice(f.lastIndexOf("/") + 1);
    if (INDEXED_BASES.has(base) || manifestKind(base) || /\.slnx?$/.test(base)) {
      let arr = byBase.get(base);
      if (!arr) byBase.set(base, (arr = []));
      arr.push(f);
    }
    let d = posixDir(f);
    while (d !== "." && !dirs.has(d)) { dirs.add(d); d = posixDir(d); }
  }
  const has = (p) => fileSet.has(p);
  const at = (dir, base) => (dir === "." ? base : `${dir}/${base}`);
  const cache = new Map();
  const text = (p) => { if (!cache.has(p)) cache.set(p, read(p)); return cache.get(p); };
  const json = (p) => { const t = text(p); return t == null ? null : parseJsonLoose(t); };
  const toml = (p) => { const t = text(p); return t == null ? null : parseToml(t); };

  // ---------------- excluded zones ----------------
  const submodules = new Set(opts.submodules || []);
  if (has(".gitmodules")) for (const p of gitmodulesPaths(text(".gitmodules") || "")) submodules.add(p.replace(/\/$/, ""));
  const lingRules = has(".gitattributes") ? linguistPatterns(text(".gitattributes") || "") : { vendored: [], generated: [] };
  const lingVendored = lingRules.vendored.map((g) => gitGlobToRegExp(g.replace(/^\//, "").replace(/\/\*\*$|\/$/, ""), g.includes("/")));
  const lingGenerated = lingRules.generated.map((g) => gitGlobToRegExp(g.replace(/^\//, "").replace(/\/\*\*$|\/$/, ""), g.includes("/")));

  const zoneCache = new Map();
  /** "vendored" | "generated" | "submodule" | null for a directory. */
  const zoneOf = (dir) => {
    if (dir === ".") return null;
    if (zoneCache.has(dir)) return zoneCache.get(dir);
    let z = zoneOf(posixDir(dir));
    if (!z) {
      const name = dir.slice(dir.lastIndexOf("/") + 1);
      if (submodules.has(dir)) z = "submodule";
      else if (VENDOR_DIRS.has(name) || lingVendored.some((re) => re.test(dir))) z = "vendored";
      else if (VENDOR_DIRS_AT_PACKAGE_ROOT.has(name)) {
        const parent = posixDir(dir);
        if (parent === "." || VENDOR_DIRS_AT_PACKAGE_ROOT.get(name).some((f) => has(at(parent, f)))) z = "vendored";
      }
      else if (GENERATED_DIRS.has(name) || lingGenerated.some((re) => re.test(dir))) z = "generated";
    }
    zoneCache.set(dir, z);
    return z;
  };
  // Fixture/example directories, and hidden tool directories (.claude/, .vercel/, .devcontainer/, ...):
  // manifests there describe tooling or copies, not packages of this repository.
  const auxSeg = (s) => AUX_DIRS.has(s.toLowerCase()) || AUX_SUFFIX_RE.test(s) || (s.startsWith(".") && s.length > 1);
  const isAux = (dir) => segs(dir).some(auxSeg);


  // ---------------- manifests ----------------
  const manifests = new Map(); // dir -> [{file, eco}]
  const addManifest = (file, eco) => {
    const d = posixDir(file);
    if (zoneOf(d)) return;
    let arr = manifests.get(d);
    if (!arr) manifests.set(d, (arr = []));
    if (!arr.some((m) => m.file === file)) arr.push({ file, eco });
  };
  for (const [base, paths] of byBase) {
    const eco = manifestKind(base);
    if (!eco) continue;
    for (const p of paths) {
      if (eco === "npm" && isMarkerPackageJson(json(p))) continue; // {"type":"module"} folder markers
      if (eco === "nx" && !isNxProject(json(p))) continue;          // project.json is a generic name (.vercel/project.json)
      if (eco === "python" && base === "setup.cfg" && (has(at(posixDir(p), "setup.py")) || has(at(posixDir(p), "pyproject.toml")))) continue;
      if (eco === "ruby" && base === "Gemfile" && (byBase.get(base) || []).length > 0 && hasGemspecIn(posixDir(p), files)) continue;
      addManifest(p, eco);
    }
  }

  // ---------------- workspace declarations ----------------
  /** @type {{root:string, eco:string, by:string, file:string, line:number, members:Set<string>}[]} */
  const workspaces = [];
  const manifestDirsOf = (eco) => [...manifests.entries()].filter(([, ms]) => ms.some((m) => m.eco === eco)).map(([d]) => d);
  const expand = (root, patterns, eco) => {
    // patterns: [{value, line}] with optional leading "!" for exclusion; matched against dirs holding a manifest of `eco`.
    const pos = [];
    const neg = [];
    for (const { value } of patterns) {
      let v = String(value).trim().replace(/^\.\//, "").replace(/\/+$/, "");
      if (!v) continue;
      const negated = v.startsWith("!");
      if (negated) v = v.slice(1).replace(/^\.\//, "");
      const full = root === "." ? v : `${root}/${v}`;
      const re = gitGlobToRegExp(path.posix.normalize(full), true);
      (negated ? neg : pos).push(re);
      // Package managers match `<pattern>/package.json`, where a trailing `**` also matches zero folders:
      // `playground/**` includes playground/ itself (vite's playground/package.json is a workspace package).
      if (/\/\*\*$/.test(full)) (negated ? neg : pos).push(gitGlobToRegExp(path.posix.normalize(full.replace(/\/\*\*$/, "")), true));
    }
    const out = new Set();
    for (const d of manifestDirsOf(eco)) {
      if (d === root) continue;
      if (neg.some((re) => re.test(d))) { excluded.add(d); continue; }
      if (pos.some((re) => re.test(d))) out.add(d);
    }
    return out;
  };
  const excluded = new Set(); // dirs a workspace explicitly negates ("!packages/legacy")
  // A workspace declared inside an example/fixture/hidden directory (axum's examples/Cargo.toml workspace)
  // describes those examples, not this repository's packages: it declares nothing.
  const declare = (root, eco, by, file, line, members) => {
    if (root !== "." && isAux(root)) return;
    workspaces.push({ root, eco, by, file, line, members });
  };

  // JS/TS: pnpm, npm/yarn/bun workspaces, lerna, rush
  for (const f of byBase.get("pnpm-workspace.yaml") || []) {
    const d = posixDir(f);
    if (zoneOf(d)) continue;
    const { items, line } = yamlTopList(text(f) || "", "packages");
    declare(d, "npm", "pnpm", f, line || 1, expand(d, items, "npm"));
  }
  for (const [d, ms] of manifests) {
    for (const m of ms) {
      if (m.eco !== "npm") continue;
      const pj = json(m.file);
      const ws = Array.isArray(pj?.workspaces) ? pj.workspaces : Array.isArray(pj?.workspaces?.packages) ? pj.workspaces.packages : null;
      if (ws) declare(d, "npm", "npm-workspaces", m.file, jsonKeyLine(text(m.file) || "", ["workspaces"]) || 1, expand(d, ws.map((v) => ({ value: v })), "npm"));
    }
  }
  for (const f of byBase.get("lerna.json") || []) {
    const d = posixDir(f);
    const lj = json(f);
    if (zoneOf(d) || !Array.isArray(lj?.packages)) continue;
    declare(d, "npm", "lerna", f, jsonKeyLine(text(f) || "", ["packages"]) || 1, expand(d, lj.packages.map((v) => ({ value: v })), "npm"));
  }
  for (const f of byBase.get("rush.json") || []) {
    const d = posixDir(f);
    const rj = json(f);
    if (zoneOf(d) || !Array.isArray(rj?.projects)) continue;
    const members = new Set(rj.projects.map((p) => join(d, String(p.projectFolder || ""))).filter((p) => manifests.has(p)));
    declare(d, "npm", "rush", f, jsonKeyLine(text(f) || "", ["projects"]) || 1, members);
  }
  // Deno workspaces: deno.json(c) `"workspace": ["./a", "./b"]` (or `{ "members": [...] }`).
  for (const base of ["deno.json", "deno.jsonc"]) {
    for (const f of byBase.get(base) || []) {
      const d = posixDir(f);
      const dj = json(f);
      const ws = Array.isArray(dj?.workspace) ? dj.workspace : Array.isArray(dj?.workspace?.members) ? dj.workspace.members : null;
      if (!ws || zoneOf(d)) continue;
      declare(d, "deno", "deno-workspace", f, jsonKeyLine(text(f) || "", ["workspace"]) || 1, expand(d, ws.map((v) => ({ value: v })), "deno"));
    }
  }
  // Rust
  for (const [d, ms] of manifests) {
    for (const m of ms) {
      if (m.eco !== "cargo") continue;
      const t = toml(m.file);
      const ws = t && tomlGet(t.data, "workspace");
      if (!ws) continue;
      const members = expand(d, (ws.members || []).map((v) => ({ value: v })), "cargo");
      for (const ex of ws.exclude || []) members.delete(join(d, ex));
      declare(d, "cargo", "cargo-workspace", m.file, t.lineOf.get("workspace") || 1, members);
    }
  }
  // Go
  for (const f of byBase.get("go.work") || []) {
    const d = posixDir(f);
    if (zoneOf(d)) continue;
    const uses = goWorkUses(text(f) || "");
    const members = new Set(uses.map((u) => join(d, u.value)).filter((p) => p !== d && (manifests.get(p) || []).some((m) => m.eco === "go")));
    declare(d, "go", "go.work", f, uses[0]?.line || 1, members);
  }
  // Python: uv / hatch / pdm workspaces
  for (const [d, ms] of manifests) {
    for (const m of ms) {
      if (m.eco !== "python" || !m.file.endsWith("pyproject.toml")) continue;
      const t = toml(m.file);
      if (!t) continue;
      for (const key of ["tool.uv.workspace", "tool.hatch.workspace", "tool.pdm.workspace"]) {
        const ws = tomlGet(t.data, key);
        if (!ws) continue;
        const members = expand(d, [...(ws.members || []).map((v) => ({ value: v })), ...(ws.exclude || []).map((v) => ({ value: "!" + v }))], "python");
        declare(d, "python", key.split(".")[1] + "-workspace", m.file, t.lineOf.get(key) || 1, members);
      }
    }
  }
  // JVM: Gradle settings (+ includeBuild as nested builds), Maven modules (recursive)
  for (const base of ["settings.gradle", "settings.gradle.kts"]) {
    for (const f of byBase.get(base) || []) {
      const d = posixDir(f);
      if (zoneOf(d)) continue;
      const s = gradleSettings(text(f) || "");
      const members = new Set();
      for (const p of s.projects) {
        const dir = join(d, p.dir);
        if (dirs.has(dir)) {
          members.add(dir);
          if (!manifests.has(dir)) addManifestDir(manifests, dir, f, "gradle"); // a project with no build file of its own
        }
      }
      for (const b of s.builds) {
        const dir = join(d, b.value);
        if (dirs.has(dir)) { members.add(dir); if (!manifests.has(dir)) addManifestDir(manifests, dir, f, "gradle"); }
      }
      if (!manifests.has(d)) addManifestDir(manifests, d, f, "gradle");
      declare(d, "gradle", "gradle-settings", f, s.projects[0]?.line || s.builds[0]?.line || 1, members);
    }
  }
  for (const f of byBase.get("pom.xml") || []) {
    const d = posixDir(f);
    if (zoneOf(d)) continue;
    const mods = mavenModules(text(f) || "");
    if (!mods.length) continue;
    const members = new Set(mods.map((m) => join(d, m.value.replace(/\/pom\.xml$/, ""))).filter((p) => has(at(p, "pom.xml"))));
    declare(d, "maven", "maven-modules", f, mods[0].line, members);
  }
  // Scala: the projects a build.sbt declares by directory (a project with no build.sbt of its own is still one).
  for (const f of byBase.get("build.sbt") || []) {
    const d = posixDir(f);
    if (zoneOf(d)) continue;
    const projs = sbtProjects(text(f) || "");
    const members = new Set();
    const memberLines = new Map();   // each project is cited at its own declaration
    for (const p of projs) {
      const dir = join(d, p.value);
      if (dir === d || !dirs.has(dir) || zoneOf(dir)) continue;   // `in(file("target/root3"))` aggregates: no folder
      members.add(dir);
      memberLines.set(dir, p.line);
      if (!manifests.has(dir)) addManifestDir(manifests, dir, f, "sbt");
    }
    if (members.size) {
      declare(d, "sbt", "sbt-projects", f, Math.min(...memberLines.values()), members);
      const w = workspaces[workspaces.length - 1];
      if (w && w.file === f) w.memberLines = memberLines;   // declare() skips a build inside an example folder
    }
  }
  // .NET solutions
  for (const [base, paths] of byBase) {
    if (!/\.slnx?$/.test(base)) continue; // classic .sln and the XML .slnx format
    for (const f of paths) {
      const d = posixDir(f);
      if (zoneOf(d)) continue;
      const projs = slnProjects(text(f) || "");
      const members = new Set(projs.map((p) => posixDir(join(d, p.value))).filter((p) => manifests.has(p)));
      if (!manifests.has(d)) addManifestDir(manifests, d, f, "dotnet");
      declare(d, "dotnet", "sln", f, projs[0]?.line || 1, members);
    }
  }
  // Elixir umbrella, Dart melos / pub workspaces
  for (const f of byBase.get("mix.exs") || []) {
    const d = posixDir(f);
    const ap = mixAppsPath(text(f) || "");
    if (!ap || zoneOf(d)) continue;
    declare(d, "elixir", "mix-umbrella", f, ap.line, expand(d, [{ value: `${ap.value}/*` }], "elixir"));
  }
  for (const f of byBase.get("melos.yaml") || []) {
    const d = posixDir(f);
    const { items, line } = yamlTopList(text(f) || "", "packages");
    if (zoneOf(d)) continue;
    declare(d, "dart", "melos", f, line || 1, expand(d, items, "dart"));
  }
  for (const f of byBase.get("pubspec.yaml") || []) {
    const d = posixDir(f);
    const { items, line } = yamlTopList(text(f) || "", "workspace");
    if (items.length && !zoneOf(d)) declare(d, "dart", "pub-workspace", f, line, expand(d, items, "dart"));
  }
  // CMake: add_subdirectory targets that are real subprojects (own project() or, one level down, targets).
  if (has("CMakeLists.txt")) {
    const visit = (dir, depth) => {
      const info = cmakeInfo(text(at(dir, "CMakeLists.txt")) || "");
      const members = new Set();
      for (const s of info.subdirs) {
        const sub = join(dir, s.value);
        if (!has(at(sub, "CMakeLists.txt")) || zoneOf(sub) || isAux(sub)) continue;
        const si = cmakeInfo(text(at(sub, "CMakeLists.txt")) || "");
        if (si.project || (depth === 0 && si.targets)) {
          members.add(sub);
          addManifestDir(manifests, sub, at(sub, "CMakeLists.txt"), "cmake");
          if (depth < 1) visit(sub, depth + 1);
        }
      }
      if (!manifests.has(dir)) addManifestDir(manifests, dir, at(dir, "CMakeLists.txt"), "cmake");
      if (members.size) declare(dir, "cmake", "cmake-subdirectories", at(dir, "CMakeLists.txt"), info.subdirs[0]?.line || 1, members);
    };
    visit(".", 0);
  } else if (has("meson.build") && !manifests.has(".")) {
    addManifestDir(manifests, ".", "meson.build", "meson");
  }

  // Bazel / Buck / Pants: top-level package groups.
  const monoBuild = has("WORKSPACE") || has("WORKSPACE.bazel") || has("MODULE.bazel") || has(".buckconfig") || has("pants.toml");
  if (monoBuild) {
    const tool = has("pants.toml") ? "pants" : has(".buckconfig") ? "buck" : "bazel";
    const buildDirs = [];
    for (const b of BUILD_FILES) for (const f of byBase.get(b) || []) { const d = posixDir(f); if (!zoneOf(d)) buildDirs.push(d); }
    const groupOf = (d, depth) => segs(d).slice(0, depth).join("/") || ".";
    let counts = new Map();
    for (const d of buildDirs) { const g = groupOf(d, 1); counts.set(g, (counts.get(g) || 0) + 1); }
    // One container directory (src/, java/, projects/) holding most packages: group one level deeper.
    const total = buildDirs.length;
    const top = [...counts.entries()].filter(([g]) => g !== ".").sort((a, b) => b[1] - a[1])[0];
    const deeper = new Set();
    if (top && total >= 10 && top[1] / total > 0.8) {
      const inner = new Set(buildDirs.filter((d) => d.startsWith(top[0] + "/")).map((d) => groupOf(d, 2)));
      if (inner.size >= 2) deeper.add(top[0]);
    }
    const members = new Set();
    for (const d of buildDirs) {
      const g1 = groupOf(d, 1);
      const g = deeper.has(g1) ? groupOf(d, 2) : g1;
      if (g === "." || isAux(g)) continue;
      members.add(g);
      if (!manifests.has(g)) addManifestDir(manifests, g, firstBuildFile(g, buildDirs, byBase, at) || at(g, "BUILD"), tool);
      else if (!manifests.get(g).some((m) => m.eco === tool)) manifests.get(g).push({ file: firstBuildFile(g, buildDirs, byBase, at) || at(g, "BUILD"), eco: tool });
    }
    const rootFile = ["MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel", ".buckconfig", "pants.toml"].find(has);
    addManifestDir(manifests, ".", rootFile, tool);
    declare(".", tool, `${tool}-packages`, rootFile, 1, members);
  }

  // ---------------- unit set ----------------
  const declaredMembers = new Map(); // dir -> [{by,file,line,root}]
  for (const w of workspaces) for (const m of w.members) {
    let arr = declaredMembers.get(m);
    if (!arr) declaredMembers.set(m, (arr = []));
    arr.push({ by: w.by, file: w.file, line: w.memberLines?.get(m) ?? w.line, root: w.root, eco: w.eco });
  }
  // A manifest inside a declared workspace of the same ecosystem that no pattern matched is not a unit.
  const insideSameEcoWorkspace = (dir, ecos) => workspaces.some((w) =>
    ecos.includes(w.eco) && w.root !== dir && (w.root === "." || dir.startsWith(w.root + "/")) && !w.members.has(dir));
  const JVM = ["gradle", "maven"];

  const units = new Map(); // dir -> unit
  const aux = [];
  for (const [dir, ms] of manifests) {
    const ecos = [...new Set(ms.map((m) => m.eco))];
    const declared = declaredMembers.get(dir) || [];
    if (dir !== "." && !declared.length) {
      if (isAux(dir)) { aux.push({ path: dir, reason: "fixture-or-example", manifests: ms.map((m) => m.file) }); continue; }
      const sameEco = insideSameEcoWorkspace(dir, ecos.flatMap((e) => (JVM.includes(e) ? JVM : [e])));
      // A real npm package the workspace just doesn't list (ruff playground/api: a name and scripts of its own)
      // is still a package; one the workspace explicitly negates, or a nameless/scriptless one, is not.
      const pj = ecos.includes("npm") ? json(ms.find((m) => m.eco === "npm").file) : null;
      const realPackage = pj && typeof pj.name === "string" && pj.scripts && Object.keys(pj.scripts).length > 0;
      if (sameEco && !ecos.includes("nx") && (excluded.has(dir) || !realPackage)) {
        aux.push({ path: dir, reason: excluded.has(dir) ? "excluded-by-workspace" : "undeclared-in-workspace", manifests: ms.map((m) => m.file) });
        continue;
      }
    }
    units.set(dir, { path: dir, ecosystems: ecos, manifests: ms.map((m) => m.file), declaredBy: declared, kind: "package" });
  }

  // Fixture batches: five or more undeclared sibling directories, each with the same kind of manifest, inside
  // another package (ruff's crates/ty_completion_eval/truth/<case>/pyproject.toml) are test cases, not packages.
  {
    const nearestUnitAbove = (d) => { let p = posixDir(d); for (;;) { if (units.has(p)) return p; if (p === ".") return "."; p = posixDir(p); } };
    const groups = new Map();
    for (const [dir, u] of units) {
      if (dir === "." || u.declaredBy.length) continue;
      const host = nearestUnitAbove(dir);
      if (host === ".") continue; // top-level folders of the repository (services/a, services/b, ...) are real packages
      const key = `${posixDir(dir)}|${u.manifests.map((m) => m.slice(m.lastIndexOf("/") + 1)).sort().join(",")}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(dir);
    }
    for (const dirs of groups.values()) {
      if (dirs.length < 5) continue;
      for (const dir of dirs) {
        aux.push({ path: dir, reason: "fixture-batch", manifests: units.get(dir).manifests });
        units.delete(dir);
      }
    }
  }

  // Special unit kinds (only where no package unit already covers the directory as its own root).
  const special = (dir, kind) => {
    if (zoneOf(dir)) return;
    const u = units.get(dir);
    if (u) { if (u.kind === "package" && dir !== ".") u.kind = kind; u.tags = [...new Set([...(u.tags || []), kind])]; return; }
    if (isAux(dir) && kind !== "docs") return;
    units.set(dir, { path: dir, ecosystems: [], manifests: [], declaredBy: [], kind });
  };
  for (const base of DOCS_MARKERS) {
    for (const f of byBase.get(base) || []) {
      const d = posixDir(f);
      // Sphinx: a conf.py anywhere under a doc/ or docs/ folder (docs/, docs/source, pytest's doc/en).
      if (base === "conf.py" && !segs(d).some((s) => /^docs?$/.test(s))) continue;
      if (base === "_config.yml" && !segs(d).some((s) => /^docs?$/.test(s)) && d !== ".") continue;
      if (d === ".") { units.get(".") && (units.get(".").tags = [...new Set([...(units.get(".").tags || []), "docs"])]); continue; }
      special(base === "conf.py" && d.endsWith("/source") ? posixDir(d) : d, "docs");
    }
  }
  for (const f of files) {
    if (!f.includes(".vitepress/")) continue;
    const m = f.match(/^(.*?)\/?\.vitepress\/config\.\w+$/);
    if (m) { if (m[1]) special(m[1], "docs"); else if (units.get(".")) units.get(".").tags = [...new Set([...(units.get(".").tags || []), "docs"])]; }
  }
  for (const f of byBase.get("AndroidManifest.xml") || []) {
    const parts = segs(posixDir(f));
    const i = parts.lastIndexOf("android");
    if (i >= 0) special(parts.slice(0, i + 1).join("/"), "mobile");
  }
  for (const f of byBase.get("project.pbxproj") || []) {
    const parts = segs(posixDir(f));
    const i = parts.findIndex((s) => s.endsWith(".xcodeproj"));
    if (i >= 0) special(parts.slice(0, i).join("/") || ".", "mobile");
  }
  for (const f of byBase.get("platformio.ini") || []) special(posixDir(f), "embedded");
  for (const f of byBase.get("dbt_project.yml") || []) special(posixDir(f), "data");
  {
    const nb = new Map();
    for (const f of files) if (f.endsWith(".ipynb")) { const d = posixDir(f); nb.set(d, (nb.get(d) || 0) + 1); }
    const nbDirs = [...nb.entries()].filter(([, n]) => n >= 3).map(([d]) => d).sort();
    for (const d of nbDirs) {
      if (nbDirs.some((o) => o !== d && d.startsWith(o + "/"))) continue; // only the top-most notebook dir
      if (!units.has(d) && d !== ".") special(d, "notebooks");
      else if (d === "." && units.has(".")) units.get(".").tags = [...new Set([...(units.get(".").tags || []), "notebooks"])];
    }
  }

  // A Flutter or React Native app's platform runners (android/, ios/, macos/, linux/, windows/, web/) are part
  // of that app -- their Gradle/Xcode projects are generated hosts, not separate packages.
  const PLATFORM_DIRS = ["android", "ios", "macos", "linux", "windows", "web"];
  const crossPlatform = [...units.values()].filter((u) => u.manifests.some((m) => {
    const base = m.slice(m.lastIndexOf("/") + 1);
    if (base === "pubspec.yaml") return /sdk:\s*flutter/.test(text(m) || "");
    if (base === "package.json") { const pj = json(m); return Boolean(pj && (hasAnyDep(pj, "react-native") || hasAnyDep(pj, "expo"))); }
    return false;
  })).map((u) => u.path);
  for (const app of crossPlatform) {
    const prefixes = PLATFORM_DIRS.map((p) => (app === "." ? p : `${app}/${p}`));
    for (const d of [...units.keys()]) {
      if (prefixes.some((p) => d === p || d.startsWith(p + "/"))) {
        units.delete(d);
        aux.push({ path: d, reason: `platform-runner-of:${app}`, manifests: [] });
      }
    }
  }

  // Root unit always exists.
  if (!units.has(".")) units.set(".", { path: ".", ecosystems: [], manifests: [], declaredBy: [], kind: "root" });
  const root = units.get(".");
  if (root.kind === "package") root.kind = "root";
  if (!root.ecosystems.length && units.size === 1 && looksLikeDotfiles(files)) root.tags = [...new Set([...(root.tags || []), "dotfiles"])];

  // Excluded zones as units (one fact each, never surveyed).
  const zones = new Map();
  for (const d of dirs) {
    const z = zoneOf(d);
    if (z && !zoneOf(posixDir(d))) zones.set(d, z);
  }
  for (const s of submodules) if (!zones.has(s)) zones.set(s, "submodule");

  // ---------------- parents, names, slugs, templates ----------------
  const sorted = [...units.keys()].sort((a, b) => (a === "." ? -1 : b === "." ? 1 : cmpStr(a, b)));
  const parentOf = (dir) => {
    let d = dir;
    while (d !== ".") { d = posixDir(d); if (units.has(d)) return d; }
    return null;
  };
  const prev = opts.previous || new Map();
  const usedSlugs = new Set([...prev.values()]);
  const out = [];
  for (const dir of sorted) {
    const u = units.get(dir);
    u.parent = dir === "." ? null : parentOf(dir);
    const named = unitName(u, json, toml, text);
    u.name = named || (dir === "." ? "root" : dir.slice(dir.lastIndexOf("/") + 1));
    if (!named) u.nameFromPath = true;   // no manifest names it: facts must not claim a name
    u.slug = prev.get(dir) || mintSlug(dir, usedSlugs);
    usedSlugs.add(u.slug);
    out.push(u);
  }
  // Templates: units built the same way (same tooling, scripts and config files) share a signature.
  const own = assignFiles(files, [...units.keys()]);
  for (const u of out) u.template = u.path === "." ? null : templateOf(u, own.get(u.path) || [], json, toml, text);
  const clusters = new Map();
  for (const u of out) if (u.template) { let a = clusters.get(u.template); if (!a) clusters.set(u.template, (a = [])); a.push(u); }
  for (const members of clusters.values()) {
    if (members.length < 2) continue;
    const rep = members.map((m) => m.path).sort()[0];
    for (const m of members) if (m.path !== rep) m.inherits = rep;
  }
  return {
    units: out,
    workspaces: workspaces.map((w) => ({ root: w.root, eco: w.eco, by: w.by, file: w.file, line: w.line, members: [...w.members].sort() })),
    zones: [...zones.entries()].map(([p, kind]) => ({ path: p, kind })).sort((a, b) => cmpStr(a.path, b.path)),
    aux,
    zoneOf,
    own,
  };
}

function addManifestDir(manifests, dir, file, eco) {
  let arr = manifests.get(dir);
  if (!arr) manifests.set(dir, (arr = []));
  if (!arr.some((m) => m.eco === eco)) arr.push({ file, eco });
}

function firstBuildFile(group, buildDirs, byBase, at) {
  const d = buildDirs.filter((x) => x === group || x.startsWith(group + "/")).sort((a, b) => a.length - b.length || cmpStr(a, b))[0];
  if (!d) return null;
  for (const b of BUILD_FILES) if ((byBase.get(b) || []).includes(at(d, b))) return at(d, b);
  return null;
}

function hasGemspecIn(dir, files) {
  const prefix = dir === "." ? "" : dir + "/";
  return files.some((f) => f.startsWith(prefix) && !f.slice(prefix.length).includes("/") && f.endsWith(".gemspec"));
}

function isMarkerPackageJson(pj) {
  if (!pj || typeof pj !== "object") return true;
  const keys = Object.keys(pj).filter((k) => !["type", "sideEffects", "private", "main", "module", "types", "exports"].includes(k));
  return keys.length === 0;
}

function hasAnyDep(pj, name) {
  return [pj.dependencies, pj.devDependencies, pj.peerDependencies].some((s) => s && typeof s === "object" && name in s);
}

function isNxProject(pj) {
  if (!pj || typeof pj !== "object") return false;
  return Boolean(pj.targets || pj.projectType || pj.sourceRoot || pj.executors || (typeof pj.$schema === "string" && /nx/.test(pj.$schema)));
}

function looksLikeDotfiles(files) {
  const top = new Set(files.map((f) => f.split("/")[0]));
  const dot = [...top].filter((t) => t.startsWith(".") && t !== ".github" && t !== ".gitignore" && t !== ".gitattributes");
  const known = ["zsh", "nvim", "vim", "tmux", "bash", "git", "kitty", "alacritty", "i3", "sway", "hypr", "fish", "wezterm"];
  return dot.length >= 3 || [...top].filter((t) => known.includes(t.toLowerCase())).length >= 2;
}

function unitName(u, json, toml, text) {
  for (const f of u.manifests) {
    const base = f.slice(f.lastIndexOf("/") + 1);
    if (base === "package.json" || base === "composer.json" || base === "project.json" || base.startsWith("deno.json")) {
      const n = json(f)?.name;
      if (typeof n === "string" && n) return n;
    } else if (base === "mix.exs") {
      const m = (text(f) || "").match(/\bapp:\s*:(\w+)/); // `app: :ecto` (held-out ecto was named "root")
      if (m) return m[1];
    } else if (base === "pyproject.toml") {
      const t = toml(f);
      const n = t && (tomlGet(t.data, "project.name") || tomlGet(t.data, "tool.poetry.name"));
      if (n) return n;
    } else if (base === "Cargo.toml") {
      const t = toml(f);
      const n = t && tomlGet(t.data, "package.name");
      if (n) return n;
    } else if (base === "go.mod") {
      const m = (text(f) || "").match(/^module\s+(\S+)/m);
      if (m) return m[1];
    } else if (base === "pom.xml") {
      const a = xmlTag((text(f) || "").replace(/<parent>[\s\S]*?<\/parent>/, ""), "artifactId");
      if (a) return a.value;
    } else if (base === "pubspec.yaml") {
      const m = (text(f) || "").match(/^name:\s*(\S+)/m);
      if (m) return m[1];
    } else if (/\.(cs|fs|vb)proj$|\.gemspec$|\.cabal$/.test(base)) {
      return base.replace(/\.[^.]+$/, "");
    } else if (base === "setup.py") {
      const m = (text(f) || "").match(/name\s*=\s*["']([^"']+)["']/);
      if (m) return m[1];
    }
  }
  return null;
}

function mintSlug(dir, used) {
  if (dir === ".") return "root";
  const clean = (s) => s.toLowerCase().replace(/^@/, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "u";
  const parts = segs(dir);
  for (let k = 1; k <= parts.length; k++) {
    const s = clean(parts.slice(-k).join("-")).slice(0, 40);
    if (s !== "root" && !used.has(s)) return s;
  }
  const base = clean(dir).slice(0, 34);
  for (let n = 2; ; n++) if (!used.has(`${base}-${n}`)) return `${base}-${n}`;
}

// Normalised signature of "how this unit is built": ecosystems, kind, tooling dependencies, script
// commands (with the unit's own name and path removed), config file names, and top-level layout.
// Runtime dependency names are left out on purpose: sibling packages differ in what they depend on
// but are built, tested and linted the same way, which is what a surveyed representative can stand for.
const TOOLING = /^(@types\/|typescript$|ts-node|tsx$|vitest|jest|@jest\/|mocha|chai|ava$|eslint|@eslint|prettier|@biomejs|biome$|rollup|vite$|webpack|esbuild|@swc|swc$|babel|@babel|tsup|unbuild|turbo$|nx$|@nx\/|lerna|rimraf|pytest|ruff|black|mypy|flake8|isort|tox|nox|coverage|hatch|setuptools|poetry|maturin|criterion|clap$|tokio$|serde$|react$|vue$|svelte$|next$|nuxt|@angular\/core|express$|fastify$|django$|flask$|fastapi$|spring-boot)/;
function templateOf(u, files, json, toml, text) {
  const sig = { kind: u.kind, eco: [...u.ecosystems].sort() };
  const prefix = u.path + "/";
  const nameRe = u.name && u.name.length >= 3 ? new RegExp(`(?<![\\w@/-])${u.name.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}(?![\\w-])`, "g") : null;
  const subst = (s) => {
    let v = String(s).split(u.path).join("<dir>");
    if (nameRe) v = v.replace(nameRe, "<name>");
    return v;
  };
  for (const f of u.manifests) {
    const base = f.slice(f.lastIndexOf("/") + 1);
    if (base === "package.json") {
      const pj = json(f) || {};
      sig.scripts = Object.fromEntries(Object.entries(pj.scripts || {}).sort().map(([k, v]) => [k, subst(v)]));
      sig.tools = Object.keys({ ...(pj.devDependencies || {}), ...(pj.dependencies || {}), ...(pj.peerDependencies || {}) }).filter((d) => TOOLING.test(d)).sort();
      sig.type = pj.type || null;
    } else if (base === "pyproject.toml") {
      const t = toml(f);
      const deps = [...(tomlGet(t?.data || {}, "project.dependencies") || []), ...Object.values(tomlGet(t?.data || {}, "project.optional-dependencies") || {}).flat()];
      sig.tools = [...new Set(deps.map((d) => String(d).split(/[\s<>=!~;[]/)[0].toLowerCase()).filter((d) => TOOLING.test(d)))].sort();
      sig.build = tomlGet(t?.data || {}, "build-system.build-backend") || null;
      sig.toolSections = Object.keys(tomlGet(t?.data || {}, "tool") || {}).sort();
    } else if (base === "Cargo.toml") {
      const t = toml(f);
      sig.tools = Object.keys({ ...(tomlGet(t?.data || {}, "dependencies") || {}), ...(tomlGet(t?.data || {}, "dev-dependencies") || {}) }).filter((d) => TOOLING.test(d)).sort();
      sig.lib = Boolean(tomlGet(t?.data || {}, "lib")) || files.includes(`${prefix}src/lib.rs`);
    }
  }
  const children = new Set();
  const configs = new Set();
  for (const f of files) {
    if (!f.startsWith(prefix)) continue;
    const rest = f.slice(prefix.length);
    const slash = rest.indexOf("/");
    if (slash < 0) {
      if (/^(tsconfig.*\.json|vite\.config\.\w+|vitest\.config\.\w+|jest\.config\.\w+|\.eslintrc.*|eslint\.config\.\w+|babel\.config\.\w+|rollup\.config\.\w+|tsup\.config\.\w+|pytest\.ini|tox\.ini|setup\.cfg|Makefile|Dockerfile|README.*|\.npmignore|project\.json)$/.test(rest)) configs.add(rest.replace(/^README.*/, "README"));
    } else children.add(rest.slice(0, slash));
  }
  sig.configs = [...configs].sort();
  sig.layout = [...children].filter((c) => /^(src|lib|test|tests|__tests__|spec|bin|cmd|internal|pkg|app|include|benches|examples)$/.test(c)).sort();
  return "t-" + crypto.createHash("sha256").update(JSON.stringify(sig)).digest("hex").slice(0, 8);
}

/** Map every file to the nearest enclosing unit (posix dir lookups only, no I/O). */
export function assignFiles(files, unitPaths) {
  const set = new Set(unitPaths);
  const memo = new Map();
  const unitOfDir = (d) => {
    if (set.has(d)) return d;
    if (d === ".") return ".";
    if (memo.has(d)) return memo.get(d);
    const u = unitOfDir(posixDir(d));
    memo.set(d, u);
    return u;
  };
  const out = new Map();
  for (const f of files) {
    const u = unitOfDir(posixDir(f));
    let arr = out.get(u);
    if (!arr) out.set(u, (arr = []));
    arr.push(f);
  }
  return out;
}
