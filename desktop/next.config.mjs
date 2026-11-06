// kell desktop's Next config. The one setting that matters: @opencode/client is left out of webpack's bundle
// (serverExternalPackages) and required from node_modules at runtime. It is ESM-only with no CJS "." export, and
// lib/engine.ts requires it lazily from the server routes; bundled, webpack cannot resolve it at all.
// Plain .mjs on purpose: a next.config.ts makes `next start` try to install typescript at launch, and the installed app
// has no devDependencies (that was the installer's second startup failure, 2026-11-03).
/** @type {import("next").NextConfig} */
const config = {
  serverExternalPackages: ["@opencode/client"],
  // The installed app runs .next/standalone/server.js with only the files the server actually uses (scripts/standalone.mjs copies
  // the static files in). Shipping all of node_modules made the app about 780 MB and over 10,000 files, so installing took minutes.
  output: "standalone",
}

export default config
