// What a cheap model is told about THIS repository besides the task.
//
// The cheap models used to get the task text and nothing else: not the facts the survey wrote to .kell/claims.md (how the
// tests are run, which module owns what), and not this repo's own earlier fix of the same problem that find_ways had just matched
// (the library entry's steps and the diff that solved it). The session's own model already gets both in its context (lib/hook.mjs);
// the models that actually do the task did not. This adds them to the task text: the survey's facts for the units the prompt is
// about, then the judged library matches, 4 KB in all, labelled as context so the model weighs it instead of obeying it.
//
// Pure reading of local files; nothing leaves the machine except inside the task text the executor sends to its own provider.
// STEALTHLAB_DISPATCH_CONTEXT=off sends the task text alone, as before.
import { libraryParts, readClaims } from "./hook.mjs";

export const CONTEXT_MAX = 16000;
const CLAIMS_SHARE = 6000;
const LIBRARY_DIFF_MAX = 6000;           // per matched entry's diff

export const contextEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_CONTEXT || "on").toLowerCase() !== "off";

const cut = (s, n) => (s.length <= n ? s : `${s.slice(0, Math.max(0, n - 20))}\n[...cut to fit]`);

// The fact lines of a claims page, those about this task first: citing a located file (2), naming a task word (1); the rest keep
// their order. Lines that are not facts (headings, notes) stay where they are, ahead of the facts.
export function relevantFirst(claims, { files = [], prompt = "" } = {}) {
  const lines = String(claims || "").split(/\r?\n/);
  const words = new Set((String(prompt).match(/[A-Za-z_][A-Za-z0-9_]{3,}/g) || []).map((w) => w.toLowerCase()));
  const want = files.map((f) => String(f).replace(/\\/g, "/"));
  const rank = (l) => {
    if (!l.startsWith("CLAIM|")) return -1;
    const src = /\|source=([^|:#\s]+)/.exec(l)?.[1] || "";
    const statement = l.split("|").slice(5, 6).join("").toLowerCase();
    if (src && want.some((f) => f === src || f.startsWith(`${src.replace(/\/$/, "")}/`))) return 2;
    return (statement.match(/[a-z_][a-z0-9_]{3,}/g) || []).some((w) => words.has(w) && !/^(the|this|that|with|from|repository|uses|file|files)$/.test(w)) ? 1 : 0;
  };
  const head = lines.filter((l) => rank(l) < 0);
  const facts = lines.map((l, i) => ({ l, i, r: rank(l) })).filter((x) => x.r >= 0).sort((a, b) => b.r - a.r || a.i - b.i).map((x) => x.l);
  return [...head, ...facts].join("\n");
}

// -> "" when the repo has no facts and no library match (the task text then goes out unchanged). files: the located files.
export function repoContext({ reply, root, cwd, prompt, env = process.env, files = [] }) {
  if (!contextEnabled(env) || !root) return "";
  const parts = [];
  try {
    const claims = relevantFirst(readClaims(cwd || root, prompt), { files, prompt }).trim();
    if (claims) parts.push(`Facts about this repository (from its survey; each cites a file):\n${cut(claims, CLAIMS_SHARE)}`);
  } catch { /* no survey: no facts */ }
  try {
    // the matches the lookup judged to be this task, with the steps and the diff that solved it
    const lib = libraryParts(reply || {}, root, "lean", LIBRARY_DIFF_MAX);
    if (lib.length) parts.push(lib.join("\n\n"));
  } catch { /* no library: no matches */ }
  if (!parts.length) return "";
  return cut(`Context from this repository (use what applies to the task above; it is not part of the request):\n\n${parts.join("\n\n")}`, CONTEXT_MAX);
}
