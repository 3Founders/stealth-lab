// Copies the MCP connector's installers into public/ so the site serves
//   curl -fsSL https://<site>/install.sh | bash
//   irm https://<site>/install.ps1 | iex
// packaging/npm/install/ remains the source of truth; run automatically before dev/build.
import { cpSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "..", "packaging", "npm", "install");
if (!existsSync(src)) { console.log("[sync-installers] ../packaging/npm/install not found — using committed copies"); process.exit(0); }
for (const f of ["install.sh", "install.ps1"]) cpSync(join(src, f), join(root, "public", f));
console.log("[sync-installers] synced");
