// Starts the admin app on port 3201 showing SAMPLE DATA (see lib/demo.ts). A separate port, so it can run beside the real
// app on 3200. Development only: the sample-data switch is ignored in a production build.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const next = require.resolve("next/dist/bin/next");
const port = process.env.PORT || "3201";

const child = spawn(process.execPath, [next, "dev", "-p", port], {
  stdio: "inherit",
  env: { ...process.env, NEXT_PUBLIC_DEMO_DATA: "1", NEXT_DIST_DIR: ".next-demo" },
});
child.on("exit", (code) => process.exit(code ?? 0));
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
