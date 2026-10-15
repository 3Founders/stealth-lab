// A solved problem's diff -> the steps of its Way, in the same shape as a global Procedure's steps: one step per source
// file it changed, each with
//   at=    the file (the step's locator)
//   gives= the names it defines or changes there (from the added lines and the hunks' enclosing functions): its effects
//   needs= the names it uses that ANOTHER step gives: its preconditions
// ordered so a step comes after the steps it needs (ties: the order of the diff). Deterministic, no model: the edges of
// the plan built from it (lib/dag.mjs) rest on what the code actually did, not on a description of it.
//
// Why steps at all, when steps alone did not help the models (docs/knowledge_side_improvements.md): they are the SHAPE
// of the plan -- how many units of work, which file each one is in, which must come first -- and each step keeps its
// own piece of the real diff, the worked example that did help (+8 to +10 points).
import path from "node:path";

const TEST_OR_DOC = /(^|\/)(tests?|testing|[\w.-]*_tests?|docs?|examples?)\/|(^|\/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$|\.(md|rst|txt)$|(^|\/)(CHANGELOG|CHANGES|HISTORY|README)/i;
const DEF = /^\s*(?:async\s+def|def|class|function|export\s+(?:async\s+)?function|export\s+class|func|fn|pub\s+fn)\s+([A-Za-z_][A-Za-z0-9_]*)/;
const ASSIGN = /^([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=(?!=)/;          // module-level NAME = ... (no indentation)
const HUNK_CTX = /^@@[^@]*@@\s*(?:.*?\b(?:def|class|function|func|fn)\s+)?([A-Za-z_][A-Za-z0-9_]*)?/;
const WORD = /[A-Za-z_][A-Za-z0-9_]{2,}/g;
const COMMON = new Set(("self cls None True False return import from def class pass else elif while for not and with " +
  "this that const let var function async await new null undefined true false").split(" "));

// a git diff -> [{file, text}] in diff order (one entry per file)
export function splitDiff(diff) {
  const out = [];
  let cur = null;
  for (const line of String(diff || "").split(/\r?\n/)) {
    const m = /^diff --git a\/(\S+) b\/(\S+)/.exec(line);
    if (m) { cur = { file: m[2], lines: [line] }; out.push(cur); continue; }
    if (cur) cur.lines.push(line);
  }
  return out.map((f) => ({ file: f.file, text: f.lines.join("\n") }));
}

export const isSourceFile = (f) => !TEST_OR_DOC.test(f);

// what one file's hunks define/change and use
function fileSymbols(text) {
  const gives = new Set(), uses = new Set();
  let added = 0;
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("@@")) {
      const ctx = HUNK_CTX.exec(line);
      if (ctx?.[1] && /\b(def|class|function|func|fn)\b/.test(line)) gives.add(ctx[1]);
      continue;
    }
    if (!/^[+-]/.test(line) || /^(\+\+\+|---) /.test(line)) continue;
    const body = line.slice(1);
    if (line.startsWith("+")) added++;
    const d = DEF.exec(body)?.[1] || ASSIGN.exec(body)?.[1];
    if (d) gives.add(d);
    if (line.startsWith("+")) for (const w of body.match(WORD) || []) if (!COMMON.has(w)) uses.add(w);
  }
  return { gives, uses, added };
}

// diff -> [{order, kind, do, at: [file], gives: [..], needs: [..], added}] (source files only; [] when none)
export function stepsFromDiff(diff) {
  const files = splitDiff(diff).filter((f) => isSourceFile(f.file));
  if (!files.length) return [];
  const info = files.map((f, i) => ({ i, file: f.file, ...fileSymbols(f.text) }));
  for (const a of info) {
    a.needs = new Set();
    for (const b of info) {
      if (a === b) continue;
      for (const g of b.gives) if (a.uses.has(g) && !a.gives.has(g)) a.needs.add(g);
    }
  }
  // order: a step after every step that gives what it needs (Kahn; ties by diff order; a cycle keeps diff order)
  const givers = (a) => info.filter((b) => b !== a && [...a.needs].some((n) => b.gives.has(n)));
  const done = [], left = [...info];
  while (left.length) {
    const ready = left.filter((a) => givers(a).every((b) => done.includes(b)));
    const next = ready.length ? ready[0] : left[0];
    done.push(next);
    left.splice(left.indexOf(next), 1);
  }
  return done.map((a, k) => {
    const g = [...a.gives].slice(0, 6);
    return {
      order: k + 1, kind: "action", at: [a.file], gives: [...a.gives].slice(0, 12), needs: [...a.needs].slice(0, 12), added: a.added,
      do: `In ${a.file}: change ${g.length ? g.join(", ") : path.posix.basename(a.file)}`,
    };
  });
}

// the escaped k=v extra fields library.md keeps on a STEP / S line
const item = (v) => String(v).replace(/%/g, "%25").replace(/\|/g, "%7C").replace(/,/g, "%2C").replace(/#/g, "%23").replace(/\n/g, "%0A");
export function stepExtras(s) {
  const f = (k, list) => (list && list.length ? [`${k}=${list.map(item).join(",")}`] : []);
  return [...f("at", s.at), ...f("gives", s.gives), ...f("needs", s.needs)];
}

// a step's extra fields back into lists
export function readStepExtras(extra = []) {
  const out = { at: [], gives: [], needs: [] };
  for (const f of extra) {
    const m = /^(at|gives|needs)=(.*)$/.exec(String(f));
    if (m && m[2] && m[2] !== "-") out[m[1]] = m[2].split(",").map((x) => decodeURIComponent(x.replace(/%(?![0-9A-Fa-f]{2})/g, "%25")));
  }
  return out;
}

// the steps addEntry stores: the file steps (with their extras); the check step is added by addEntry itself
export function wayStepsForEntry(diff) {
  return stepsFromDiff(diff).map((s) => ({ kind: s.kind, do: s.do, extra: stepExtras(s) }));
}

// one step's piece of a past diff (the files it is at), cut to max characters
export function diffPiece(diff, files, max = 900) {
  const want = new Set(files);
  const text = splitDiff(diff).filter((f) => want.has(f.file)).map((f) => f.text).join("\n");
  return text.length <= max ? text : `${text.slice(0, max - 20)}\n[...cut]`;
}
