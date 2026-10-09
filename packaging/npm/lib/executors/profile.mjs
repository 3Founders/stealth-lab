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
  const keyEnv = raw.key_env;
  if (typeof keyEnv !== "string" || !NAME.test(keyEnv)) throw new Error(`profile ${name}: key_env must be the NAME of an environment variable`);
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
    name, model_id: modelId, key_env: keyEnv, anthropic_base_url: anthropic, openai_base_url: openai,
    input_per_mtok: inP, output_per_mtok: outP,
    zdr: flag(name, "zdr", raw.zdr), no_training: flag(name, "no_training", raw.no_training),
    dpa_signed: flag(name, "dpa_signed", raw.dpa_signed) ?? false, region,
    request_extras: Object.freeze(extras),
    no_system_role: flag(name, "no_system_role", raw.no_system_role) ?? false,
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
  const v = env[profile.key_env];
  if (!v || !String(v).trim()) throw new Error(`profile ${profile.name}: environment variable ${profile.key_env} is not set`);
  return String(v);
}

// What a profile declares about data handling, for display next to a run's result.
export function complianceOf(profile) {
  return { zdr: profile.zdr ?? null, no_training: profile.no_training ?? null, region: profile.region ?? "unknown", dpa_signed: profile.dpa_signed };
}

// Credential variables of OTHER vendors that must not reach a child that is about to talk to this profile's provider.
export const FOREIGN_CREDENTIAL_VARS = Object.freeze([
  "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
  "GEMINI_API_KEY", "GOOGLE_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]);
