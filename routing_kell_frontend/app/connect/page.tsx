import type { Metadata } from "next";
import AnimatedHeading from "@/components/AnimatedHeading";
import CopyField from "@/components/CopyField";
import { SITES, TASK_PROMPT, connectorUrl } from "@/lib/connector";

export const metadata: Metadata = { title: "Connect" };

export default function Connect() {
  const url = connectorUrl();
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONNECT</b></div>
        <h1 className="display"><AnimatedHeading>Connect</AnimatedHeading></h1>
        <p className="lead">Use keळ from Claude on the web or ChatGPT. Add it once as a custom connector, then paste one instruction.</p>
      </section>

      <div className="frame" style={{ display: "grid", gap: "28px", paddingBottom: "80px" }}>
        {url ? (
          <CopyField label="Connector address" value={url} event="connector_url_copy" />
        ) : (
          <p className="lead">
            The hosted server is not available yet, so there is no address to add. In the meantime, Claude Code and
            Cursor work from the <a href="/docs" style={{ textDecoration: "underline" }}>setup command</a>.
          </p>
        )}

        {SITES.map((s) => (
          <section key={s.id} id={s.id}>
            <h2>{s.name}</h2>
            <p>{s.needs}</p>
            <ol>{s.steps.map((x) => <li key={x}>{x}</li>)}</ol>
            <p><a href={s.openUrl} rel="noreferrer" style={{ textDecoration: "underline" }}>Open {s.name} connector settings</a></p>
            <p>Then paste the instruction below into: {s.promptWhere}</p>
          </section>
        ))}

        <CopyField label="Instruction to paste" value={TASK_PROMPT} event="connector_prompt_copy" />
      </div>
    </>
  );
}
