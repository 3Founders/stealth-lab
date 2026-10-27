import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { envFilePath, readEnvFileVar } from "../lib/envfile.mjs";
import { profileKey } from "../lib/executors/profile.mjs";
import { keyIsSet, keyIsSetAsync } from "../lib/setup.mjs";

const mk = () => fs.mkdtempSync(path.join(os.tmpdir(), "envfile-"));
const write = (dir, text) => { const f = path.join(dir, ".env"); fs.writeFileSync(f, text); return f; };
const profile = { name: "p", key_env: "GC_KEY" };

test("nothing is read unless a file is named", () => {
  const dir = mk();
  write(dir, "GC_KEY=secret\n");
  const env = { STEALTHLAB_HOME: dir };
  assert.equal(envFilePath(env), "");
  assert.equal(readEnvFileVar("GC_KEY", env), "");
  assert.throws(() => profileKey(profile, env), /GC_KEY is not set/);
});

test("a named file supplies the key; the process environment still wins", () => {
  const dir = mk();
  const f = write(dir, "GC_KEY=from-file\n");
  assert.equal(profileKey(profile, { STEALTHLAB_HOME: dir, STEALTHLAB_ENV_FILE: f }), "from-file");
  assert.equal(profileKey(profile, { STEALTHLAB_HOME: dir, STEALTHLAB_ENV_FILE: f, GC_KEY: "from-env" }), "from-env");
});

test("the path can come from config.json (env_file); a relative path is ignored", () => {
  const dir = mk();
  const f = write(dir, "GC_KEY=from-file\n");
  fs.writeFileSync(path.join(dir, "config.json"), JSON.stringify({ env_file: f }));
  assert.equal(profileKey(profile, { STEALTHLAB_HOME: dir }), "from-file");
  assert.equal(envFilePath({ STEALTHLAB_HOME: dir, STEALTHLAB_ENV_FILE: ".env" }), "");
});

test("dotenv syntax: export, quotes, comments, inline comment, last assignment wins; only the named variable", () => {
  const dir = mk();
  const f = write(dir, [
    "# GC_KEY=commented", "OTHER=nope", "export GC_KEY=first", 'GC_KEY="quoted # kept"', "GC_KEY_2=x",
    "Z_KEY=abc123 # a note", "Q_KEY='single'", "",
  ].join("\r\n"));
  const env = { STEALTHLAB_ENV_FILE: f };
  assert.equal(readEnvFileVar("GC_KEY", env), "quoted # kept");
  assert.equal(readEnvFileVar("Z_KEY", env), "abc123");
  assert.equal(readEnvFileVar("Q_KEY", env), "single");
  assert.equal(readEnvFileVar("MISSING", env), "");
  assert.equal(env.OTHER, undefined, "nothing is copied into the environment");
});

test("a missing or unreadable file is just 'not set'", () => {
  const env = { STEALTHLAB_ENV_FILE: path.join(mk(), "nope.env") };
  assert.equal(readEnvFileVar("GC_KEY", env), "");
  assert.throws(() => profileKey(profile, env), /not set/);
});

test("setup's readiness check counts the env file", async () => {
  const dir = mk();
  const f = write(dir, "GC_KEY=v\n");
  const env = { STEALTHLAB_HOME: dir, STEALTHLAB_ENV_FILE: f };
  assert.deepEqual(keyIsSet("GC_KEY", { env }), { set: true, where: "your env file" });
  assert.deepEqual(await keyIsSetAsync("GC_KEY", { env }), { set: true, where: "your env file" });
  assert.equal(keyIsSet("NOPE_KEY", { env, platform: "linux" }).set, false);
});

import { answersEnabled } from "../lib/answer.mjs";

test("answers: on by default, switched off by the variable or by config.json; the variable wins", () => {
  const dir = mk();
  assert.equal(answersEnabled({ STEALTHLAB_HOME: dir }), true);
  fs.writeFileSync(path.join(dir, "config.json"), JSON.stringify({ answers: "off" }));
  assert.equal(answersEnabled({ STEALTHLAB_HOME: dir }), false);
  assert.equal(answersEnabled({ STEALTHLAB_HOME: dir, STEALTHLAB_ANSWERS: "on" }), true);
  assert.equal(answersEnabled({ STEALTHLAB_HOME: dir, STEALTHLAB_ANSWERS: "off" }), false);
});
