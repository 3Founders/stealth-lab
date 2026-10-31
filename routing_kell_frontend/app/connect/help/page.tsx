import type { Metadata } from "next";
import { TwoColumnTable, underline } from "@/components/connect/GuideParts";
import { TROUBLE } from "@/lib/connect-guide";

export const metadata: Metadata = { title: "Connect: if something goes wrong" };

export default function HelpGuide() {
  return (
    <section aria-labelledby="help">
      <h2 id="help">If something goes wrong</h2>
      <TwoColumnTable head={["What you see", "What to do"]} rows={TROUBLE} />
      <p>For anything else, <a href="/contact" style={underline}>send the keळ team</a> what you typed, what you saw, and (for Claude Code) the last lines of <code>~/.stealthlab/hooks.log</code>.</p>
    </section>
  );
}
