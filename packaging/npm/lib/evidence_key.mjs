// The secret behind every hash of a check command or a file path that could leave the machine
// (docs/evidence_upload_design.md section 3).
//
// WHY A RANDOM KEY AND NOT ONE DERIVED FROM THE REPOSITORY IDENTITY: the identity hash is already sent to the server, and
// for a public repository anyone can compute it. A key built from it would let the server, or anyone who learns it, test
// guesses such as `npm test` or `src/auth/login.py` against the hashes. A random key that never leaves this folder cannot
// be guessed. The cost is that two machines with different keys do not produce matching hashes for the same check; sharing
// the key file is the team's explicit choice (see SHARING below).
//
// Where: <repo>/.stealth/evidence.key, 32 random bytes written as 64 hex characters, file mode 0600 where the operating
// system supports it (Windows ignores the mode bits and relies on the folder's ACL: this is a limit, not a guarantee).
// The key is never printed, never uploaded, never written into evidence.md, and `.stealth/.gitignore` lists it.
//
// SHARING (a choice, off by default): to let the server match the same check across a team's machines, copy the SAME
// evidence.key to each machine's .stealth/ (or point STEALTHLAB_EVIDENCE_KEY_FILE at one shared file). Whoever holds the key
// can test guesses against the hashes, so share it only inside the group that may already see the commands and paths.
// Never commit it; never send it by a channel you would not use for a password.
//
// Every failure here fails open toward "no key": the callers then upload nothing. There is no fallback to a weaker key.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const KEY_FILE = "evidence.key";
const KEY_BYTES = 32;
const KEY_RE = /^[0-9a-f]{64}$/;

export function keyPath(root, env = process.env) {
  return env.STEALTHLAB_EVIDENCE_KEY_FILE ? path.resolve(env.STEALTHLAB_EVIDENCE_KEY_FILE) : path.join(root, ".stealth", KEY_FILE);
}

// .stealth/.gitignore must list the key before the key exists, so a first `git add -A` cannot pick it up.
export function ensureKeyIgnored(root) {
  try {
    const dir = path.join(root, ".stealth");
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, ".gitignore");
    let cur = "";
    try { cur = fs.readFileSync(file, "utf8"); } catch { /* none yet */ }
    const lines = cur.split(/\r?\n/);
    const missing = [KEY_FILE, "evidence.md.prev"].filter((l) => !lines.includes(l));
    if (missing.length) fs.writeFileSync(file, cur + (cur && !cur.endsWith("\n") ? "\n" : "") + missing.join("\n") + "\n");
    return true;
  } catch {
    return false;
  }
}

// -> a 32-byte Buffer, created once and reused; null when it cannot be read or created (callers upload nothing).
export function evidenceKey(root, { env = process.env } = {}) {
  try {
    const file = keyPath(root, env);
    const read = () => {
      const text = fs.readFileSync(file, "utf8").trim();
      return KEY_RE.test(text) ? Buffer.from(text, "hex") : null;
    };
    try {
      const k = read();
      if (k) return k;
    } catch { /* not there yet */ }
    if (env.STEALTHLAB_EVIDENCE_KEY_FILE) return null;           // a shared file we were pointed at must already hold a key
    if (!ensureKeyIgnored(root)) return null;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const hex = crypto.randomBytes(KEY_BYTES).toString("hex");
    try {
      fs.writeFileSync(file, hex + "\n", { flag: "wx", mode: 0o600 });   // wx: never overwrite a key another process just made
    } catch (e) {
      if (e.code !== "EEXIST") throw e;
    }
    try { fs.chmodSync(file, 0o600); } catch { /* the OS has no mode bits */ }
    return read();
  } catch {
    return null;
  }
}

const normCommand = (s) => String(s ?? "").trim().replace(/\s+/g, " ");
const normPath = (s) => String(s ?? "").trim().replace(/\\/g, "/").replace(/^\.\//, "");

// HMAC-SHA256 with a domain label, so a command and a path (or a repo) with the same text never hash alike.
export function hmacHex(key, domain, text) {
  return crypto.createHmac("sha256", key).update(`${domain}\0${text}`).digest("hex");
}
export const checkHash = (key, command) => hmacHex(key, "check", normCommand(command));
export const pathHash = (key, p) => hmacHex(key, "path", normPath(p));
