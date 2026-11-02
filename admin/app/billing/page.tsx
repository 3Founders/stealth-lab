"use client";
import { useCallback, useEffect, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import { CsvButton } from "@/components/Widgets";
import {
  STATES, createReferralCode, deleteReferralCode, getInvoices, getOrders, getOverview, getPayments, getPlans, getReconciliation,
  getReferralCodes, getSeller, issueCreditNote, issueInvoice, rupees, savePlan, saveSeller, setReferralCode, stateName,
  type ApiState, type BillingOverview, type Invoice, type OrderRow, type PaymentRow, type PlanRow, type Reconciliation,
  type ReferralCodeRow, type SellerState,
} from "@/lib/billing-api";

const TABS = ["Overview", "Payments", "Orders", "Reconcile", "Invoices", "Plans", "Referral codes", "Seller details"] as const;
type Tab = (typeof TABS)[number];
const when = (iso: string) => new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });

/** INR billing for the platform: what was ordered and paid, whether each order was credited, GST invoices and credit notes,
 * and what is sold (the plan numbers and the referral codes, both edited here and audited). */
export default function Billing() {
  const [tab, setTab] = useState<Tab>("Overview");
  return (
    <>
      <Hero marker="BILLING" title="Billing" lead="Rupee payments from the gateway, GST tax invoices and credit notes, and what is sold: the plan numbers and the referral codes. This page cannot add or remove a credit." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 24 }}>
        <div role="tablist" aria-label="Billing sections" className="cluster" style={{ gridColumn: "1 / -1" }}>
          {TABS.map((t) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} className={tab === t ? "btn-ink" : "btn-line"} onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>
        <div style={{ gridColumn: "1 / -1" }} role="tabpanel">
          {tab === "Overview" && <OverviewTab />}
          {tab === "Payments" && <PaymentsTab />}
          {tab === "Orders" && <OrdersTab />}
          {tab === "Reconcile" && <ReconcileTab />}
          {tab === "Invoices" && <InvoicesTab />}
          {tab === "Plans" && <PlansTab />}
          {tab === "Referral codes" && <ReferralCodesTab />}
          {tab === "Seller details" && <SellerTab />}
        </div>
      </section>
    </>
  );
}

function useLoad<T>(fn: (s?: AbortSignal) => Promise<ApiState<T>>) {
  const [state, setState] = useState<ApiState<T>>({ kind: "loading" });
  const load = useCallback(() => { const ac = new AbortController(); setState({ kind: "loading" }); void fn(ac.signal).then(setState); return () => ac.abort(); }, [fn]);
  useEffect(() => load(), [load]);
  return [state, load] as const;
}

function OverviewTab() {
  const [s] = useLoad<BillingOverview>(getOverview);
  if (s.kind !== "ok") return <AdminNotice state={s} />;
  const o = s.data;
  return (
    <>
      <div className="stats">
        <Stat label="Paid" value={rupees(o.paid_paise)} note={`${o.payments} payment${o.payments === 1 ? "" : "s"} credited`} />
        <Stat label="Ordered" value={rupees(o.ordered_paise)} note={`${o.orders} order${o.orders === 1 ? "" : "s"} started`} />
        <Stat label="Invoiced" value={rupees(o.invoiced_paise)} note={`${o.invoices} invoice${o.invoices === 1 ? "" : "s"}`} />
        <Stat label="Credit notes" value={rupees(o.credit_noted_paise)} note={`${o.credit_notes} issued`} />
        <Stat label="Payments without an invoice" value={o.payments_without_invoice} bad={o.payments_without_invoice > 0} note="Issue these under Payments" />
        <Stat label={`Orders not credited after ${o.stale_after_minutes} min`} value={o.stale_orders} bad={o.stale_orders > 0} note="See Reconcile" />
      </div>
      <p className="small dim" style={{ marginTop: 16 }}>
        Sign-in with a second factor is {o.mfa_required ? "required" : "NOT required"} for this page. Government e-invoice registration (IRN and QR code) is not connected.
      </p>
    </>
  );
}

