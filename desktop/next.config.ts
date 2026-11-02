// kell desktop's Next config. The one setting that matters: @opencode/client is left out of webpack's bundle
// (serverExternalPackages) and required from node_modules at runtime. It is ESM-only with no CJS "." export, and
// lib/engine.ts requires it lazily from the server routes; bundled, webpack cannot resolve it at all.
import type { NextConfig } from "next"

const config: NextConfig = {
  serverExternalPackages: ["@opencode/client"],
}

export default config
