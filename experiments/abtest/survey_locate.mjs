// For the pipeline survey (pipeline_survey.py): StealthLab's locate on one checked-out repository and one issue text --
// the search alone, and (SURVEY_NARROW=1) the search narrowed by a cheap model (lib/locate_model.mjs).
// argv: <root> <file with the prompt>; prints {search: {...}, narrowed: {...} | null}.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [root, promptFile] = process.argv.slice(2);
const npm = process.env.STEALTHLAB_NPM_DIR || path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")), "../../packaging/npm");
const { locate } = await import(pathToFileURL(path.join(npm, "lib", "locate.mjs")).href);
const prompt = fs.readFileSync(promptFile, "utf8");
const env = { ...process.env, STEALTHLAB_DISPATCH_LOCATE: "on" };
let t0 = Date.now();
const search = { ...locate({ prompt, root, env }), ms: 0 };
search.ms = Date.now() - t0;
let narrowed = null;
if (process.env.SURVEY_NARROW === "1") {
  const { narrowLocation } = await import(pathToFileURL(path.join(npm, "lib", "locate_model.mjs")).href);
  t0 = Date.now();
  const log = [];
  narrowed = { ...(await narrowLocation({ prompt, root, loc: search, env, log: (m) => log.push(m) })), log };
  narrowed.ms = Date.now() - t0;
}
process.stdout.write(JSON.stringify({ search, narrowed }));
