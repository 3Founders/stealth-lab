"use client";
import type { ToolApproval } from "@/lib/tools";

/** One pending tool call in the desktop app (P1-11). The approval is scoped to this exact call shape
 *  and is single-use; "Allow once" spends it. Arguments are shown by hash: the row never stores them. */
export function ApprovalCard({ approval, busy, onApprove, onDeny }: {
  approval: ToolApproval;
  busy?: boolean;
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
}) {
  const risk = String(approval.risk || "R2").toUpperCase();
  const hash = String(approval.args_hash || "").replace(/^sha256:/, "").slice(0, 12);
  const expires = (() => {
    const t = Date.parse(approval.expires_at);
    return Number.isFinite(t) ? new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "soon";
  })();
  const reversible = risk === "R0" || risk === "R1" ? "Read-only or reversible" : "Needs your approval";

  return (
    <div role="alert" style={{
      border: "1px solid rgba(120,120,140,.35)", borderRadius: 12, padding: "12px 14px",
      marginBottom: 8, background: "rgba(120,120,140,.06)", fontSize: 13,
    }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <b>{approval.tool || approval.server}</b>
        <span style={{ opacity: .65 }}>{approval.server}</span>
        <span style={{
          marginLeft: "auto", fontWeight: 600, fontSize: 11, padding: "2px 7px", borderRadius: 999,
          border: "1px solid currentColor", color: risk === "R3" || risk === "R4" ? "#c2410c" : "#2563eb",
        }}>{risk}</span>
      </div>
      <div style={{ marginTop: 6, opacity: .8 }}>A tool wants to run for this task. Review it before allowing it.</div>
      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 10px", margin: "8px 0 10px" }}>
        <dt style={{ opacity: .6 }}>Target</dt><dd style={{ margin: 0 }}>{approval.server}</dd>
        <dt style={{ opacity: .6 }}>Data</dt><dd style={{ margin: 0 }}>{approval.data_class}</dd>
        <dt style={{ opacity: .6 }}>Arguments</dt><dd style={{ margin: 0 }}>sha256:{hash}…</dd>
        <dt style={{ opacity: .6 }}>Scope</dt><dd style={{ margin: 0 }}>{reversible}</dd>
        <dt style={{ opacity: .6 }}>Cost</dt><dd style={{ margin: 0 }}>not priced yet</dd>
        <dt style={{ opacity: .6 }}>Expires</dt><dd style={{ margin: 0 }}>{expires}</dd>
      </dl>
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" disabled={busy} onClick={() => onApprove(approval.id)}
          style={{ padding: "6px 12px", borderRadius: 8, border: 0, background: "#1f6feb", color: "#fff", cursor: "pointer" }}>
          Allow once
        </button>
        <button type="button" disabled={busy} onClick={() => onDeny(approval.id)}
          style={{ padding: "6px 12px", borderRadius: 8, border: "1px solid rgba(120,120,140,.45)", background: "transparent", cursor: "pointer" }}>
          Deny
        </button>
      </div>
    </div>
  );
}
