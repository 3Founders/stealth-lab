// Model profiles: how an executor reaches an OPEN model (GLM, DeepSeek, Kimi, ...) instead of the vendor's own.
//
// A profile lives in ~/.stealthlab/exec.json under "profiles", keyed by the model name a run asks for:
//   { "profiles": { "glm-5.3": {
//       "model_id": "glm-5.3",                    // the id the provider expects (default: the key)
//       "key_env": "ZAI_API_KEY",                 // NAME of the env var holding the provider key; never the key itself
//       "anthropic_base_url": "https://api.z.ai/api/anthropic",   // for the claude executor
//       "openai_base_url": "https://api.example.com/v1",          // for the opencode and stealth executors
//       "input_per_mtok": 0.6, "output_per_mtok": 2.2,            // USD, optional; without them cost is unknown
//       "zdr": true, "no_training": true, "region": "us", "dpa_signed": true,   // declarations, see below
//       "request_extras": { "provider": { "zdr": true } },
//       "no_system_role": true } } }      // the endpoint rejects a system turn: its text opens the first user turn
//
// Boundary (same as common.mjs): the provider key is the USER'S OWN key for that provider, read from the user's own
// environment and handed only to the process that talks to that provider. It is never written to disk by us, never
// logged, and never mixed with another vendor's credentials: the claude adapter removes the user's Anthropic
// credentials from the child before pointing it at a third-party endpoint.
//
// The compliance fields are declarations by whoever wrote the file, kept so a run can show what was claimed. Unknown
// stays unknown (absent), never "true". Nothing here proves what a provider does with prompts.
import { readConfig } from "../config.mjs";
const REGION = /^[a-z]{2,8}(-[a-z0-9]{1,8})?$/;
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const KEY_ENV_FOR_CHILD = "OPEN_MODEL_API_KEY";   // the one variable a child reads the provider key from

// Body fields the step agent owns; a profile may not override them.
export const PROTECTED_BODY_KEYS = Object.freeze(["model", "messages", "stream", "stream_options", "tools", "tool_choice",
  "functions", "function_call", "max_tokens", "max_completion_tokens", "temperature"]);

function httpsUrl(name, field, value) {
  if (value === undefined || value === null) return undefined;
  let u;
  try { u = new URL(String(value)); } catch { throw new Error(`profile ${name}: ${field} is not a URL`); }
  const loopback = u.hostname === "localhost" || u.hostname === "127.0.0.1" || u.hostname === "[::1]";
  if (u.protocol !== "https:" && !(u.protocol === "http:" && loopback)) {
    throw new Error(`profile ${name}: ${field} must be https (http is allowed only for localhost)`);
  }
  if (u.username || u.password) throw new Error(`profile ${name}: ${field} must not contain credentials`);
  return String(value).replace(/\/+$/, "");
}

function price(name, field, value) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw new Error(`profile ${name}: ${field} must be a non-negative number`);
  return value;
}

function flag(name, field, value) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "boolean") throw new Error(`profile ${name}: ${field} must be true or false (or absent when unknown)`);
  return value;
}

