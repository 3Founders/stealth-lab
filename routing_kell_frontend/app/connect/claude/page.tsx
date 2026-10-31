import type { Metadata } from "next";
import { ConnectorAddress, Steps, TryTable, underline } from "@/components/connect/GuideParts";
import { SITES, connectorUrl } from "@/lib/connector";
import { CLAUDE_WEB_STEPS, CLAUDE_WEB_TRY } from "@/lib/connect-guide";

export const metadata: Metadata = { title: "Connect: Claude on the web" };

export default function ClaudeWebGuide() {
  const claude = SITES.find((s) => s.id === "claude")!;
  return (
    <section aria-labelledby="cw">
      <h2 id="cw">Claude on the web (claude.ai)</h2>
      <p>About 5 minutes. On the web, keळ is a connector: Claude asks it what has worked for similar tasks, which tool fits, and whether a cheap model can write part of the answer. keळ can also make images. {claude.needs}</p>
      <ConnectorAddress url={connectorUrl()} />
      <Steps steps={CLAUDE_WEB_STEPS} />
      <p><a href={claude.openUrl} rel="noreferrer" style={underline}>Open Claude connector settings</a></p>
      <h3>Try it</h3>
      <TryTable {...CLAUDE_WEB_TRY} />
    </section>
  );
}
