// An opt-in place for provider keys: a dotenv-style file (typically the backend's own backend/.env) named by
// STEALTHLAB_ENV_FILE or by `env_file` in ~/.stealthlab/config.json (set with `setup --env-file <path>`).
//
// Why it exists: the hooks run inside Claude Code / Cursor, which never load the backend's .env, so a profile's
// key_env was only found in the process environment or (Windows) the user environment. A developer who already keeps the
// keys in backend/.env should not have to copy them anywhere else.
//
// Limits, on purpose:
//   * only the ONE variable a profile names is read, on demand; nothing from the file is copied into process.env, so it
//     cannot leak into a child process, and the rest of the file (database URLs, other keys) is never looked at;
//   * the process environment always wins; the file is only a fallback;
//   * the path must be absolute, and nothing is read unless it is named. There is no search for .env files.
// Not a secret store: the file stays where it is, with whatever permissions it already has.

import fs from "node:fs";
import path from "node:path";
import { configDir, readConfig, writeConfig } from "./config.mjs";

// The file's path from the environment or the saved config, or "" when none is named (or it is not absolute).
export function envFilePath(env = process.env) {
  let p = String(env.STEALTHLAB_ENV_FILE || "").trim();
  if (!p) { try { p = String(readConfig(env).env_file || "").trim(); } catch { p = ""; } }
  return p && path.isAbsolute(p) ? p : "";
}

// One KEY=value line, dotenv style: optional `export `, single or double quotes, an unquoted value ends at ` #`.
function parseValue(raw) {
  let v = raw.trim();
  const q = v[0];
  if ((q === '"' || q === "'") && v.indexOf(q, 1) > 0) return v.slice(1, v.indexOf(q, 1));
  const hash = v.search(/\s#/);
  if (hash >= 0) v = v.slice(0, hash);
  return v.trim();
}

// The value of `name` in the file, or "" (no file, unreadable, or not there). The last assignment wins, as in dotenv.
export function readEnvFileVar(name, env = process.env) {
  const file = envFilePath(env);
  if (!file) return "";
  let text;
  try { text = fs.readFileSync(file, "utf8"); } catch { return ""; }
  const re = new RegExp(`^\\s*(?:export\\s+)?${name.replace(/[^A-Za-z0-9_]/g, "")}\\s*=(.*)$`);
  let out = "";
  for (const line of text.split(/\r?\n/)) {
    if (/^\s*#/.test(line)) continue;
    const m = re.exec(line);
    if (m) out = parseValue(m[1]);
  }
  return out;
}

// ---- writing (the console's "add a key"): only ever the one variable asked for, in a file the user can read ----

const KEY_NAME = /^[A-Z][A-Z0-9_]{1,63}$/;
export const isKeyName = (name) => KEY_NAME.test(String(name || ""));
const assignmentOf = (name) => new RegExp(String.raw`^\s*(?:export\s+)?${name}\s*=`);

// Where saved keys go: the named env file if there is one, else ~/.stealthlab/keys.env (created 0600 and then named in
// config.json, so the hooks find it the same way they find any env file).
export function envFileTarget(env = process.env) {
  return envFilePath(env) || path.join(configDir(env), "keys.env");
}

function quoteValue(value) {
  if (/[\r\n\0]/.test(value)) throw new Error("a key cannot contain a line break");
  if (value.includes('"') && value.includes("'")) throw new Error("a key cannot contain both quote characters");
  if (/[\s#]/.test(value) || value.includes('"') || value.includes("'")) return value.includes('"') ? `'${value}'` : `"${value}"`;
  return value;
}

// Set NAME=value in the env file (replacing an earlier assignment of NAME, leaving every other line as it was).
// -> the file's path.
export function writeEnvFileVar(name, value, env = process.env) {
  if (!isKeyName(name)) throw new Error("the key name must be capital letters, digits and underscores");
  const v = String(value ?? "");
  if (!v.trim()) throw new Error("the key is empty");
  const line = `${name}=${quoteValue(v)}`;
  const file = envFileTarget(env);
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  let lines = [];
  try { lines = fs.readFileSync(file, "utf8").split(/\r?\n/); } catch { /* new file */ }
  const kept = lines.filter((l) => !assignmentOf(name).test(l));
  while (kept.length && kept[kept.length - 1] === "") kept.pop();
  kept.push(line);
  fs.writeFileSync(file, kept.join("\n") + "\n", { mode: 0o600 });
  try { fs.chmodSync(file, 0o600); } catch { /* best effort on Windows */ }
  if (!envFilePath(env)) writeConfig({ env_file: file }, env);
  return file;
}

// Remove NAME from the env file. -> whether a line was removed.
export function removeEnvFileVar(name, env = process.env) {
  if (!isKeyName(name)) throw new Error("the key name must be capital letters, digits and underscores");
  const file = envFilePath(env);
  if (!file) return false;
  let lines;
  try { lines = fs.readFileSync(file, "utf8").split(/\r?\n/); } catch { return false; }
  const kept = lines.filter((l) => !assignmentOf(name).test(l));
  if (kept.length === lines.length) return false;
  fs.writeFileSync(file, kept.join("\n"), { mode: 0o600 });
  return true;
}
