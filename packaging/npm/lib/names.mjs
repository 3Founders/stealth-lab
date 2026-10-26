// keळ's names on the user's machine. The npm package is kell-mcp (the bare name kell is someone else's) and the command
// is kell; before the rename both were stealthlab-mcp, which stays a command so hooks written by an older version run.
export const PACKAGE = "kell-mcp";
export const COMMAND = "kell";
const LEGACY = ["stealthlab", "mcp"].join("-");

// Is this hook command one of ours, from this version or an older one? (Ours run bin/kell.mjs, `kell`, `kell-mcp`,
// or the old stealthlab-mcp.)
export function isOwnCommand(cmd) {
  const c = String(cmd || "");
  return c.includes(LEGACY) || c.includes(PACKAGE) || /(^|[\s"'\\/])kell(\.mjs)?(["'\s]|$)/.test(c);
}
