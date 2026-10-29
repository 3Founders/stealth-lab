// A solved problem in the global ingestion's shape: a GENERAL Goal ("Support a new SQL function"), a Way whose steps say
// what to do in general terms with parameters ({function}), the parameters' values in THIS fix, and the invariants the
// change kept. One cheap call (gemma-4-31b-it by default), after lib/way_extract.mjs found the steps from the diff; the
// files, effects and preconditions (at= / gives= / needs=) stay the diff's, the model only names and generalises.
//
// Why general: the same normalised Goal title is one Goal and the same step list one Way (library.mjs), so a second
// "support a new function" fix joins the first one's Goal and Way instead of making its own -- the Way is reused and
// counted, and the next similar task is planned from it. Failure or no model: null, and the steps keep their
// mechanical descriptions (nothing is lost, only not generalised). STEALTHLAB_WAY_DESCRIBE=off.
import { readExecConfig } from "./exec/store.mjs";
import { profileKey } from "./executors/profile.mjs";

export const describeEnabled = (env = process.env) => (env.STEALTHLAB_WAY_DESCRIBE || "on").toLowerCase() !== "off";

export const DESCRIBE_SYSTEM = [
  "You turn one solved code change into reusable knowledge for this repository. Reply with only a JSON object:",
  '{"goal": string, "way": string, "slots": {name: value}, "steps": [string], "invariants": [string]}.',
  "goal: the KIND of problem, in general terms, at most 10 words (\"Support a new SQL function\", not the function's name).",
  "way: how it is done here, at most 10 words. slots: 1-4 parameters that vary between problems of this kind, each with",
  "its value in THIS change (\"function\": \"ARRAY_SLICE\"). steps: exactly one string per numbered step given, in the",
  "same order, each at most 20 words, saying what to do in that file in general terms and using {slot} names where the",
  "value goes. invariants: 0-3 things the change kept true that a similar change must keep too, at most 15 words each.",
  "Use only what the change shows; never invent files or steps.",
].join(" ");

const clip = (s, n) => String(s || "").replace(/[\r\n|]+/g, " ").replace(/\s+/g, " ").trim().slice(0, n);

// -> {goal, way, slots: {k: v}, steps: [string], invariants: [string]} | null. A reply with MORE steps than given (the
// model also wrote one for a test file the diff shows) keeps, per given step, the reply step that names that step's file
// -- `files[i]` the files of step i -- when every given step has one.
export function parseDescription(text, nSteps, files = []) {
  const s = String(text || "");
  const a = s.indexOf("{"), b = s.lastIndexOf("}");
  if (a < 0 || b <= a) return null;
  let o;
  try { o = JSON.parse(s.slice(a, b + 1)); } catch { return null; }
  if (!o || typeof o.goal !== "string" || !o.goal.trim() || !Array.isArray(o.steps)) return null;
  if (o.steps.length > nSteps && files.length === nSteps) {
    const named = files.map((fs) => o.steps.find((x) => typeof x === "string" &&
      fs.some((f) => x.includes(f) || x.includes(f.split("/").pop()))));
    if (named.every(Boolean)) o.steps = named;
  }
  if (o.steps.length !== nSteps) return null;
  const slots = {};
  for (const [k, v] of Object.entries(o.slots && typeof o.slots === "object" ? o.slots : {}).slice(0, 4)) {
    const key = clip(k, 30).replace(/[^\w-]/g, "");
    if (key) slots[key] = clip(v, 80);
  }
  return {
    goal: clip(o.goal, 100), way: clip(o.way || o.goal, 100), slots,
    steps: o.steps.map((x) => clip(x, 200)),
    invariants: (Array.isArray(o.invariants) ? o.invariants : []).slice(0, 3).map((x) => clip(x, 120)).filter(Boolean),
  };
}

export async function describeWay({ task, diff, steps, env = process.env, fetchImpl = globalThis.fetch, log = () => {}, timeoutMs = 20000 }) {
  try {
    if (!describeEnabled(env) || !steps?.length || !String(diff || "").trim()) return null;
    const name = String(env.STEALTHLAB_WAY_DESCRIBE_MODEL || "gemma-4-31b-it").trim();
    const profile = readExecConfig(env).profiles?.[name];
    if (!profile?.openai_base_url) return null;
    const user = `The problem it solved:\n${String(task || "").slice(0, 1500)}\n\nThe steps (one per file):\n` +
      steps.map((s, i) => `${i + 1}. ${s.at.join(", ")}: changes ${s.gives.slice(0, 6).join(", ") || "(see diff)"}`).join("\n") +
      `\n\nThe change (git diff):\n${String(diff).slice(0, 9000)}`;
    const messages = profile.no_system_role
      ? [{ role: "user", content: `${DESCRIBE_SYSTEM}\n\n${user}` }]
      : [{ role: "system", content: DESCRIBE_SYSTEM }, { role: "user", content: user }];
    const res = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
      body: JSON.stringify({ model: profile.model_id, messages, max_tokens: 1500, temperature: 0, ...(profile.request_extras || {}) }),
    });
    if (!res.ok) { log(`way: describe HTTP ${res.status}`); return null; }
    const body = await res.json();
    const d = parseDescription(body?.choices?.[0]?.message?.content, steps.length, steps.map((s) => s.at));
    if (!d) log("way: describe gave no usable reply");
    return d;
  } catch (err) {
    log(`way: describe ${err.name === "TimeoutError" ? "timed out" : err.message}`);
    return null;
  }
}

// A new library entry re-linked to its general Goal and Way (library.mjs reshapeEntry) -> true when it was.
export async function describeEntry({ root, id, task, diff, env = process.env, fetchImpl = globalThis.fetch, log = () => {} }) {
  try {
    const { stepsFromDiff } = await import("./way_extract.mjs");
    const steps = stepsFromDiff(diff);
    const d = await describeWay({ task, diff, steps, env, fetchImpl, log });
    if (!d) return false;
    const { reshapeEntry } = await import("./library.mjs");
    const ok = reshapeEntry(root, id, d);
    if (ok) log(`way: ${id} -> "${d.goal}" / "${d.way}"${Object.keys(d.slots).length ? ` (${Object.keys(d.slots).join(", ")})` : ""}`);
    return ok;
  } catch (err) {
    log(`way: ${err.message}`);
    return false;
  }
}
