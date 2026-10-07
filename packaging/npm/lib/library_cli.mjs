// `stealthlab-mcp library <command>`: deterministic upkeep of .stealth/library.md, its indexes, SUMMARY.md and
// routing.md (lib/library.mjs). Everything runs locally; nothing is sent anywhere. Output is JSON on stdout
// (agents read it); errors go to stderr with exit 1.
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { parseArgs } from "node:util";
import * as L from "./library.mjs";

export const LIBRARY_HELP = `stealthlab-mcp library <command> [--root <repo>]   (.stealth/library.md upkeep; local only)

  index                         canonicalise library.md, rebuild index/library.idx, index/terms.idx, SUMMARY.md
  check                         mark entries stale whose touched files changed (git hash-object)
  show <L-id>                   print one entry (idx range checked against its hash; self-healing)
  refresh <L-id>                after re-checking a stale entry: current file shas, status current
  add --title <goal> --check <cmd> [--diff-base <rev> | --diff-file <path>] [--unit <path>] [--g <goal id>]
      [--p <procedure id>] [--name <way>] [--step "<kind>|<do>|<check>"]... [--tags a,b]
      [--outcome pass|historical|fail] [--route <R-id>] [--no-verify]
                                write back a solved problem: runs --check first and refuses if it fails;
                                the diff (default: git diff HEAD + untracked files) goes to library/solutions/
  route --from-reply <file|->   write find_ways' routing_rows into routing.md (or --line <ROUTE line>...)
  obs <R-id> --model <m> --scaffold <s> (--ok | --fail)
                                count one attempt on a route (what find_ways' route_obs sends back)
  payload                       the find_ways arguments the knowledge hook sends (library_rows, route_obs,
                                repo_identity) -- to see exactly what leaves this machine
`;

function repoRoot(start) {
  try {
    return execFileSync("git", ["-C", start, "rev-parse", "--show-toplevel"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return start;
  }
}

function readInput(src) {
  return src === "-" ? fs.readFileSync(0, "utf8") : fs.readFileSync(src, "utf8");
}

export function parseStep(spec) {
  const [kind, doText, check] = String(spec).split("|");
  if (!["action", "instruction", "subgoal"].includes(kind) || !doText) {
    throw new Error(`--step is "<action|instruction|subgoal>|<what to do>|<check or empty>", got ${JSON.stringify(spec)}`);
  }
  return { kind, do: doText, check: check || null };
}

export async function runLibraryCli(argv, { cwd = process.cwd(), print = (o) => process.stdout.write(JSON.stringify(o, null, 2) + "\n") } = {}) {
  const [command, ...rest] = argv;
  const { values: v, positionals } = parseArgs({
    args: rest, allowPositionals: true, strict: true,
    options: {
      root: { type: "string" }, title: { type: "string" }, unit: { type: "string" }, g: { type: "string" },
      p: { type: "string" }, name: { type: "string" }, step: { type: "string", multiple: true },
      tags: { type: "string" }, outcome: { type: "string" }, route: { type: "string" }, check: { type: "string" },
      "diff-base": { type: "string" }, "diff-file": { type: "string" }, "no-verify": { type: "boolean" },
      "from-reply": { type: "string" }, line: { type: "string", multiple: true },
      model: { type: "string" }, scaffold: { type: "string" }, ok: { type: "boolean" }, fail: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });
  if (!command || v.help || command === "help") return process.stderr.write(LIBRARY_HELP);
  const root = path.resolve(v.root || repoRoot(cwd));
  switch (command) {
    case "index":
      return print(L.buildIndex(root));
    case "check":
      return print({ changed: L.checkStaleness(root) });
    case "show": {
      const e = L.readEntry(root, positionals[0]);
      if (!e) throw new Error(`no library entry ${positionals[0]}`);
      return print(e);
    }
    case "refresh":
      return print(L.refreshEntry(root, positionals[0]));
    case "add": {
      const diff = v["diff-file"] ? readInput(v["diff-file"]) : L.diffFromGit(root, v["diff-base"] || "HEAD");
      return print(L.addEntry(root, {
        title: v.title, unit: v.unit, g: v.g, p: v.p, name: v.name, outcome: v.outcome, route: v.route,
        tags: v.tags ? v.tags.split(",").map((t) => t.trim()).filter(Boolean) : [],
        steps: (v.step || []).map(parseStep), diff, check: v.check, verify: !v["no-verify"],
      }));
    }
    case "route": {
      let lines = v.line || [];
      if (v["from-reply"]) {
        const reply = JSON.parse(readInput(v["from-reply"]));
        lines = [...lines, ...(reply.routing_rows || [])];
      }
      if (!lines.length) throw new Error("nothing to write: pass --from-reply <find_ways reply> or --line <ROUTE line>");
      return print({ written: L.upsertRoutes(root, lines) });
    }
    case "obs": {
      if (!v.model || !v.scaffold || v.ok === v.fail) throw new Error("obs needs <R-id> --model --scaffold and exactly one of --ok / --fail");
      L.recordObs(root, positionals[0], v.model, v.scaffold, Boolean(v.ok));
      return print({ recorded: true });
    }
    case "payload":
      return print(L.requestPayload(root));
    default:
      throw new Error(`unknown library command "${command}"\n\n${LIBRARY_HELP}`);
  }
}
