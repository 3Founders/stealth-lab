// Secret redaction for everything that leaves a run: check-output tails, executor summaries, "learned"
// items, the run store's logs and events, and every outbox / hosted payload.
//
// PORTED_PATTERNS is a line-for-line port of KNOWN_TOKEN_PATTERNS in
// backend/app/services/trace_redaction.py (same names, same order, same regexes; Python's re.DOTALL
// becomes [\s\S]). test/exec_redact.test.mjs pins the port to vectors produced by running the Python
// module itself, so the two cannot drift silently.
//
// EXTRA_PATTERNS are a deliberate SUPERSET, applied after the ported ones. They exist because the ported
// set misses shapes that matter for check-output tails specifically: `sk-test-...` / `sk-proj-...` style
// keys (the ported openai_key pattern stops at the first hyphen), and a private-key block whose END line
// was cut off by the 40-line tail. They never change the output for an input the ported set already
// fully redacts.
//
// HONEST LIMIT (same as the Python module's): no pattern set catches every secret shape. This is a floor
// for detected patterns, not a guarantee -- which is why raw transcripts never leave the machine at all
// and only short, redacted fields do.

export const PORTED_PATTERNS = [
  ["aws_access_key", /AKIA[0-9A-Z]{16}/g],
  ["github_token", /gh[pousr]_[A-Za-z0-9]{36,}/g],
  ["slack_token", /xox[baprs]-[A-Za-z0-9-]{10,}/g],
  ["stripe_key", /sk_(live|test)_[A-Za-z0-9]{16,}/g],
  ["openai_key", /sk-[A-Za-z0-9]{20,}/g],
  ["anthropic_key", /sk-ant-[A-Za-z0-9-]{20,}/g],
  ["generic_bearer", /[Bb]earer\s+[A-Za-z0-9._-]{20,}/g],
  ["private_key_block", /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g],
];

export const EXTRA_PATTERNS = [
  ["secret_key", /\bsk-[A-Za-z0-9][A-Za-z0-9_-]{15,}/g],
  ["private_key_block", /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*$/g],
  ["secret_assignment",
    /\b([A-Za-z0-9_]*(?:API_KEY|APIKEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Za-z0-9_]*)(\s*[=:]\s*)(["']?)(?!\[REDACTED)[^\s"']{8,}\3/gi],
];

// Same rule set as SENSITIVE_PATH_PATTERNS (backslashes normalized first, as the Python fix does).
export const SENSITIVE_PATH_PATTERNS = [
  /(^|[/\s])\.env($|\.[\w.]+$)/,
  /\.pem$/,
  /(^|[/\s])id_(rsa|dsa|ecdsa|ed25519)$/,
  /\.key$/,
  /\.pfx$/,
  /(^|[/\s])\.ssh\//,
  /(^|[/\s])\.aws\/credentials$/,
];

function apply(text, patterns, matched) {
  let out = text;
  for (const [name, re] of patterns) {
    re.lastIndex = 0;
    out = out.replace(re, (...m) => {
      matched.push(name);
      if (name === "secret_assignment") return `${m[1]}${m[2]}[REDACTED:${name}]`;
      return `[REDACTED:${name}]`;
    });
  }
  return out;
}

// Exactly the backend's _redact_string: kept separate so the port can be tested for equality.
export function redactPorted(text) {
  return apply(String(text ?? ""), PORTED_PATTERNS, []);
}

// redact(text, { secrets }) -- ported patterns + the superset + any literal secret values the caller
// knows (e.g. the saved StealthLab token), which are replaced wherever they appear.
export function redact(text, { secrets = [] } = {}) {
  if (text === null || text === undefined) return text;
  let out = String(text);
  for (const s of secrets) {
    if (typeof s === "string" && s.length >= 8) out = out.split(s).join("[REDACTED:known_secret]");
  }
  const matched = [];
  out = apply(out, PORTED_PATTERNS, matched);
  return apply(out, EXTRA_PATTERNS, matched);
}

// Deep redaction over parsed JSON: only string leaves are touched, so the result stays valid JSON.
export function redactValue(value, opts) {
  if (typeof value === "string") return redact(value, opts);
  if (Array.isArray(value)) return value.map((v) => redactValue(v, opts));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redactValue(v, opts)]));
  }
  return value;
}

export function isSensitivePath(p) {
  const candidate = String(p).replace(/\\/g, "/");
  return SENSITIVE_PATH_PATTERNS.some((re) => re.test(candidate));
}
