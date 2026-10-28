/**
 * Import keys from a pasted .env block, in the browser. The text is parsed here and never sent anywhere; only variable names
 * from the list below are read (so a pasted file's database passwords and other secrets are ignored, not stored). What the page
 * shows is the variable name and the last 4 characters, never the whole key.
 */
export interface FoundKey { varName: string; provider: string; apiKey: string }

/** The standard variable names each provider's own tools use. */
export const ENV_VARS: Record<string, string> = {
  ANTHROPIC_API_KEY: "anthropic",
  OPENAI_API_KEY: "openai",
  GROQ_API_KEY: "groq",
  TOGETHER_API_KEY: "together",
};

export interface ParsedEnv { found: FoundKey[]; ignored: number }

export function parseEnvBlock(text: string): ParsedEnv {
  const found: FoundKey[] = [];
  const seen = new Set<string>();
  let ignored = 0;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const m = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (!m) { ignored++; continue; }
    const provider = ENV_VARS[m[1].toUpperCase()];
    if (!provider) { ignored++; continue; }
    let value = m[2].trim();
    const quoted = /^(["'])(.*)\1/.exec(value);
    value = quoted ? quoted[2] : value.replace(/\s+#.*$/, ""); // a trailing comment only counts on an unquoted value
    value = value.trim();
    if (!value || /^\$\{?[A-Za-z_]/.test(value) || seen.has(m[1].toUpperCase())) { ignored++; continue; } // empty, a ${REFERENCE}, or a repeat
    seen.add(m[1].toUpperCase());
    found.push({ varName: m[1].toUpperCase(), provider, apiKey: value });
  }
  return { found, ignored };
}