// Validate one raw profile; throws with a message the user can act on. Returns a frozen normalised copy.
export function normaliseProfile(name, raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error(`profile ${name}: must be an object`);
  // The key comes from ONE place: an environment variable (key_env: the user's own provider key), or the StealthLab token the
  // user is already signed in with (key_from "stealthlab": for a profile that points at the StealthLab gateway, where the
  // provider keys live on the server and no developer machine holds one).
  const keyFrom = raw.key_from;
  if (keyFrom !== undefined && keyFrom !== null && keyFrom !== "stealthlab") throw new Error(`profile ${name}: key_from can only be "stealthlab"`);
  if (keyFrom && raw.key_env) throw new Error(`profile ${name}: give key_env or key_from, not both`);
  const keyEnv = keyFrom ? null : raw.key_env;
  if (!keyFrom && (typeof keyEnv !== "string" || !NAME.test(keyEnv))) throw new Error(`profile ${name}: key_env must be the NAME of an environment variable`);
  const modelId = String(raw.model_id || name).trim();
  if (!modelId || /[\s|]/.test(modelId)) throw new Error(`profile ${name}: model_id must be a single token`);
  const anthropic = httpsUrl(name, "anthropic_base_url", raw.anthropic_base_url);
  const openai = httpsUrl(name, "openai_base_url", raw.openai_base_url);
  if (!anthropic && !openai) throw new Error(`profile ${name}: give anthropic_base_url (claude) and/or openai_base_url (opencode, stealth)`);
  let region;
  if (raw.region !== undefined && raw.region !== null) {
    if (typeof raw.region !== "string" || !(REGION.test(raw.region) || raw.region === "unknown")) throw new Error(`profile ${name}: region must be lowercase like "us", "eu", "in" or "unknown"`);
    region = raw.region;
  }
  let extras = {};
  if (raw.request_extras !== undefined && raw.request_extras !== null) {
    if (typeof raw.request_extras !== "object" || Array.isArray(raw.request_extras)) throw new Error(`profile ${name}: request_extras must be an object`);
    const clash = PROTECTED_BODY_KEYS.filter((k) => Object.prototype.hasOwnProperty.call(raw.request_extras, k));
    if (clash.length) throw new Error(`profile ${name}: request_extras cannot set ${clash.join(", ")} (the agent owns those fields)`);
    extras = JSON.parse(JSON.stringify(raw.request_extras));
  }
  const inP = price(name, "input_per_mtok", raw.input_per_mtok);
  const outP = price(name, "output_per_mtok", raw.output_per_mtok);
  if ((inP === undefined) !== (outP === undefined)) throw new Error(`profile ${name}: give both input_per_mtok and output_per_mtok, or neither`);
  return Object.freeze({
    name, model_id: modelId, key_env: keyEnv, key_from: keyFrom || null, anthropic_base_url: anthropic, openai_base_url: openai,
    input_per_mtok: inP, output_per_mtok: outP,
    zdr: flag(name, "zdr", raw.zdr), no_training: flag(name, "no_training", raw.no_training),
    dpa_signed: flag(name, "dpa_signed", raw.dpa_signed) ?? false, region,
    request_extras: Object.freeze(extras),
    no_system_role: flag(name, "no_system_role", raw.no_system_role) ?? false,
    // the model reads images (a GLM-V, say): tasks built on a screenshot may go to it, and it gets the image
    vision: flag(name, "vision", raw.vision) ?? false,
    // the strongest cheap model (GLM-5.3-flash, say): it alone tries a task the brief calls large, is never cut by the
    // max-rungs limit, still gets its try after two small ones fail the same advance tests, and gets extra time
    // (dispatch's STEALTHLAB_DISPATCH_STRONG_EXTRA_S). By default it is the last cheap try; first: true starts it in the
    // first wave (a fast strong model loses nothing there)
    strong: flag(name, "strong", raw.strong) ?? false,
    first: flag(name, "first", raw.first) ?? false,
  });
}

// All profiles in a config object, validated. A bad entry throws: failing loudly beats half-applied routing.
export function normaliseProfiles(rawProfiles) {
  const out = {};
  if (!rawProfiles || typeof rawProfiles !== "object" || Array.isArray(rawProfiles)) return out;
  for (const [name, raw] of Object.entries(rawProfiles)) out[name] = normaliseProfile(name, raw);
  return out;
}

// The key for a profile from the user's environment, or throws saying which variable is missing.
export function profileKey(profile, env = process.env) {
  if (profile.key_from === "stealthlab") return stealthlabKey(profile, env);
  const v = env[profile.key_env];
  if (!v || !String(v).trim()) throw new Error(`profile ${profile.name}: environment variable ${profile.key_env} is not set`);
  return String(v);
}

const hostOf = (u) => { try { return new URL(String(u)).hostname.toLowerCase(); } catch { return ""; } };

// The saved StealthLab token, for a gateway profile. It is sent ONLY to the host of the user's own StealthLab server: an
// exec.json edited to point such a profile somewhere else must not be able to carry the token away.
function stealthlabKey(profile, env) {
  let cfg = {};
  try { cfg = readConfig(env); } catch { /* an unreadable config: no saved token */ }
  const token = String(env.STEALTHLAB_TOKEN || cfg.token || "").trim();
  if (!token) throw new Error(`profile ${profile.name}: no StealthLab token is saved (run stealthlab-mcp login, or setup --token-file <file>)`);
  const server = hostOf(env.STEALTHLAB_MCP_URL || cfg.url || "");
  const target = hostOf(profile.openai_base_url || profile.anthropic_base_url);
  if (!server || !target || server !== target) {
    throw new Error(`profile ${profile.name}: key_from "stealthlab" sends your StealthLab token, so its base URL must be on your StealthLab server's host (${server || "none configured"}), not ${target || "unknown"}`);
  }
  return token;
}

// What a profile declares about data handling, for display next to a run's result.
export function complianceOf(profile) {
  return { zdr: profile.zdr ?? null, no_training: profile.no_training ?? null, region: profile.region ?? "unknown", dpa_signed: profile.dpa_signed };
}

// Credential variables of OTHER vendors that must not reach a child that is about to talk to this profile's provider.
export const FOREIGN_CREDENTIAL_VARS = Object.freeze([
  "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
  "GEMINI_API_KEY", "GOOGLE_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]);
