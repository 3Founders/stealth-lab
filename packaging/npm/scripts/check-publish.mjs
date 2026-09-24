// Refuses `npm publish` until the hosted endpoint is set, and set identically
// in the three places a user can enter from: package.json (npx),
// install/install.sh (curl | bash) and install/install.ps1 (irm | iex).
import fs from "node:fs";

const read = (p) => fs.readFileSync(new URL(`../${p}`, import.meta.url), "utf8");
const pkgUrl = JSON.parse(read("package.json")).stealthlab?.defaultMcpUrl || "";
const shUrl = read("install/install.sh").match(/^DEFAULT_URL="(.*)"$/m)?.[1] ?? null;
const psUrl = read("install/install.ps1").match(/^\s*\$DefaultUrl = "(.*)"$/m)?.[1] ?? null;

const problems = [];
if (!pkgUrl) problems.push('package.json "stealthlab.defaultMcpUrl" is empty');
else if (!/^https:\/\//.test(pkgUrl)) problems.push(`defaultMcpUrl must be https: ${pkgUrl}`);
if (shUrl !== pkgUrl) problems.push(`install.sh DEFAULT_URL (${shUrl}) != package.json (${pkgUrl})`);
if (psUrl !== pkgUrl) problems.push(`install.ps1 $DefaultUrl (${psUrl}) != package.json (${pkgUrl})`);

if (problems.length) {
  console.error("not publishable:\n  - " + problems.join("\n  - "));
  process.exit(1);
}
console.log(`publish check ok: ${pkgUrl}`);
