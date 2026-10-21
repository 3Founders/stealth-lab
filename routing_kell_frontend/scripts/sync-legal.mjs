// Copies the repo's docs/legal/*.md into content/legal so the site builds even when deployed from prod_frontend/ alone.
// docs/legal remains the source of truth; run automatically before dev/build.
import { cpSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "..", "docs", "legal");
const dst = join(root, "content", "legal");
if (!existsSync(src)) { console.log("[sync-legal] ../docs/legal not found — using committed copies"); process.exit(0); }
mkdirSync(dst, { recursive: true });
for (const f of readdirSync(src)) if (f.endsWith(".md") && f !== "AUDIT_REPORT.md") cpSync(join(src, f), join(dst, f));
console.log("[sync-legal] synced");
