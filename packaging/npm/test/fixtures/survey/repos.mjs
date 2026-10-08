// Fixture generator for `stealthlab-mcp survey`: one small repository per repo shape in
// docs/plan_2026-10_priors_library_survey.md §4.1, plus the §4.6 edge cases. Each fixture is a plain
// {path: content} map; `makeRepo` writes it to a throwaway directory and (optionally) commits it to git.
// Every fixture states the units it MUST resolve to (`units`) -- the ground truth the tests check.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const created = [];
process.on("exit", () => {
  for (const d of created) { try { fs.rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ } }
});

export function tmpDir(prefix = "sl-survey-") {
  const d = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
  created.push(d);
  return d;
}

const GIT_ENV = { ...process.env, GIT_AUTHOR_NAME: "t", GIT_AUTHOR_EMAIL: "t@example.com", GIT_COMMITTER_NAME: "t",
  GIT_COMMITTER_EMAIL: "t@example.com", GIT_CONFIG_NOSYSTEM: "1" };
export function git(dir, ...args) {
  return gitEnv(dir, {}, ...args);
}

function gitEnv(dir, extraEnv, ...args) {
  return execFileSync("git", ["-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", "-c", "protocol.file.allow=always", ...args],
    { cwd: dir, env: { ...GIT_ENV, ...extraEnv }, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

/** Write files (content string or Buffer) under dir. */
export function writeFiles(dir, files) {
  for (const [rel, content] of Object.entries(files)) {
    const p = path.join(dir, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, content);
  }
}

/**
 * @param {Record<string,string>} files
 * @param {{git?: boolean, remote?: string, commits?: {message:string, files:Record<string,string>, date?:string}[]}} opts
 */
export function makeRepo(files, opts = {}) {
  const dir = tmpDir();
  writeFiles(dir, files);
  if (opts.git !== false) {
    git(dir, "init", "-q");
    git(dir, "add", "-A");
    git(dir, "commit", "-qm", "initial", "--allow-empty");
    if (opts.remote) git(dir, "remote", "add", "origin", opts.remote);
    for (const c of opts.commits || []) {
      writeFiles(dir, c.files);
      git(dir, "add", "-A");
      const env = c.date ? ["--date", c.date] : [];
      // `date` is the commit's date: author AND committer, as in a real old history (git log --since reads the latter).
      gitEnv(dir, c.date ? { GIT_COMMITTER_DATE: c.date } : {}, "commit", "-qm", c.message, ...env);
    }
  }
  return dir;
}

const pj = (o) => JSON.stringify(o, null, 2) + "\n";
const lib = (name, extra = {}) => pj({ name, version: "1.0.0", scripts: { build: "tsup src/index.ts", test: "vitest run" },
  devDependencies: { typescript: "^5.4.0", vitest: "^1.6.0", tsup: "^8.0.0" }, ...extra });

/** name -> {files, units: [expected unit paths], note} */
export const SHAPES = {
  "single-npm": {
    files: { "package.json": pj({ name: "solo", description: "A single package for testing.", scripts: { test: "jest", build: "tsc -p ." },
      engines: { node: ">=20" }, devDependencies: { jest: "^29.0.0", typescript: "^5.4.0" } }), "package-lock.json": "{}\n",
      "src/index.ts": "export const x = 1;\n", "src/index.test.ts": "test('x', () => {});\n", ".nvmrc": "20.11.0\n" },
    units: ["."],
  },
  "pnpm-negation": {
    files: { "package.json": pj({ name: "mono", private: true, scripts: { test: "turbo run test" }, devDependencies: { turbo: "^2.0.0" } }),
      "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n  - 'apps/*'\n  - '!packages/legacy'\n", "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
      "packages/ui/package.json": lib("@acme/ui"), "packages/ui/src/index.ts": "export {};\n",
      "packages/core/package.json": lib("@acme/core"), "packages/core/src/index.ts": "export {};\n", "packages/core/test/core.test.ts": "test('c', () => {});\n",
      "packages/legacy/package.json": lib("@acme/legacy"),
      "apps/web/package.json": pj({ name: "web", scripts: { dev: "next dev", build: "next build" }, dependencies: { next: "14.2.3", react: "18.3.1" } }),
      "packages/ui/test/fixture/package.json": pj({ name: "fixture-pkg", scripts: { test: "x" } }),
      "packages/ui/esm/package.json": pj({ type: "module" }) },
    units: [".", "apps/web", "packages/core", "packages/ui"],
  },
  "npm-workspaces-nx": {
    files: { "package.json": pj({ name: "root", workspaces: { packages: ["libs/*"] } }), "package-lock.json": "{}\n", "nx.json": "{}\n",
      "libs/a/package.json": lib("a"), "libs/a/project.json": pj({ name: "a", targets: { build: {} } }),
      "tools/gen/project.json": pj({ name: "gen", projectType: "library", sourceRoot: "tools/gen/src" }),
      ".vercel/project.json": pj({ projectId: "x", orgId: "y" }) },
    units: [".", "libs/a", "tools/gen"],
  },
  "lerna-rush": {
    files: { "lerna.json": pj({ packages: ["modules/*"] }), "package.json": pj({ name: "l", private: true }),
      "modules/x/package.json": lib("x"), "rush.json": pj({ projects: [{ packageName: "y", projectFolder: "rushy/y" }] }),
      "rushy/y/package.json": lib("y") },
    units: [".", "modules/x", "rushy/y"],
  },
  "cargo-workspace": {
    files: { "Cargo.toml": "[workspace]\nmembers = [\n  \"crates/*\",\n]\nexclude = [\"crates/skip\"]\n\n[workspace.dependencies]\nserde = \"1\"\n",
      "crates/core/Cargo.toml": "[package]\nname = \"core\"\nversion = \"0.1.0\"\nedition = \"2021\"\nrust-version = \"1.75\"\n\n[dependencies]\ntokio = { version = \"1\", features = [\"full\"] }\n",
      "crates/core/src/lib.rs": "pub fn x() {}\n", "crates/cli/Cargo.toml": "[package]\nname = \"cli\"\nversion = \"0.1.0\"\nedition = \"2021\"\n\n[[bin]]\nname = \"acme\"\npath = \"src/main.rs\"\n",
      "crates/cli/src/main.rs": "fn main() {}\n", "crates/skip/Cargo.toml": "[package]\nname = \"skip\"\nversion = \"0.1.0\"\n", "rust-toolchain.toml": "[toolchain]\nchannel = \"1.78.0\"\n" },
    units: [".", "crates/cli", "crates/core"],
  },
  "go-work": {
    files: { "go.work": "go 1.22\n\nuse (\n\t./svc/api\n\t./lib/shared\n)\n", "svc/api/go.mod": "module example.com/api\n\ngo 1.22\n\nrequire github.com/gin-gonic/gin v1.9.1\n",
      "svc/api/cmd/apid/main.go": "package main\nfunc main(){}\n", "lib/shared/go.mod": "module example.com/shared\n\ngo 1.21\n",
      "lib/shared/x_test.go": "package shared\n", "tools/go.mod": "module example.com/tools\n\ngo 1.22\n" },
    // tools/go.mod is not in go.work: declarations are exact, so it is an AUX module, not a unit.
    units: [".", "lib/shared", "svc/api"],
  },
  "python-uv-workspace": {
    files: { "pyproject.toml": "[project]\nname = \"ws\"\nversion = \"0\"\nrequires-python = \">=3.11\"\n\n[tool.uv.workspace]\nmembers = [\"packages/*\"]\nexclude = [\"packages/old\"]\n\n[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n\n[tool.ruff]\nline-length = 100\n",
      "uv.lock": "version = 1\n", "packages/api/pyproject.toml": "[project]\nname = \"api\"\nversion = \"0\"\ndependencies = [\n  \"fastapi>=0.110\",\n  \"pydantic>=2\",\n]\n",
      "packages/api/api/__init__.py": "", "packages/old/pyproject.toml": "[project]\nname = \"old\"\nversion = \"0\"\n", "tests/test_x.py": "def test_x():\n    pass\n" },
    units: [".", "packages/api"],
  },
  "python-several": {
    files: { "service/pyproject.toml": "[project]\nname = \"service\"\nversion = \"0\"\ndependencies = [\"django>=5.0\"]\n", "service/manage.py": "",
      "worker/setup.py": "from setuptools import setup\nsetup(name=\"worker\", python_requires=\">=3.10\")\n", "worker/worker.py": "" },
    units: [".", "service", "worker"],
  },
  "gradle-multi": {
    files: { "settings.gradle.kts": "rootProject.name = \"g\"\ninclude(\":app\", \":lib:util\")\nproject(\":lib:util\").projectDir = file(\"libraries/util\")\nincludeBuild(\"build-logic\")\n",
      "build.gradle.kts": "plugins { id(\"java\") }\n", "gradlew": "#!/bin/sh\n", "app/build.gradle.kts": "plugins {\n  id(\"org.springframework.boot\") version \"3.2.0\"\n}\njava { toolchain { languageVersion.set(JavaLanguageVersion.of(21)) } }\n",
      "libraries/util/build.gradle.kts": "plugins { `java-library` }\n", "build-logic/settings.gradle.kts": "rootProject.name = \"build-logic\"\n",
      "build-logic/build.gradle.kts": "plugins { `kotlin-dsl` }\n", "buildSrc/build.gradle.kts": "" },
    units: [".", "app", "build-logic", "libraries/util"],
  },
  "maven-nested": {
    files: { "pom.xml": "<project>\n  <artifactId>parent</artifactId>\n  <modules>\n    <module>core</module>\n    <module>web</module>\n  </modules>\n</project>\n",
      "core/pom.xml": "<project>\n  <artifactId>core</artifactId>\n  <properties>\n    <maven.compiler.release>17</maven.compiler.release>\n  </properties>\n</project>\n",
      "web/pom.xml": "<project>\n  <artifactId>web</artifactId>\n  <modules>\n    <module>web-api</module>\n  </modules>\n</project>\n", "web/web-api/pom.xml": "<project>\n  <artifactId>web-api</artifactId>\n</project>\n",
      "tools/pom.xml": "<project><artifactId>orphan</artifactId></project>\n" },
    units: [".", "core", "web", "web/web-api"],
  },
  "dotnet-sln": {
    files: { "App.sln": "Microsoft Visual Studio Solution File\nProject(\"{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}\") = \"Api\", \"src\\Api\\Api.csproj\", \"{1}\"\nEndProject\nProject(\"{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}\") = \"Api.Tests\", \"tests\\Api.Tests\\Api.Tests.csproj\", \"{2}\"\nEndProject\n",
      "src/Api/Api.csproj": "<Project Sdk=\"Microsoft.NET.Sdk.Web\">\n  <PropertyGroup>\n    <TargetFramework>net8.0</TargetFramework>\n  </PropertyGroup>\n</Project>\n",
      "tests/Api.Tests/Api.Tests.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\">\n  <ItemGroup>\n    <PackageReference Include=\"xunit\" Version=\"2.6.0\" />\n  </ItemGroup>\n</Project>\n", "global.json": pj({ sdk: { version: "8.0.100" } }) },
    units: [".", "src/Api", "tests/Api.Tests"],
  },
  "bazel": {
    files: { "MODULE.bazel": "module(name = \"m\")\n", ".bazelversion": "7.1.0\n", "BUILD.bazel": "", "services/a/BUILD.bazel": "", "services/a/x/BUILD.bazel": "",
      "services/b/BUILD": "", "libs/c/BUILD.bazel": "", "libs/c/d/e/BUILD.bazel": "", "third_party/z/BUILD": "" },
    units: [".", "libs", "services"],
  },
  "bazel-container": {
    files: { "WORKSPACE": "", ...Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`src/pkg${i % 4}/m${i}/BUILD`, ""])) },
    units: [".", "src/pkg0", "src/pkg1", "src/pkg2", "src/pkg3"],
  },
  "cmake": {
    files: { "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(top CXX)\nset(CMAKE_CXX_STANDARD 20)\nenable_testing()\nadd_subdirectory(engine)\nadd_subdirectory(src)\nadd_subdirectory(tests)\n",
      "engine/CMakeLists.txt": "project(engine)\nadd_library(engine e.cpp)\n", "src/CMakeLists.txt": "add_executable(app main.cpp)\n", "tests/CMakeLists.txt": "add_executable(t t.cpp)\n" },
    units: [".", "engine", "src"],
  },
  "elixir-umbrella-melos": {
    files: { "mix.exs": "defmodule U.MixProject do\n  def project, do: [apps_path: \"apps\", elixir: \"~> 1.15\"]\nend\n", "apps/web/mix.exs": "defmodule Web do\n  {:phoenix, \"~> 1.7\"}\nend\n",
      "apps/core/mix.exs": "defmodule Core do\nend\n", "dart/melos.yaml": "name: d\npackages:\n  - packages/**\n", "dart/pubspec.yaml": "name: d\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n",
      "dart/packages/a/pubspec.yaml": "name: a\n" },
    units: [".", "apps/core", "apps/web", "dart", "dart/packages/a"],
  },
  "polyglot": {
    files: { "backend/pyproject.toml": "[project]\nname = \"backend\"\nversion = \"0\"\nrequires-python = \">=3.12\"\n", "backend/app/__init__.py": "",
      "frontend/package.json": pj({ name: "frontend", scripts: { build: "vite build", test: "vitest" }, devDependencies: { vite: "^5", vitest: "^1" } }), "frontend/pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
      "packaging/npm/package.json": pj({ name: "cli", bin: { cli: "bin/cli.mjs" } }), "packaging/pyproject.toml": "[project]\nname = \"connect\"\nversion = \"0\"\n",
      "Makefile": "test:\n\tcd backend && pytest -q\n\nlint:\n\truff check .\n", ".github/workflows/ci.yml": "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.12'\n      - run: pip install -e backend\n      - run: pytest -q\n        working-directory: backend\n" },
    units: [".", "backend", "frontend", "packaging", "packaging/npm"],
  },
  "zones": {
    files: { "package.json": pj({ name: "z", scripts: { test: "node t.js" } }), "vendor/lib/package.json": pj({ name: "vendored", scripts: { x: "y" } }),
      "third_party/foo/Cargo.toml": "[package]\nname = \"foo\"\n", "dist/package.json": pj({ name: "built" }), "gen/api_pb2.py": "# Code generated by protoc. DO NOT EDIT.\n",
      ".gitattributes": "generated/** linguist-generated\nextern/** linguist-vendored\n", "extern/x/go.mod": "module x\n", "generated/y/package.json": pj({ name: "gen-y" }) },
    units: ["."],
    zones: ["dist", "extern", "generated", "third_party", "vendor"],
  },
  "special-kinds": {
    files: { "docs/mkdocs.yml": "site_name: d\n", "notebooks/a.ipynb": "{}", "notebooks/b.ipynb": "{}", "notebooks/c.ipynb": "{}",
      "mobile/android/app/src/main/AndroidManifest.xml": "<manifest/>\n", "mobile/android/build.gradle": "", "firmware/platformio.ini": "[env:esp32]\n",
      "analytics/dbt_project.yml": "name: a\n", "Makefile": "build:\n\techo hi\n" },
    units: [".", "analytics", "docs", "firmware", "mobile/android", "notebooks"],
  },
  "no-manifest": {
    files: { "install.sh": "#!/bin/sh\n", "README.md": "# scripts\n", "Makefile": "test:\n\t./run_tests.sh\n" },
    units: ["."],
  },
  "dotfiles": {
    files: { ".zshrc": "", ".vimrc": "", ".tmux.conf": "", "nvim/init.lua": "", "install.sh": "" },
    units: ["."],
  },
};
