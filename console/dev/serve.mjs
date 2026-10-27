// `npm run helper` (from console/): the real helper in a sandbox, serving the built page, with a fake provider.
// Build the page first (`npm run build`), then open the address printed. Nothing on this machine is changed.
// For the hot-reloading dev server instead: `npm run dev`, then open  http://localhost:3300/?api=<helper base>&t=<token>
import path from "node:path";
import { HERE, startDevHelper, startFakeProvider } from "./harness.mjs";

const provider = await startFakeProvider();
const h = await startDevHelper({ uiDir: path.resolve(HERE, "..", "out"), allowOrigins: ["http://localhost:3300", "http://127.0.0.1:3300"], provider, port: Number(process.env.PORT) || 0 });
console.log(`console helper (sandbox, fake provider on ${provider.base})\n  page:  ${h.url}\n  dev:   http://localhost:3300/?api=${h.base}&t=${h.token}\nCtrl+C to stop.`);
await new Promise(() => {});
