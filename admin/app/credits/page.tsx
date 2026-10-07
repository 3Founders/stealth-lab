"use client";
import { useState } from "react";
import { AdminNotice, Hero, Stat, str } from "@/components/Admin";
import { CsvButton } from "@/components/Widgets";
import { clawbackCredit, getCreditBalance, getCreditHistory, type ApiState, type Row } from "@/lib/admin-api";

const reasonStyle = { font: "inherit", fontSize: 14, padding: "4px 8px", border: "1px solid var(--rule-strong)", borderRadius: 3, background: "rgba(255,255,255,.5)", width: 160 } as const;
// Escrow rows are settled by the commitments module, never clawed back by hand.
const NOT_CLAWABLE = ["clawback", "goal_commitment", "goal_commitment_release", "bounty_payout"];

export default function Credits() {
  const [who, setWho] = useState("");
  const [balance, setBalance] = useState<ApiState<{ contributor_id: string; balance: number }>>({ kind: "idle" });
  const [history, setHistory] = useState<ApiState<{ contributor_id: string; events: Row[] }>>({ kind: "idle" });
  const [reason, setReason] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");

  async function lookup(e?: React.FormEvent) {
    e?.preventDefault();
    const id = who.trim();
    if (!id) return;
    setBalance({ kind: "loading" });
    setHistory({ kind: "loading" });
    setMsg("");
    const [b, h] = await Promise.all([getCreditBalance(id), getCreditHistory(id, 100)]);
    setBalance(b);
    setHistory(h);
  }

  async function claw(eventId: string) {
    const text = (reason[eventId] ?? "").trim();
    if (!text) { setMsg("A reason is required to claw back."); return; }
    if (!window.confirm("Claw back this event? This appends a reversing ledger row (nothing is deleted).")) return;
    const r = await clawbackCredit(eventId, text);
    setMsg(r.kind === "ok" ? `Clawed back ${eventId.slice(0, 8)}.` : r.kind === "error" ? r.message : `Not applied (${r.kind}).`);
    if (r.kind === "ok") lookup();
  }

  const events = history.kind === "ok" ? history.data.events : [];
  const clawable = (e: Row) =>
    !NOT_CLAWABLE.includes(str(e.reason)) && !events.some((x) => str(x.reversal_of_event_id) === str(e.id));

  return (
    <>
      <Hero marker="CREDITS" title="Credits" lead="Credits are an append-only ledger with no cash-out. A clawback adds a reversing row; it never edits history." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <form className="cform" onSubmit={lookup}>
          <div className="cfield">
            <label htmlFor="who">Contributor id</label>
            <input id="who" type="text" value={who} onChange={(e) => setWho(e.target.value)} placeholder="the contributor's subject id" required />
          </div>
          <div><button type="submit" className="btn-ink">Look up</button></div>
        </form>
        {balance.kind === "idle" ? null : balance.kind !== "ok" ? (
          <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={balance} /></div>
        ) : (
          <div className="stats"><Stat label="Balance" value={balance.data.balance} note="Sum of all ledger rows, open commitments included" /></div>
        )}
        {msg && <p className="notice-ok" role="status" style={{ gridColumn: "1 / span 12" }}>{msg}</p>}
        {history.kind === "ok" && (events.length === 0 ? (
          <div className="empty" style={{ gridColumn: "1 / span 12" }}><b>No ledger events.</b></div>
        ) : (
          <div className="panel scroll-x">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
              <h2 className="h3">Ledger</h2>
              <CsvButton filename={`credits-${who.trim()}.csv`} header={["id", "time", "reason", "amount", "goal_id", "procedure_row_id", "reversal_of_event_id"]}
                rows={() => events.map((e) => [str(e.id), str(e.created_at), str(e.reason), str(e.amount), str(e.goal_id), str(e.procedure_row_id), str(e.reversal_of_event_id)])} />
            </div>
            <table className="dtable">
              <thead><tr><th>When</th><th>Reason</th><th>Amount</th><th>Event</th><th>Clawback</th></tr></thead>
              <tbody>
                {events.map((e) => {
                  const id = str(e.id);
                  return (
                    <tr key={id}>
                      <td>{str(e.created_at).slice(0, 16).replace("T", " ")}</td>
                      <td>{str(e.reason).replace(/_/g, " ")}</td>
                      <td>{str(e.amount)}</td>
                      <td className="mono">{id.slice(0, 8)}</td>
                      <td>
                        {clawable(e) ? (
                          <div className="row-actions">
                            <input aria-label="Clawback reason" placeholder="Reason" value={reason[id] ?? ""} onChange={(ev) => setReason({ ...reason, [id]: ev.target.value })} style={reasonStyle} />
                            <button type="button" className="btn-line danger" onClick={() => claw(id)}>Claw back</button>
                          </div>
                        ) : <span className="dim">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}
      </section>
    </>
  );
}
