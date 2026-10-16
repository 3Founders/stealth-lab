// What a developer who knows the repository does before changing it, as a short instruction for the cheap agent: read
// how the code is tested now, look at how the project's maintainers have changed this code, reproduce the problem, and
// keep the project's conventions. Pointers, not pasted content: the task text has 2,000 characters, and the agent has
// the tools to read the test file and run git log itself.
//
// Why: on 26 real issues the cheap model was in the right file 39 of 44 times it was wrong, and the wrong answers were
// guessed interfaces the issue does not name (the hidden test sits in the module's existing test file and uses the
// project's names), a new required parameter that broke every existing caller, error messages unlike the project's,
// debug files left behind, and fixes never run (survey stage 3, 2026-10-16). STEALTHLAB_DISPATCH_CONTEXT_PACK=on.
export const contextPackEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_CONTEXT_PACK || "off").toLowerCase() === "on";

// files: the located files (most likely first); testFile: the module's existing tests (lib/locate.mjs testFile)
export function contextPack({ files = [], testFile = null } = {}) {
  const f = files.filter(Boolean).slice(0, 2);
  const steps = [];
  if (testFile) steps.push(`read ${testFile}: how this code is tested now -- add the test for this change there, in its style`);
  if (f.length) steps.push(`run \`git log -n 3 -p -- ${f.join(" ")}\` to see how this project changes this code`);
  steps.push("reproduce the problem first (the report's own example, or a small script) and check that your fix changes the result");
  return `Before you edit: ${steps.map((s, i) => `(${i + 1}) ${s}`).join("; ")}. Keep the project's conventions: existing function ` +
    "signatures stay compatible (a new parameter is optional, defaulting to the old behaviour); new errors and messages follow " +
    "the style already used nearby; remove any scratch or debug files you made.";
}
