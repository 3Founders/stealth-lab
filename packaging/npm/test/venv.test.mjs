import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { activateVenv, findVenv } from "../lib/venv.mjs";

// a fake filesystem: the files that exist
const fsOf = (...files) => (p) => files.some((f) => path.resolve(f) === path.resolve(p));

test("the repository's virtualenv goes first on PATH for everything the hook starts; none, off or one already active: unchanged", () => {
  const root = path.resolve("/repo");
  const exists = fsOf(path.join(root, ".venv", "pyvenv.cfg"), path.join(root, ".venv", "Scripts", "python.exe"));
  assert.deepEqual(findVenv(root, { platform: "win32", exists }), { dir: path.join(root, ".venv"), bin: path.join(root, ".venv", "Scripts") });
  assert.equal(findVenv(root, { platform: "linux", exists }), null, "a Windows venv has no bin/python");
  const env = { Path: "C:\Windows", PYTHONHOME: "C:\py" };
  assert.equal(activateVenv(root, env, { platform: "win32", exists }), path.join(root, ".venv"));
  assert.equal(env.Path.split(path.delimiter)[0], path.join(root, ".venv", "Scripts"));
  assert.equal(env.PATH, undefined, "Windows' Path is updated, not doubled");
  assert.equal(env.VIRTUAL_ENV, path.join(root, ".venv"));
  assert.equal(env.PYTHONHOME, undefined);
  const active = { PATH: "/usr/bin", VIRTUAL_ENV: "/other" };
  assert.equal(activateVenv(root, active, { platform: "win32", exists }), null);
  assert.equal(active.PATH, "/usr/bin");
  assert.equal(activateVenv(root, { PATH: "/usr/bin", STEALTHLAB_VENV: "off" }, { platform: "win32", exists }), null);
  assert.equal(activateVenv(root, { PATH: "/usr/bin" }, { platform: "win32", exists: () => false }), null);
  const posix = fsOf(path.join(root, "venv", "pyvenv.cfg"), path.join(root, "venv", "bin", "python"));
  assert.equal(findVenv(root, { platform: "linux", exists: posix }).bin, path.join(root, "venv", "bin"));
});
