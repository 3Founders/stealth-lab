// `stealthlab-mcp library <command>`: deterministic upkeep of .stealth/library.md, its indexes, SUMMARY.md and
// routing.md (lib/library.mjs). Everything runs locally; nothing is sent anywhere. Output is JSON on stdout
// (agents read it); errors go to stderr with exit 1.
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { parseArgs } from "node:util";
import * as L from "./library.mjs";
import * as T from "./library_tidy.mjs";

export const LIBRARY_HELP = `stealthlab-mcp library <command> [--root <repo>]   (.stealth/library.md upkeep; local only)

  index                         canonicalise library.md, rebuild index/library.idx, index/terms.idx, SUMMARY.md
  check                         mark entries stale whose touched files changed (git hash-object)
  show <L-id>                   print one entry (idx range checked against its hash; self-healing)
  refresh <L-id>                after re-checking a stale entry: current file shas, status current
  add --title <goal> --check <cmd> [--diff-base <rev> | --diff-file <path>] [--unit <path>] [--g <goal id>]
      [--p <procedure id>] [--name <way>] [--step "<kind>|<do>|<check>"]... [--tags a,b]
      [--goal <G-id>] [--way <W-id>]  (attach to an existing Goal / Way; otherwise found or created)
      [--outcome pass|historical|fail] [--route <R-id>] [--no-verify]
                                write back a solved problem: runs --check first and refuses if it fails;
                                the diff (default: git diff HEAD + untracked files) goes to library/solutions/
  route --from-reply <file|->   write find_ways' routing_rows into routing.md (or --line <ROUTE line>...)
  obs <R-id> --model <m> --scaffold <s> (--ok | --fail)
                                count one attempt on a route (what find_ways' route_obs sends back)
  link                          give every entry a reusable Goal (G-) and Way (W-): the same problem shares one
                                Goal, the same procedure one Way (content-hash ids, merge-safe); then re-index
  goal <G-id> --parent <G-id|-> place a Goal under a parent Goal (or clear it); cycles are refused
  share <L-id>                  draft the submit_way call that offers this entry to everyone: problem, way and
                                steps with checks only (never the diff or paths); you fill the rest and send it
  payload                       the find_ways arguments the knowledge hook sends (library_rows, route_obs,
                                repo_identity) -- to see exactly what leaves this machine
  lint                          dangling goal/way/parent links, parent cycles, missing diffs, size vs the 64 KB cap;
                                exit 1 on an error (read-only)
  tidy [--write]                what to clean: duplicate entries and goals, stale entries, goals that look narrower
                                than another, what to archive first -- each with the command to run (read-only);
                                --write also saves the worklist to .stealth/library/TIDY.md for the agent
  drop <L-id> [--superseded-by <L-id>]
                                move a duplicate or outdated entry to library/archive-superseded.md (greppable, out
                                of the index); its diff stays. A union merge can bring it back: run tidy again
  merge-goals <keep G-id> <drop G-id>
                                the same problem under two titles: entries, ways and child goals move to <keep>
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
      parent: { type: "string" }, goal: { type: "string" }, way: { type: "string" },
      "superseded-by": { type: "string" }, write: { type: "boolean" },
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
        goal: v.goal, way: v.way,
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
    case "link": {
      const linked = L.linkLibrary(root);
      let settings = {};
      try {
        const { resolveSettings } = await import("./config.mjs");
        settings = resolveSettings({});
        if (settings.token) {
          const { freshToken } = await import("./oauth.mjs");
          settings.token = (await freshToken(settings).catch(() => null)) || settings.token;
        }
      } catch { /* not configured: Ways stay uncoded */ }
      return print({ ...linked, codes: await L.codeWays(root, { url: settings.url, token: settings.token }) });
    }
    case "goal":
      if (!v.parent) throw new Error("library goal <G-id> --parent <G-id or ->");
      return print(L.setGoalParent(root, positionals[0], v.parent));
    case "share":
      return print(L.shareDraft(root, positionals[0]));
    case "lint": {
      const report = T.lintLibrary(root);
      print(report);
      if (!report.ok) process.exitCode = 1;
      return;
    }
    case "tidy": {
      const report = T.tidyReport(root);
      if (v.write) {
        const file = path.join(L.stealthDir(root), "library", "TIDY.md");
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.writeFileSync(file, T.renderTidy(report));
        report.written = path.relative(root, file).split(path.sep).join("/");
      }
      return print(report);
    }
    case "drop":
      if (!positionals[0]) throw new Error("library drop <L-id> [--superseded-by <L-id>]");
      return print(L.dropEntry(root, positionals[0], { supersededBy: v["superseded-by"] || null }));
    case "merge-goals":
      if (!positionals[0] || !positionals[1]) throw new Error("library merge-goals <keep G-id> <drop G-id>");
      return print(L.mergeGoals(root, positionals[0], positionals[1]));
    default:
      throw new Error(`unknown library command "${command}"\n\n${LIBRARY_HELP}`);
  }
}
