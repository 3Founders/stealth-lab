// Run workstream C's scanner (stealthlab-mcp survey) on one checkout, for the local tier of arm L1.
//
//   node survey_runner.mjs <survey.mjs path> <checkout> <since ISO date> <max commits>
//
// `since` is passed explicitly (two years before the BASE commit), because the CLI's default "2.years.ago" is
// relative to today and would truncate the history of an older checkout. Prints the summary as one JSON line.
import { pathToFileURL } from "node:url";

const [surveyPath, root, since, maxCommits] = process.argv.slice(2);
const { runSurvey } = await import(pathToFileURL(surveyPath).href);
const r = runSurvey(root, { since, maxCommits: Number(maxCommits) || 5000, history: true });
process.stdout.write(JSON.stringify({ history: r.history, units: r.units?.length ?? r.units, timing: r.timing,
  memory_mb: r.memory_mb, writes: r.writes }) + "\n");
