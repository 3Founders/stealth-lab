"use client";
// One step's locator, as a person writes it (2026-11-18).
//
// The server has always accepted a script as a step's locator (submissions/intake.py's SCRIPT_EXT, checked and run by
// review.py and sandbox.py), but the form gave one vague box -- "a website, a file or script link, or a tool name" -- so
// nobody could tell that pointing a step at their own script was allowed, what it had to look like, or why a local path
// is refused. This splits that box into the three things the server actually distinguishes, and says the rule for each
// where the person is typing instead of after they submit.
//
// What the server enforces, which the hints mirror:
//   * a script must be a URL, not a path on the person's machine (review.py:157 -- kell cannot open your disk)
//   * only Python, shell and JavaScript can run (review.py:160 / sandbox.py RUNTIMES) -- writing .rb passes intake and
//     then fails review, so that is caught here, before the step is written
//   * the script is fetched and executed, so it must be public
import { useState } from "react";

const SCRIPT_EXT = [".py", ".sh", ".bash", ".js", ".mjs"];
const LOCAL_PATH = /^(?:[a-zA-Z]:[\\/]|\\\\|~(?:[\\/]|$)|\/(?:Users|home|root|tmp|var|opt|mnt)\b)/;

export type LocatorKind = "url" | "script" | "tool";

/** What kind a locator the person already typed looks like, so reopening a step shows the right choice. */
export function kindOf(value: string): LocatorKind {
  const v = value.trim();
  if (!v) return "url";
  const path = v.split("?")[0].split("#")[0].toLowerCase();
  if (SCRIPT_EXT.some((e) => path.endsWith(e))) return "script";
  if (!/^https?:\/\//i.test(v) && !LOCAL_PATH.test(v)) return "tool";
  return "url";
}

/** Why this locator will not work, in the words the reviewer would use. Empty when it is fine (or still being typed). */
export function locatorProblem(kind: LocatorKind, value: string): string {
  const v = value.trim();
  if (!v) return "";
  if (kind === "script") {
    if (LOCAL_PATH.test(v)) return "kell cannot open a file on your computer: link the script (a GitHub raw link works).";
    if (!/^https?:\/\//i.test(v)) return "a script locator must be a link, e.g. https://raw.githubusercontent.com/…/tool.py";
    if (!SCRIPT_EXT.some((e) => v.split("?")[0].toLowerCase().endsWith(e)))
      return "kell runs Python (.py), shell (.sh) or JavaScript (.js) scripts; this link is none of those.";
  }
  if (kind === "url" && LOCAL_PATH.test(v)) return "kell cannot open a file on your computer: give a website link instead.";
  return "";
}

const PLACEHOLDER: Record<LocatorKind, string> = {
  url: "https://example.com/page-to-read",
  script: "https://raw.githubusercontent.com/you/repo/main/tool.py",
  tool: "tool-name, or server/tool",
};

const HINT: Record<LocatorKind, string> = {
  url: "A page kell reads for this step.",
  script: "kell fetches this file and runs it. Python, shell or JavaScript, and public — a GitHub raw link works.",
  tool: "A tool kell already knows, by name.",
};

export default function LocatorField({ n, value, onChange, disabled }: {
  n: number;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  // The kind is both chosen and inferred: inferred from what is already typed (so reopening a step shows the right tab),
  // but once the person picks one it stays put, including on an empty box. Without the second half, clicking "Script"
  // before typing anything would appear to do nothing, which is how this control failed the first time it was written.
  const [chosen, setChosen] = useState<LocatorKind | null>(null);
  const kind = chosen ?? kindOf(value);
  const problem = locatorProblem(kind, value);
  const pick = (k: LocatorKind) => {
    setChosen(k);
    // A path from another kind is not carried over: a script tab holding a website reads as a mistake. The words the
    // person wrote stay where they can see them until they type over them.
    if (k === "script" && value.trim() && !SCRIPT_EXT.some((e) => value.split("?")[0].toLowerCase().endsWith(e))
        && /^https?:\/\//i.test(value.trim())) onChange("");
  };
  return (
    <div className="sw-loc-field">
      <span className="sw-seg sw-loc-kind" role="radiogroup" aria-label={`What this step needs for step ${n}`}>
        {(["url", "script", "tool"] as const).map((k) => (
          <button key={k} type="button" role="radio" aria-checked={kind === k} disabled={disabled} onClick={() => pick(k)}>
            {k === "url" ? "Link" : k === "script" ? "Script" : "Tool"}
          </button>
        ))}
      </span>
      <input aria-label={`Link or tool for step ${n}`} value={value} disabled={disabled}
        placeholder={PLACEHOLDER[kind]} onChange={(e) => onChange(e.target.value)}
        aria-invalid={problem ? true : undefined} data-problem={problem || undefined} />
      <p className="sw-dim">
        {problem
          ? <span className="sw-loc-problem">{problem}</span>
          : HINT[kind]}
      </p>
    </div>
  );
}