function PaymentsTab() {
  const [s, reload] = useLoad<{ payments: PaymentRow[] }>(getPayments);
  const [issuing, setIssuing] = useState<PaymentRow | null>(null);
  if (s.kind !== "ok") return <AdminNotice state={s} empty="No payments yet." />;
  const rows = s.data.payments;
  return (
    <>
      <div className="cluster" style={{ marginBottom: 12 }}>
        <CsvButton filename="payments.csv" header={["time", "organisation", "payment", "order", "amount_inr", "invoice"]}
          rows={() => rows.map((p) => [p.created_at, p.organization ?? p.organization_id, p.payment_ref, p.order_id ?? "", (p.amount_paise / 100).toFixed(2), p.invoice_number ?? ""])} />
      </div>
      {rows.length === 0 ? <div className="empty"><b>No payments yet.</b></div> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Time</th><th>Organisation</th><th>Payment</th><th>Amount</th><th>Invoice</th></tr></thead>
          <tbody>{rows.map((p) => (
            <tr key={p.payment_ref}>
              <td>{when(p.created_at)}</td><td>{p.organization ?? p.organization_id.slice(0, 8)}</td><td><code>{p.payment_ref}</code></td><td>{rupees(p.amount_paise)}</td>
              <td>{p.invoice_number ?? <button type="button" className="btn-line" onClick={() => setIssuing(p)}>Issue invoice</button>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {issuing && <IssueDialog payment={issuing} onClose={() => setIssuing(null)} onDone={() => { setIssuing(null); reload(); }} />}
    </>
  );
}

function IssueDialog({ payment, onClose, onDone }: { payment: PaymentRow; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(payment.organization ?? "");
  const [gstin, setGstin] = useState("");
  const [state, setState] = useState("");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    const r = await issueInvoice(payment.payment_ref, { name, gstin: gstin.trim() || undefined, state_code: state || undefined, address: address.trim() || undefined });
    setBusy(false);
    if (r.kind === "ok") onDone(); else setError(r.kind === "error" ? r.message : `Not issued (${r.kind}).`);
  }
  return (
    <form className="cform profile-card" onSubmit={submit} style={{ marginTop: 16, maxWidth: 560 }} aria-label="Issue a tax invoice">
      <h2 className="h3">Tax invoice for {rupees(payment.amount_paise)}</h2>
      <p className="small dim">The amount is treated as the total the buyer paid, GST included. Buyer details are copied onto the invoice and can&rsquo;t be edited afterwards; a mistake is fixed with a credit note.</p>
      <div className="cfield"><label htmlFor="b-name">Buyer name</label><input id="b-name" value={name} onChange={(e) => setName(e.target.value)} required /></div>
      <div className="cfield"><label htmlFor="b-gstin">Buyer GSTIN <span className="dim">(if they have one)</span></label><input id="b-gstin" value={gstin} onChange={(e) => setGstin(e.target.value.toUpperCase())} maxLength={15} /></div>
      <div className="cfield"><label htmlFor="b-state">Place of supply (state)</label>
        <select id="b-state" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">From the GSTIN, or choose</option>
          {STATES.map(([c, n]) => <option key={c} value={c}>{c} · {n}</option>)}
        </select></div>
      <div className="cfield"><label htmlFor="b-addr">Buyer address <span className="dim">(optional)</span></label><textarea id="b-addr" rows={2} value={address} onChange={(e) => setAddress(e.target.value)} /></div>
      <div className="cluster"><button className="btn-ink" type="submit" disabled={busy}>{busy ? "Issuing…" : "Issue invoice"}</button><button type="button" className="btn-line" onClick={onClose} disabled={busy}>Cancel</button></div>
      {error && <p className="small" role="alert">{error}</p>}
    </form>
  );
}

function OrdersTab() {
  const [s] = useLoad<{ orders: OrderRow[] }>(getOrders);
  if (s.kind !== "ok") return <AdminNotice state={s} />;
  if (s.data.orders.length === 0) return <div className="empty"><b>No orders yet.</b></div>;
  return (
    <div className="table-wrap"><table className="table">
      <thead><tr><th>Started</th><th>Organisation</th><th>Order</th><th>Amount</th><th>Code</th><th>Status</th></tr></thead>
      <tbody>{s.data.orders.map((o) => (
        <tr key={o.order_id}><td>{when(o.created_at)}</td><td>{o.organization ?? o.organization_id.slice(0, 8)}</td><td><code>{o.order_id}</code></td><td>{rupees(o.amount_paise)}</td><td>{o.referral_code ? <code>{o.referral_code}</code> : <span className="dim">-</span>}</td><td>{o.credited ? "Paid and credited" : "Not credited"}</td></tr>
      ))}</tbody>
    </table></div>
  );
}

function ReconcileTab() {
  const [s] = useLoad<Reconciliation>(getReconciliation);
  if (s.kind !== "ok") return <AdminNotice state={s} />;
  const r = s.data;
  return (
    <div style={{ display: "grid", gap: 24 }}>
      <p className="small dim">{r.note}</p>
      <div>
        <h2 className="h3">Orders not credited after {r.stale_after_minutes} minutes ({r.uncredited_orders.length})</h2>
        {r.uncredited_orders.length === 0 ? <div className="empty"><b>None.</b></div> : (
          <div className="table-wrap"><table className="table"><thead><tr><th>Started</th><th>Organisation</th><th>Order</th><th>Amount</th></tr></thead>
            <tbody>{r.uncredited_orders.map((o) => <tr key={o.order_id}><td>{when(o.created_at)}</td><td>{o.organization ?? o.organization_id.slice(0, 8)}</td><td><code>{o.order_id}</code></td><td>{rupees(o.amount_paise)}</td></tr>)}</tbody></table></div>
        )}
      </div>
      <div>
        <h2 className="h3">Payments without an invoice ({r.payments_without_invoice.length})</h2>
        {r.payments_without_invoice.length === 0 ? <div className="empty"><b>None.</b></div> : (
          <div className="table-wrap"><table className="table"><thead><tr><th>Time</th><th>Organisation</th><th>Payment</th><th>Amount</th></tr></thead>
            <tbody>{r.payments_without_invoice.map((p) => <tr key={p.payment_ref}><td>{when(p.created_at)}</td><td>{p.organization ?? p.organization_id.slice(0, 8)}</td><td><code>{p.payment_ref}</code></td><td>{rupees(p.amount_paise)}</td></tr>)}</tbody></table></div>
        )}
      </div>
    </div>
  );
}

function InvoicesTab() {
  const [s, reload] = useLoad<{ invoices: Invoice[] }>(getInvoices);
  const [open, setOpen] = useState<Invoice | null>(null);
  const [msg, setMsg] = useState("");
  async function note(inv: Invoice) {
    if (!window.confirm(`Issue a credit note for ${inv.number}? This reverses the whole invoice and cannot be undone.`)) return;
    const r = await issueCreditNote(inv.id);
    setMsg(r.kind === "ok" ? `Credit note ${r.data.number} issued.` : r.kind === "error" ? r.message : `Not issued (${r.kind}).`);
    if (r.kind === "ok") reload();
  }
  if (s.kind !== "ok") return <AdminNotice state={s} />;
  const rows = s.data.invoices;
  return (
    <>
      {msg && <p className="notice-ok" role="status">{msg}</p>}
      <div className="cluster" style={{ marginBottom: 12 }}>
        <CsvButton filename="invoices.csv" header={["number", "kind", "date", "buyer", "buyer_gstin", "place_of_supply", "taxable_inr", "cgst_inr", "sgst_inr", "igst_inr", "total_inr"]}
          rows={() => rows.map((i) => [i.number, i.kind, i.issued_at, i.buyer_name, i.buyer_gstin ?? "", i.place_of_supply, (i.taxable_paise / 100).toFixed(2), (i.cgst_paise / 100).toFixed(2), (i.sgst_paise / 100).toFixed(2), (i.igst_paise / 100).toFixed(2), (i.total_paise / 100).toFixed(2)])} label="Export for the accountant (CSV)" />
      </div>
      {rows.length === 0 ? <div className="empty"><b>No invoices yet.</b><p>Issue one from a payment under Payments. Seller details must be saved first.</p></div> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Number</th><th>Type</th><th>Date</th><th>Buyer</th><th>Total</th><th /></tr></thead>
          <tbody>{rows.map((i) => (
            <tr key={i.id}><td><code>{i.number}</code></td><td>{i.kind === "invoice" ? "Tax invoice" : "Credit note"}</td><td>{when(i.issued_at)}</td><td>{i.buyer_name}</td><td>{rupees(i.total_paise)}</td>
              <td className="cluster"><button type="button" className="btn-line" onClick={() => setOpen(i)}>View</button>{i.kind === "invoice" && <button type="button" className="btn-line" onClick={() => void note(i)}>Credit note</button>}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      {open && <InvoiceView inv={open} onClose={() => setOpen(null)} />}
    </>
  );
}

/** The invoice as a printable page. Print or save as PDF from the browser; a PDF generator is not built. */
export function InvoiceView({ inv, onClose }: { inv: Invoice; onClose: () => void }) {
  const credit = inv.kind === "credit_note";
  return (
    <div className="invoice-sheet profile-card" style={{ marginTop: 20, maxWidth: 720 }} aria-label={`${credit ? "Credit note" : "Tax invoice"} ${inv.number}`}>
      <div className="cluster no-print"><button type="button" className="btn-ink" onClick={() => window.print()}>Print or save as PDF</button><button type="button" className="btn-line" onClick={onClose}>Close</button></div>
      <h2 className="h3">{credit ? "Credit note" : "Tax invoice"}</h2>
      <p className="small"><b>{inv.number}</b> · {when(inv.issued_at)}{inv.credit_for ? " · reverses an earlier invoice" : ""}</p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16 }}>
        <div><p className="caption">Seller</p><p><b>{inv.seller_name}</b></p><p className="small">GSTIN {inv.seller_gstin}<br />{inv.seller_address}<br />State {inv.seller_state_code} · {stateName(inv.seller_state_code)}</p></div>
        <div><p className="caption">Buyer</p><p><b>{inv.buyer_name}</b></p><p className="small">{inv.buyer_gstin ? <>GSTIN {inv.buyer_gstin}<br /></> : null}{inv.buyer_address ? <>{inv.buyer_address}<br /></> : null}Place of supply {inv.place_of_supply} · {stateName(inv.place_of_supply)}</p></div>
      </div>
      <table className="table" style={{ marginTop: 12 }}>
        <thead><tr><th>Description</th><th>SAC</th><th>Taxable value</th></tr></thead>
        <tbody><tr><td>{inv.description}</td><td>{inv.sac_code}</td><td>{rupees(inv.taxable_paise)}</td></tr></tbody>
        <tfoot>
          {inv.cgst_paise > 0 && <tr><td colSpan={2}>CGST ({Number(inv.rate_pct) / 2}%)</td><td>{rupees(inv.cgst_paise)}</td></tr>}
          {inv.sgst_paise > 0 && <tr><td colSpan={2}>SGST ({Number(inv.rate_pct) / 2}%)</td><td>{rupees(inv.sgst_paise)}</td></tr>}
          {inv.igst_paise > 0 && <tr><td colSpan={2}>IGST ({inv.rate_pct}%)</td><td>{rupees(inv.igst_paise)}</td></tr>}
          <tr><td colSpan={2}><b>Total{credit ? " reversed" : ""}</b></td><td><b>{rupees(inv.total_paise)}</b></td></tr>
        </tfoot>
      </table>
      <p className="small dim">Prices are in rupees. Payment reference {inv.payment_ref ?? "n/a"}. Issued by {inv.issued_by}.</p>
    </div>
  );
}

function SellerTab() {
  const [s, reload] = useLoad<SellerState>(getSeller);
  const [form, setForm] = useState({ legal_name: "", trade_name: "", gstin: "", state_code: "", address: "", sac_code: "", gst_rate_pct: "18", invoice_prefix: "KEL" });
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (s.kind === "ok" && s.data.profile) {
      const p = s.data.profile;
      setForm({ legal_name: p.legal_name, trade_name: p.trade_name ?? "", gstin: p.gstin ?? "", state_code: p.state_code ?? "", address: p.address ?? "", sac_code: p.sac_code ?? "", gst_rate_pct: p.gst_rate_pct, invoice_prefix: p.invoice_prefix });
    }
  }, [s]);
  if (s.kind !== "ok") return <AdminNotice state={s} />;
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setMsg("");
    const r = await saveSeller({ ...form, trade_name: form.trade_name || null, gstin: form.gstin || null, state_code: form.state_code || null, address: form.address || null, sac_code: form.sac_code || null });
    setBusy(false);
    setMsg(r.kind === "ok" ? "Saved." : r.kind === "error" ? r.message : `Not saved (${r.kind}).`);
    if (r.kind === "ok") reload();
  }
  return (
    <form className="cform profile-card" onSubmit={save} style={{ maxWidth: 620 }}>
      <h2 className="h3">Seller details</h2>
      <p className="small dim">These print on every invoice, and are copied onto each one when it is issued, so changing them later never rewrites an old invoice. The rate and the SAC code are for your accountant to confirm; nothing here is guessed.</p>
      {s.data.problems.length > 0 && <p className="small" role="status"><b>Before invoices can be issued:</b> {s.data.problems.join("; ")}.</p>}
      <div className="cfield"><label htmlFor="s-name">Legal name</label><input id="s-name" value={form.legal_name} onChange={set("legal_name")} required /></div>
      <div className="cfield"><label htmlFor="s-trade">Trade name <span className="dim">(optional)</span></label><input id="s-trade" value={form.trade_name} onChange={set("trade_name")} /></div>
      <div className="cfield"><label htmlFor="s-gstin">GSTIN</label><input id="s-gstin" value={form.gstin} onChange={(e) => setForm((f) => ({ ...f, gstin: e.target.value.toUpperCase() }))} maxLength={15} placeholder="27ABCDE1234F1Z5" /></div>
      <div className="cfield"><label htmlFor="s-state">State</label>
        <select id="s-state" value={form.state_code} onChange={set("state_code")}><option value="">From the GSTIN</option>{STATES.map(([c, n]) => <option key={c} value={c}>{c} · {n}</option>)}</select></div>
      <div className="cfield"><label htmlFor="s-addr">Address</label><textarea id="s-addr" rows={3} value={form.address} onChange={set("address")} /></div>
      <div className="cfield"><label htmlFor="s-sac">SAC code <span className="dim">(confirm with your accountant)</span></label><input id="s-sac" value={form.sac_code} onChange={set("sac_code")} maxLength={12} /></div>
      <div className="cfield"><label htmlFor="s-rate">GST rate (%)</label><input id="s-rate" inputMode="decimal" value={form.gst_rate_pct} onChange={set("gst_rate_pct")} /></div>
      <div className="cfield"><label htmlFor="s-prefix">Invoice number prefix</label><input id="s-prefix" value={form.invoice_prefix} onChange={(e) => setForm((f) => ({ ...f, invoice_prefix: e.target.value.toUpperCase() }))} maxLength={4} /><span className="hint">Numbers look like {form.invoice_prefix || "KEL"}/2627/000001: sequential per financial year, at most 16 characters.</span></div>
      <div className="cluster"><button className="btn-ink" type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button></div>
      {msg && <p className="small" role="status">{msg}</p>}
    </form>
  );
}

/** What is sold: the plan numbers the server uses for every order and /pricing shows. A change applies to new orders only. */
function PlansTab() {
  const [s, reload] = useLoad<{ plans: PlanRow[] }>(getPlans);
  const [editing, setEditing] = useState<PlanRow | null>(null);
  const [msg, setMsg] = useState("");
  if (s.kind !== "ok") return <AdminNotice state={s} />;
  return (
    <>
      <p className="small dim">
        These are the numbers the server sells with and /pricing shows. A change applies to orders made after it; an order already started keeps
        its amount. The card sentences live in the site, so a brand-new plan kind is a code change. The Pilot&apos;s price and usage are per seat.
      </p>
      {msg && <p className="notice-ok" role="status">{msg}</p>}
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Plan</th><th>Price</th><th>Period</th><th>Usage included</th><th>Status</th><th /></tr></thead>
        <tbody>{s.data.plans.map((p) => (
          <tr key={p.plan}>
            <td><b>{p.plan}</b></td>
            <td>{rupees(p.price_paise)}{p.plan === "pilot" ? " per seat" : ""}</td>
            <td>{p.period_days} days</td>
            <td>${p.included_usd}{p.plan === "pilot" ? " per seat" : ""}</td>
            <td>{!p.active ? "Not sold" : p.plan === "pilot" ? "On sale - code needed" : "On sale"}</td>
            <td><button type="button" className="btn-line" onClick={() => setEditing(p)}>Edit</button></td>
          </tr>
        ))}</tbody>
      </table></div>
      {editing && <PlanEdit row={editing} onClose={() => setEditing(null)} onDone={(m) => { setEditing(null); setMsg(m); reload(); }} />}
    </>
  );
}

function PlanEdit({ row, onClose, onDone }: { row: PlanRow; onClose: () => void; onDone: (message: string) => void }) {
  const [priceInr, setPriceInr] = useState(String(row.price_paise / 100));
  const [days, setDays] = useState(String(row.period_days));
  const [usd, setUsd] = useState(row.included_usd);
  const [active, setActive] = useState(row.active);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    const r = await savePlan(row.plan, {
      price_paise: Math.round(Number(priceInr) * 100), period_days: Math.round(Number(days)), included_usd: usd.trim(), active,
    });
    setBusy(false);
    if (r.kind === "ok") onDone(r.data.note);
    else setError(r.kind === "error" ? r.message : `Not saved (${r.kind}).`);
  }
  return (
    <form className="cform profile-card" onSubmit={save} style={{ marginTop: 16, maxWidth: 560 }} aria-label={`Edit the ${row.plan} plan`}>
      <h2 className="h3">{row.plan}</h2>
      <p className="small dim">Price is whole rupees before GST. Usage included is in dollars, credited at purchase.</p>
      <div className="cfield"><label htmlFor="p-price">Price (₹, before GST)</label><input id="p-price" inputMode="decimal" value={priceInr} onChange={(e) => setPriceInr(e.target.value)} required /></div>
      <div className="cfield"><label htmlFor="p-days">Period (days)</label><input id="p-days" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} required /></div>
      <div className="cfield"><label htmlFor="p-usd">Usage included ($)</label><input id="p-usd" inputMode="decimal" value={usd} onChange={(e) => setUsd(e.target.value)} required /></div>
      <div className="cfield"><label htmlFor="p-active"><input id="p-active" type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> On sale</label></div>
      <div className="cluster"><button className="btn-ink" type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button><button type="button" className="btn-line" onClick={onClose} disabled={busy}>Cancel</button></div>
      {error && <p className="small" role="alert">{error}</p>}
    </form>
  );
}

/** The referral codes: issued here, plus the personal ones made for a buying account. No reward is paid for any of them. */
function ReferralCodesTab() {
  const [s, reload] = useLoad<{ codes: ReferralCodeRow[] }>(getReferralCodes);
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !code.trim()) return;
    setBusy(true); setMsg("");
    const r = await createReferralCode(code, note);
    setBusy(false);
    setMsg(r.kind === "ok" ? `Added ${r.data.code}.` : r.kind === "error" ? r.message : `Not added (${r.kind}).`);
    if (r.kind === "ok") { setCode(""); setNote(""); reload(); }
  }
  async function toggle(row: ReferralCodeRow) {
    const r = await setReferralCode(row.code, { active: !row.active });
    setMsg(r.kind === "ok" ? `${row.code} is now ${r.data.active ? "on" : "off"}.` : r.kind === "error" ? r.message : `Not saved (${r.kind}).`);
    if (r.kind === "ok") reload();
  }
  async function remove(row: ReferralCodeRow) {
    if (!window.confirm(`Delete ${row.code}? A code an order was made with cannot be deleted, only turned off.`)) return;
    const r = await deleteReferralCode(row.code);
    setMsg(r.kind === "ok" ? `Deleted ${row.code}.` : r.kind === "error" ? r.message : `Not deleted (${r.kind}).`);
    if (r.kind === "ok") reload();
  }
  if (s.kind !== "ok") return <AdminNotice state={s} />;
  const rows = s.data.codes;
  return (
    <>
      <p className="small dim">
        Every active code unlocks the one-person Pilot - the Rs 649 plan for 30 days with $4 of usage - on /pricing. &ldquo;Issued&rdquo;
        codes are made here and handed out (the launch code FIRSTTRIAL is seeded by migration 168; matching ignores case, so
        &ldquo;Firsttrial&rdquo; as typed works); &ldquo;personal&rdquo; codes are made for a buying account when its plan is activated and
        unlock the same plan for whoever the buyer shares it with. Orders record the code they were made with. No reward is paid to
        anyone for a code: the referrer reward is not built.
      </p>
      <form className="cform profile-card" onSubmit={add} style={{ marginBottom: 16, maxWidth: 560 }} aria-label="Add a referral code">
        <h2 className="h3">Add a code</h2>
        <div className="cfield"><label htmlFor="rc-code">Code</label><input id="rc-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} maxLength={32} placeholder="For example FRIEND7" required /></div>
        <div className="cfield"><label htmlFor="rc-note">Note <span className="dim">(who it is for; never shown to buyers)</span></label><input id="rc-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} /></div>
        <div className="cluster"><button className="btn-ink" type="submit" disabled={busy || !code.trim()}>{busy ? "Adding…" : "Add code"}</button></div>
        {msg && <p className="small" role="status">{msg}</p>}
      </form>
      {rows.length === 0 ? <div className="empty"><b>No codes yet.</b></div> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Code</th><th>Kind</th><th>Note</th><th>Orders</th><th>Status</th><th /></tr></thead>
          <tbody>{rows.map((c) => (
            <tr key={c.code}>
              <td><code>{c.code}</code></td>
              <td>{c.kind}{c.kind === "personal" && c.owner_user_id ? <span className="dim"> · {c.owner_user_id.slice(0, 8)}</span> : null}</td>
              <td>{c.note ?? <span className="dim">-</span>}</td>
              <td>{c.uses}</td>
              <td>{c.active ? "Active" : "Off"}</td>
              <td className="cluster">
                <button type="button" className="btn-line" onClick={() => void toggle(c)}>{c.active ? "Turn off" : "Turn on"}</button>
                {c.uses === 0 && <button type="button" className="btn-line" onClick={() => void remove(c)}>Delete</button>}
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
