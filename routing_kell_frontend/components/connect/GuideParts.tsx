import CopyField from "@/components/CopyField";
import type { Step } from "@/lib/connect-guide";

export const underline = { textDecoration: "underline" } as const;
const cell = { textAlign: "left", verticalAlign: "top", padding: "10px 14px 10px 0", borderBottom: "1px solid var(--rule)" } as const;

/** Numbered steps, each with its text, bullets, a copyable command and a note. */
export function Steps({ steps }: { steps: Step[] }) {
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

/** "Send this / What you should see" for checking a setup worked. */
export function TryTable({ intro, rows, outro }: { intro: string; rows: Array<[string, string]>; outro: string }) {
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

export function TwoColumnTable({ head, rows }: { head: [string, string]; rows: Array<[string, string]> }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", fontSize: 15, minWidth: 520 }}>
        <thead><tr><th style={cell}>{head[0]}</th><th style={cell}>{head[1]}</th></tr></thead>
        <tbody>{rows.map(([a, b]) => <tr key={a}><td style={cell}>{a}</td><td style={cell}>{b}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

/** Where the connector address goes, or why there isn't one yet. */
export function ConnectorAddress({ url }: { url: string | null }) {
  return url ? (
    <CopyField label="Connector address" value={url} event="connector_url_copy" />
  ) : (
    <p className="lead">
      The hosted server is not available yet, so there is no address to add. In the meantime, Claude Code and
      Cursor work from the <a href="/docs" style={underline}>setup command</a>.
    </p>
  );
}
