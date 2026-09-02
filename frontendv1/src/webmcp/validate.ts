/** Validate and sanitize WebMCP tool input. Never trust agent input. */

const ID_RE = /^[0-9a-fA-F-]{8,64}$/;

export function sanitizeId(input: unknown, label: string): string {
  if (typeof input !== "string") throw new Error(`${label} must be a string`);
  const id = input.trim();
  if (!ID_RE.test(id)) throw new Error(`invalid ${label}`);
  return id;
}

export function sanitizeQuery(input: unknown, label: string): string {
  if (typeof input !== "string") throw new Error(`${label} must be a string`);
  const q = input.trim().slice(0, 500);
  if (!q) throw new Error(`${label} must not be empty`);
  return q;
}

export function sanitizeScope(input: unknown): string | undefined {
  if (input === null || input === undefined) return undefined;
  if (typeof input !== "string" || !ID_RE.test(input.trim()))
    throw new Error("invalid scope id");
  return input.trim();
}

export function sanitizeFocus(input: unknown): string | undefined {
  if (input === null || input === undefined) return undefined;
  if (typeof input !== "string") throw new Error("focus must be a string");
  const f = input.trim().slice(0, 200);
  return f || undefined;
}
