import type { Metadata } from "next";
import { Steps, TryTable } from "@/components/connect/GuideParts";
import { CLAUDE_CODE_NEEDS, CLAUDE_CODE_STEPS, CLAUDE_CODE_TRY, CLAUDE_CODE_UNDO } from "@/lib/connect-guide";

export const metadata: Metadata = { title: "Connect: Claude Code" };

export default function ClaudeCodeGuide() {
  return (
    <section aria-labelledby="cc">
      <h2 id="cc">Claude Code</h2>
      <p>About 10 minutes. keळ looks at each request before Claude starts. If a cheap open model can do the work (answers, small fixes with tests, summaries), keळ hands it over and a check confirms the result. Everything else goes to Claude as usual. keळ also suggests the right outside tool, such as a browser or a GitHub server, when a task needs one.</p>
      <h3>What you need</h3>
      <ul>{CLAUDE_CODE_NEEDS.map((x) => <li key={x}>{x}</li>)}</ul>
      <Steps steps={CLAUDE_CODE_STEPS} />
      <h3>Try it</h3>
      <TryTable {...CLAUDE_CODE_TRY} />
      <h3>Undo, pause or remove it</h3>
      <ul>{CLAUDE_CODE_UNDO.map((x) => <li key={x}>{x}</li>)}</ul>
    </section>
  );
}
