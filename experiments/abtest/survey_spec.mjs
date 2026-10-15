// For the pipeline survey, stage 2: StealthLab's test writer on one issue, exactly as dispatch calls it (the located code,
// the baseline rule, GLM-5.3 hedged with Sonnet). argv: <root> <prompt file> <python> <out dir>.
// Prints {file, model?, ambiguous?, ms} or {file: null}.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [root, promptFile, python, outDir] = process.argv.slice(2);
const npm = process.env.STEALTHLAB_NPM_DIR;
const { locate, renderForWriter } = await import(pathToFileURL(path.join(npm, "lib", "locate.mjs")).href);
const { writeSpecTests } = await import(pathToFileURL(path.join(npm, "lib", "dispatch.mjs")).href);
const prompt = fs.readFileSync(promptFile, "utf8");
const env = { ...process.env, STEALTHLAB_DISPATCH_TESTFIRST_MODEL: process.env.SURVEY_WRITER || "glm-5.3" };
const t0 = Date.now();
const log = [];
const { narrowLocation } = await import(pathToFileURL(path.join(npm, "lib", "locate_model.mjs")).href);
const loc = await narrowLocation({ prompt, root, loc: locate({ prompt, root, env }), env });   // as dispatch does
fs.mkdirSync(outDir, { recursive: true });
const spec = await writeSpecTests({ prompt, root, mine: "claude-opus-5-5|claude-code", env, dir: outDir, log: (m) => log.push(m),
                                    located: renderForWriter(loc, root), baseline: true, python });
// the "likely place" line, for stage 3's agents (as dispatch would send it)
const { renderWhere } = await import(pathToFileURL(path.join(npm, "lib", "locate.mjs")).href);
fs.writeFileSync(path.join(outDir, "where.txt"), renderWhere(loc));
const costs = (() => { try { return fs.readFileSync(path.join(path.dirname(path.dirname(outDir)), "dispatch_costs.jsonl"), "utf8"); } catch { return ""; } })();
const model = (/"what":"test-first","model":"([^"]+)"/.exec(costs.split("\n").reverse().join("\n")) || [])[1] || null;
process.stdout.write(JSON.stringify({ file: spec?.file || null, ambiguous: spec?.ambiguous || null, model, log, ms: Date.now() - t0 }));
