import type { Metadata } from "next";
import CopyField from "@/components/CopyField";
import { ConnectorAddress, underline } from "@/components/connect/GuideParts";
import { SITES, TASK_PROMPT, connectorUrl } from "@/lib/connector";

export const metadata: Metadata = { title: "Connect: ChatGPT" };

export default function ChatGptGuide() {
  const chatgpt = SITES.find((s) => s.id === "chatgpt")!;
  return (
    <section aria-labelledby="cg">
      <h2 id="cg">{chatgpt.name}</h2>
      <p>{chatgpt.needs}</p>
      <ConnectorAddress url={connectorUrl()} />
      <ol>{chatgpt.steps.map((x) => <li key={x}>{x}</li>)}</ol>
      <p><a href={chatgpt.openUrl} rel="noreferrer" style={underline}>Open {chatgpt.name} connector settings</a></p>
      <p>Then paste the instruction below into: {chatgpt.promptWhere}</p>
      <CopyField label="Instruction to paste" value={TASK_PROMPT} event="connector_prompt_copy" />
    </section>
  );
}
