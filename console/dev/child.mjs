// Runs the sandboxed helper plus the fake provider in their OWN process and prints one JSON line:
//   {"base":..., "token":..., "provider":...}
// The jsdom UI tests use this, because under jsdom the helper's own AbortController/AbortSignal are jsdom's, which Node's
// fetch rejects. A child process is also closer to real use (the page and the helper are separate programs).
import { startDevHelper, startFakeProvider } from "./harness.mjs";

const gapMs = Number(process.env.KEL_GAP_MS) || 40;
const provider = await startFakeProvider({ gapMs });
const h = await startDevHelper({ provider, withKey: process.env.KEL_WITH_KEY === "1" });
process.stdout.write(JSON.stringify({ base: h.base, token: h.token, provider: provider.base }) + "\n");
process.on("SIGTERM", async () => { await h.close(); await provider.close(); process.exit(0); });
process.stdin.resume();
process.stdin.on("end", async () => { await h.close(); await provider.close(); process.exit(0); });
