import type { Metadata } from "next";
import AnimatedHeading from "@/components/AnimatedHeading";
import CopyField from "@/components/CopyField";
import { SITES, TASK_PROMPT, connectorUrl } from "@/lib/connector";
import {
  CLAUDE_CODE_NEEDS, CLAUDE_CODE_STEPS, CLAUDE_CODE_TRY, CLAUDE_CODE_UNDO,
  CLAUDE_WEB_STEPS, CLAUDE_WEB_TRY, TROUBLE, type Step,
} from "@/lib/connect-guide";

export const metadata: Metadata = { title: "Connect" };

const link = { textDecoration: "underline" } as const;

function Steps({ steps }: { steps: Step[] }) {
  return (
    <ol style={{ display: "grid", gap: "28px", paddingLeft: "1.4em" }}>
      {steps.map((s) => (
        <li key={s.title}>
          <h3 style={{ marginBottom: 8 }}>{s.title}</h3>
          {s.body && <p>{s.body}</p>}
          {s.list && <ul style={{ margin: "8px 0", paddingLeft: "1.2em" }}>{s.list.map((x) => <li key={x}>{x}</li>)}</ul>}
          {s.code && <div style={{ margin: "12px 0" }}><CopyField label={s.codeLabel ?? s.title} value={s.code} event="connect_guide_copy" /></div>}
          {s.note && <p style={{ color: "var(--grey)" }}>{s.note}</p>}
        </li>
      ))}
    </ol>
  );
}

function TryTable({ intro, rows, outro }: { intro: string; rows: Array<[string, string]>; outro: string }) {
  return (
    <>
      <p>{intro}</p>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 15, minWidth: 520 }}>
          <thead><tr><th style={cell}>Send this</th><th style={cell}>What you should see</th></tr></thead>
          <tbody>{rows.map(([a, b]) => <tr key={a}><td style={cell}><code>{a}</code></td><td style={cell}>{b}</td></tr>)}</tbody>
        </table>
      </div>
      <p>{outro}</p>
    </>
  );
}
const cell = { textAlign: "left", verticalAlign: "top", padding: "10px 14px 10px 0", borderBottom: "1px solid var(--rule)" } as const;

export default function Connect() {
  const url = connectorUrl();
  const chatgpt = SITES.find((s) => s.id === "chatgpt")!;
  const claude = SITES.find((s) => s.id === "claude")!;
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONNECT</b></div>
        <h1 className="display"><AnimatedHeading>Connect</AnimatedHeading></h1>
        <p className="lead">
          Set up keळ in Claude Code, Claude on the web or ChatGPT. Each guide is written for someone who has never used keळ:
          follow the steps in order and type exactly what is shown.
        </p>
        <p style={{ gridColumn: "1 / -1" }}>
          <a href="#claude-code" style={link}>Claude Code</a> · <a href="#claude" style={link}>Claude on the web</a> · <a href="#chatgpt" style={link}>ChatGPT</a> · <a href="#trouble" style={link}>If something goes wrong</a>
        </p>
      </section>

      <div className="frame" style={{ display: "grid", gap: "40px", paddingBottom: "80px" }}>
        <section id="claude-code">
          <h2>Claude Code</h2>
          <p>About 10 minutes. keळ looks at each request before Claude starts. If a cheap open model can do the work (answers, small fixes with tests, summaries), keळ hands it over and a check confirms the result. Everything else goes to Claude as usual. keळ also suggests the right outside tool, such as a browser or a GitHub server, when a task needs one.</p>
          <h3>What you need</h3>
          <ul>{CLAUDE_CODE_NEEDS.map((x) => <li key={x}>{x}</li>)}</ul>
          <Steps steps={CLAUDE_CODE_STEPS} />
          <h3>Try it</h3>
          <TryTable {...CLAUDE_CODE_TRY} />
          <h3>Undo, pause or remove it</h3>
          <ul>{CLAUDE_CODE_UNDO.map((x) => <li key={x}>{x}</li>)}</ul>
        </section>

        <section id="claude">
          <h2>Claude on the web (claude.ai)</h2>
          <p>About 5 minutes. On the web, keळ is a connector: Claude asks it what has worked for similar tasks, which tool fits, and whether a cheap model can write part of the answer. keळ can also make images. {claude.needs}</p>
          {url ? (
            <CopyField label="Connector address" value={url} event="connector_url_copy" />
          ) : (
            <p className="lead">
              The hosted server is not available yet, so there is no address to add. In the meantime, Claude Code and
              Cursor work from the <a href="/docs" style={link}>setup command</a>.
            </p>
          )}
          <Steps steps={CLAUDE_WEB_STEPS} />
          <p><a href={claude.openUrl} rel="noreferrer" style={link}>Open Claude connector settings</a></p>
          <h3>Try it</h3>
          <TryTable {...CLAUDE_WEB_TRY} />
        </section>

        <section id={chatgpt.id}>
          <h2>{chatgpt.name}</h2>
          <p>{chatgpt.needs}</p>
          {url && <CopyField label="Connector address" value={url} event="connector_url_copy" />}
          <ol>{chatgpt.steps.map((x) => <li key={x}>{x}</li>)}</ol>
          <p><a href={chatgpt.openUrl} rel="noreferrer" style={link}>Open {chatgpt.name} connector settings</a></p>
          <p>Then paste the instruction below into: {chatgpt.promptWhere}</p>
          <CopyField label="Instruction to paste" value={TASK_PROMPT} event="connector_prompt_copy" />
        </section>

        <section id="trouble">
          <h2>If something goes wrong</h2>
          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", fontSize: 15, minWidth: 520 }}>
              <thead><tr><th style={cell}>What you see</th><th style={cell}>What to do</th></tr></thead>
              <tbody>{TROUBLE.map(([a, b]) => <tr key={a}><td style={cell}>{a}</td><td style={cell}>{b}</td></tr>)}</tbody>
            </table>
          </div>
          <p>For anything else, <a href="/contact" style={link}>send the keळ team</a> what you typed, what you saw, and (for Claude Code) the last lines of <code>~/.stealthlab/hooks.log</code>.</p>
        </section>
      </div>
    </>
  );
}
