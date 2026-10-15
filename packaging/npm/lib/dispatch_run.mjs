// .stealth/run.md for a dispatched task: the plan StealthLab followed, in the plan_and_run NODE format (lib/plan_check.mjs),
// so the session's model can pick up where the cheap tries stopped instead of starting from nothing:
//   N-1 locate   where the task is (lib/locate.mjs)                     done
//   N-2 tests    tests written in advance that fail on the unchanged code   done / skipped (none could be written)
//   N-3 change   the change itself, checked by N-2's tests (+ the module's own tests)   done (a cheap model) / ready (yours)
// run.md belongs to the planner (the agent, plan_and_run): one that StealthLab dispatch did not write is never touched.
import fs from "node:fs";
import path from "node:path";

export const RUN_HEAD = "# run.md -- written by StealthLab dispatch for the task below; plan_and_run may replace it.";

const clean = (s, n = 160) => String(s || "").replace(/[|\r\n]+/g, " ").replace(/\s+/g, " ").trim().slice(0, n);

export function renderDispatchRun({ task, loc, specFile, regressFile, python = "python", winner = null, tried = [] }) {
  const spots = (loc?.spots || []).map((s) => `${s.file}:${s.start}-${s.end}${s.symbol ? ` (${s.symbol})` : ""}`).join(", ");
  const first = (loc?.spots || [])[0];
  const specCheck = specFile ? `${python} -m pytest -q -p no:cacheprovider "${specFile}"` : null;
  const changeCheck = [specCheck, regressFile ? `${python} -m pytest -q -x -p no:cacheprovider ${regressFile}` : null].filter(Boolean).join(" && ");
  const triedText = tried.length ? ` Cheap tries: ${tried.map((t) => `${String(t.unit).split("|")[0]} ${clean(t.state, 40)}`).join("; ")}.` : "";
  return [
    RUN_HEAD,
    `# task: ${clean(task, 300)}`,
    `NODE|N-1|${first ? "done" : "skipped"}|Locate the code the task is about${spots ? `: ${clean(spots, 300)}` : " (nothing found)"}|step=-|claims=-|deps=-|check=${first ? `test -f ${first.file}` : "-"}`,
    `NODE|N-2|${specFile ? "done" : "skipped"}|Tests written in advance for the task (they fail on the unchanged code)${specFile ? "" : ": none could be written"}|step=-|claims=-|deps=N-1|check=${specCheck || "-"}`,
    `NODE|N-3|${winner ? "done" : "ready"}|${clean(`Make the change${winner ? ` (done by ${winner})` : ""}.${triedText}`, 360)}|step=-|claims=-|deps=N-2|check=${changeCheck || "the task's own check"}`,
    "",
  ].join("\n");
}

// A task done in parts (lib/dag_run.mjs): one NODE per part, its deps, the model that did it (or "session": yours).
export function renderDagRun({ task, nodes, entry, interCheck, fullCheck }) {
  const last = nodes.length;
  return [
    RUN_HEAD,
    `# task: ${clean(task, 300)}`,
    `# plan: from ${entry} (a similar fix already made here); parts with no deps between them can run in parallel`,
    ...nodes.map((n, i) => `NODE|${n.id}|${n.status}|${clean(`The change in ${n.at.join(", ")}${n.gives?.length ? ` (past fix: ${n.gives.slice(0, 4).join(", ")})` : ""}`, 200)}` +
      `|step=-|claims=-|deps=${n.deps.length ? n.deps.join(",") : "-"}|model=${clean(n.who || "session", 60)}|check=${i === last - 1 && fullCheck ? fullCheck : interCheck || "-"}`),
    "",
  ].join("\n");
}

export function writeDagRun(root, args) {
  return writeRunText(root, renderDagRun(args));
}

function writeRunText(root, text) {
  try {
    const p = path.join(root, ".stealth", "run.md");
    if (!fs.existsSync(path.dirname(p))) return false;
    if (fs.existsSync(p) && !fs.readFileSync(p, "utf8").startsWith(RUN_HEAD)) return false;
    fs.writeFileSync(p, text);
    return true;
  } catch {
    return false;
  }
}

// -> true when written; false when run.md is the planner's own (not written by dispatch) or .stealth is missing
export function writeDispatchRun(root, args) {
  try {
    const p = path.join(root, ".stealth", "run.md");
    if (!fs.existsSync(path.dirname(p))) return false;
    if (fs.existsSync(p) && !fs.readFileSync(p, "utf8").startsWith(RUN_HEAD)) return false;
    fs.writeFileSync(p, renderDispatchRun(args));
    return true;
  } catch {
    return false;
  }
}
