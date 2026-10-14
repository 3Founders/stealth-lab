"use client";
import { useEffect, useState } from "react";
import { DEFAULT_THRESHOLDS, type Thresholds } from "@/lib/exec";
import { resetThresholds, saveThresholds, sameThresholds, validateThresholds } from "@/lib/thresholds";

const inputStyle = { font: "inherit", fontSize: 15, padding: "6px 8px", border: "1px solid var(--rule-strong)", borderRadius: 3, background: "rgba(255,255,255,.5)", width: 90 } as const;

/**
 * What this view flags. Kept in this browser for this organisation; it changes the highlighting only. What the
 * organisation actually enforces (budgets, allow-lists, the kill switch) is set under Policy.
 */
export default function ThresholdSettings({ orgId, value, onChange }: { orgId: string; value: Thresholds; onChange: (t: Thresholds) => void }) {
  const [draft, setDraft] = useState({ watch: "", action: "", warn: "", urgent: "" });
  const [msg, setMsg] = useState("");

  useEffect(() => {
    setDraft({ watch: String(+(value.failureWatch * 100).toFixed(2)), action: String(+(value.failureAction * 100).toFixed(2)), warn: String(value.budgetWarnPct), urgent: String(value.budgetUrgentPct) });
  }, [value]);

  function save(e: React.FormEvent) {
    e.preventDefault();
    const next = validateThresholds({ failureWatch: Number(draft.watch) / 100, failureAction: Number(draft.action) / 100, budgetWarnPct: Number(draft.warn), budgetUrgentPct: Number(draft.urgent) });
    if (!next) { setMsg("Not saved: the failure watch level must be above 0 and below the action level (at most 100%), and the budget warning must be below the urgent level, which must be under 100%."); return; }
    if (!saveThresholds(orgId, next)) { setMsg("Couldn’t save in this browser (storage may be blocked)."); return; }
    setMsg("Saved for this browser.");
    onChange(next);
  }

  function reset() {
    resetThresholds(orgId);
    setMsg("Back to the defaults.");
    onChange(DEFAULT_THRESHOLDS);
  }

  return (
    <details className="no-print" style={{ gridColumn: "1 / span 12", border: "1px solid var(--rule)", padding: "10px 16px" }}>
      <summary style={{ cursor: "pointer" }}>Alert thresholds{sameThresholds(value, DEFAULT_THRESHOLDS) ? " (defaults)" : " (customised)"}</summary>
      <form onSubmit={save} style={{ display: "grid", gap: 12, marginTop: 12, maxWidth: 640 }}>
        <p className="small dim" style={{ margin: 0 }}>These decide what this page highlights. They are saved in this browser only and do not change what the organisation enforces; budgets and limits are set under Policy.</p>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <label className="small">Failure rate: watch at (%)<br /><input style={inputStyle} inputMode="decimal" value={draft.watch} onChange={(e) => setDraft({ ...draft, watch: e.target.value })} /></label>
          <label className="small">Failure rate: action at (%)<br /><input style={inputStyle} inputMode="decimal" value={draft.action} onChange={(e) => setDraft({ ...draft, action: e.target.value })} /></label>
          <label className="small">Budget used: warn at (%)<br /><input style={inputStyle} inputMode="decimal" value={draft.warn} onChange={(e) => setDraft({ ...draft, warn: e.target.value })} /></label>
          <label className="small">Budget used: urgent at (%)<br /><input style={inputStyle} inputMode="decimal" value={draft.urgent} onChange={(e) => setDraft({ ...draft, urgent: e.target.value })} /></label>
        </div>
        <div className="row-actions">
          <button type="submit" className="btn-line">Save</button>
          <button type="button" className="btn-line" onClick={reset}>Reset to defaults</button>
        </div>
        {msg && <p className="small" role="status" style={{ margin: 0 }}>{msg}</p>}
      </form>
    </details>
  );
}
