import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { adoptLegacyDir, findStealthRoot, stealthDir } from "../lib/library.mjs";

// keळ's folder was renamed from .stealth to .kell; a repository that still has only the old one keeps working
const OLD = "." + "stealth";
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "kell-dir-"));

test("a repository with only the old folder gets it renamed, contents and all", () => {
  const root = tmp();
  fs.mkdirSync(path.join(root, OLD, "library"), { recursive: true });
  fs.writeFileSync(path.join(root, OLD, "claims.md"), "R-001|fact\n");
  fs.writeFileSync(path.join(root, OLD, "library", "x.diff"), "diff\n");
  assert.equal(findStealthRoot(root), root);
  assert.ok(!fs.existsSync(path.join(root, OLD)));
  assert.equal(fs.readFileSync(path.join(root, ".kell", "claims.md"), "utf8"), "R-001|fact\n");
  assert.ok(fs.existsSync(path.join(root, ".kell", "library", "x.diff")));
});

test("found from a sub-directory too", () => {
  const root = tmp();
  fs.mkdirSync(path.join(root, OLD));
  fs.writeFileSync(path.join(root, OLD, ".keep"), "");
  fs.mkdirSync(path.join(root, "src", "deep"), { recursive: true });
  assert.equal(findStealthRoot(path.join(root, "src", "deep")), root);
  assert.ok(fs.existsSync(path.join(root, ".kell", ".keep")));
});

test("stealthDir() renames the old folder before handing back the path", () => {
  const root = tmp();
  fs.mkdirSync(path.join(root, OLD));
  fs.writeFileSync(path.join(root, OLD, "routing.md"), "ROUTE\n");
  assert.equal(stealthDir(root), path.join(root, ".kell"));
  assert.ok(fs.existsSync(path.join(root, ".kell", "routing.md")));
});

test("a folder of that name that is not keळ's is left alone", () => {
  const root = tmp();
  fs.mkdirSync(path.join(root, OLD));
  fs.writeFileSync(path.join(root, OLD, "someone-elses.txt"), "x");
  adoptLegacyDir(root);
  assert.ok(fs.existsSync(path.join(root, OLD, "someone-elses.txt")));
  assert.ok(!fs.existsSync(path.join(root, ".kell")));
});

test("when both folders exist, neither is touched", () => {
  const root = tmp();
  fs.mkdirSync(path.join(root, OLD));
  fs.writeFileSync(path.join(root, OLD, "claims.md"), "old\n");
  fs.mkdirSync(path.join(root, ".kell"));
  fs.writeFileSync(path.join(root, ".kell", "claims.md"), "new\n");
  adoptLegacyDir(root);
  assert.equal(fs.readFileSync(path.join(root, OLD, "claims.md"), "utf8"), "old\n");
  assert.equal(fs.readFileSync(path.join(root, ".kell", "claims.md"), "utf8"), "new\n");
});
