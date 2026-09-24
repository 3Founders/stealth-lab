// Copies the MCP connector's installers into public/ so the site serves
//   curl -fsSL https://<site>/install.sh | bash
//   irm https://<site>/install.ps1 | iex
// packaging/npm/install/ remains the source of truth; run automatically before dev/build.
//
// NEXT_PUBLIC_KEL_MCP_URL (the hosted MCP endpoint, e.g. https://mcp.example.com/mcp),
// when set at build time, becomes the scripts' default endpoint, so the one-liners
// work without passing --url or editing the repo.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "..", "packaging", "npm", "install");
if (!existsSync(src)) { console.log("[sync-installers] ../packaging/npm/install not found — using committed copies"); process.exit(0); }

const mcpUrl = (process.env.NEXT_PUBLIC_KEL_MCP_URL || "").trim();
if (mcpUrl && !/^https:\/\/[^\s"'`$]+$/.test(mcpUrl)) {
  console.error(`[sync-installers] NEXT_PUBLIC_KEL_MCP_URL must be an https URL without quotes or spaces, got ${JSON.stringify(mcpUrl)}`);
  process.exit(1);
}
const defaults = {
  "install.sh": [/^DEFAULT_URL=".*"$/m, `DEFAULT_URL="${mcpUrl}"`],
  "install.ps1": [/^(\s*)\$DefaultUrl = ".*"$/m, `$1$DefaultUrl = "${mcpUrl}"`],
};
for (const [file, [pattern, replacement]] of Object.entries(defaults)) {
  let text = readFileSync(join(src, file), "utf8");
  if (mcpUrl) {
    if (!pattern.test(text)) { console.error(`[sync-installers] default URL line not found in ${file}`); process.exit(1); }
    text = text.replace(pattern, replacement);
  }
  writeFileSync(join(root, "public", file), text);
}
console.log(`[sync-installers] synced${mcpUrl ? ` (default endpoint ${mcpUrl})` : ""}`);
