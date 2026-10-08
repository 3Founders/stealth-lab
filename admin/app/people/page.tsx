"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import { RangePicker, useRange } from "@/components/Range";
import { CsvButton, Updated } from "@/components/Widgets";
import { formatUsd, percent, toMicros } from "@/lib/money";
import { getBudget, getMembers, getUsageByUser, type ApiState, type BudgetPosition, type Member, type Org, type UserUsageRow } from "@/lib/org-api";
import { displayName, shortId, totalsByUser, userBudgets } from "@/lib/people";
import { isRange } from "@/lib/range";

const compact = (n: number) => new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
const CAP_LABEL = { ok: "", near: "near the cap", at: "at the cap", none: "" } as const;

function PeoplePanel({ org }: { org: Org }) {
  const { range, ready, setRange } = useRange(30);
  const [usage, setUsage] = useState<ApiState<{ rows: UserUsageRow[] }>>({ kind: "loading" });
  const [budget, setBudget] = useState<ApiState<BudgetPosition>>({ kind: "loading" });
  const [members, setMembers] = useState<ApiState<{ members: Member[] }>>({ kind: "loading" });
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const ac = new AbortController();
    setBudget({ kind: "loading" });
    getBudget(org.organization_id, ac.signal).then(setBudget);
    getMembers(org.organization_id, ac.signal).then(setMembers);
    return () => ac.abort();
  }, [org.organization_id, nonce]);

  useEffect(() => {
    if (!ready || !isRange(range)) return;
    const ac = new AbortController();
    setUsage({ kind: "loading" });
    getUsageByUser(org.organization_id, `${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`, ac.signal).then((r) => {
      setUsage(r);
      if (r.kind === "ok") setLoadedAt(new Date());
    });
    return () => ac.abort();
  }, [org.organization_id, range, ready, nonce]);

  const people = members.kind === "ok" ? members.data.members : [];
  const totals = useMemo(() => totalsByUser(usage.kind === "ok" ? usage.data.rows : []), [usage]);
  const grand = totals.reduce((s, t) => s + t.total, 0n);
  const today = useMemo(() => (budget.kind === "ok" ? userBudgets(budget.data) : []), [budget]);
  const atCap = today.filter((u) => u.position.state === "at");
  const noPolicy = budget.kind === "error" && budget.status === 409;

  return (
    <>
      <RangePicker range={range} setRange={setRange} idPrefix="people" />
      <div style={{ gridColumn: "1 / span 12" }}><Updated at={loadedAt} onRefresh={refresh} busy={usage.kind === "loading"} /></div>

      {atCap.length > 0 && (
        <p className="notice-ok" role="alert" style={{ gridColumn: "1 / span 12", background: "var(--yellow)", borderColor: "var(--ink)" }}>
          <b>{atCap.length} user{atCap.length === 1 ? " has" : "s have"} reached today&rsquo;s per-user budget:</b> {atCap.map((u) => displayName(u.user_id, people)).join(", ")}. Further calls from them are refused until tomorrow (UTC).
        </p>
      )}

      <div className="panel">
        <h2 className="h3">Budget right now</h2>
        {noPolicy ? (
          <div className="empty"><b>No policy yet.</b><p>With no policy nothing is allowed, so there is no budget to track. Create one under Policy.</p></div>
        ) : budget.kind !== "ok" ? <AdminNotice state={budget} /> : (
          <>
            <div className="stats">
              <Stat label="Monthly used" value={formatUsd(toMicros(budget.data.monthly.used_usd))} note={`of ${formatUsd(toMicros(budget.data.monthly.budget_usd))} · settled spend`} />
              <Stat label="Held" value={formatUsd(toMicros(budget.data.monthly.held_usd))} note="Live reservations for calls in flight; they settle or release" />
              <Stat label="Monthly remaining" value={formatUsd(toMicros(budget.data.monthly.remaining_usd))} note="Budget − used − held" bad={toMicros(budget.data.monthly.remaining_usd) <= 0n && toMicros(budget.data.monthly.budget_usd) > 0n} />
              <Stat label="Per-user daily budget" value={formatUsd(toMicros(budget.data.per_user_daily_budget_usd))} note={`${today.length} user${today.length === 1 ? "" : "s"} active today (UTC)`} />
            </div>
            {today.length > 0 && (
              <div className="scroll-x">
                <table className="dtable">
                  <thead><tr><th>User today</th><th>Used</th><th>Held</th><th>Remaining</th><th>Of daily cap</th></tr></thead>
                  <tbody>
                    {today.map((u) => (
                      <tr key={u.user_id}>
                        <td>{displayName(u.user_id, people)}</td>
                        <td>{formatUsd(u.used)}</td><td>{formatUsd(u.held)}</td><td>{formatUsd(u.remaining)}</td>
                        <td>{u.position.pct == null ? "—" : `${u.position.pct.toFixed(0)}%`}{CAP_LABEL[u.position.state] && <b> · {CAP_LABEL[u.position.state]}</b>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>

      <div className="panel scroll-x">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <h2 className="h3">Spend by user</h2>
          <CsvButton filename={`spend-by-user-${range.since}-${range.until}.csv`}
            header={["User id", "Name", "Email", "Calls", "Failed", "Tokens", "Input USD", "Output USD", "Processing USD", "Total USD", "Share", "Unpriced calls", "Last active"]}
            rows={() => totals.map((t) => {
              const m = people.find((x) => x.subject === t.subject);
              return [t.subject, m?.display_name, m?.email, t.calls, t.failed, t.tokens, formatUsd(t.input, { cents: true }).slice(1), formatUsd(t.output, { cents: true }).slice(1),
                formatUsd(t.processing, { cents: true }).slice(1), formatUsd(t.total, { cents: true }).slice(1), percent(t.total, grand), t.upperBoundCalls, t.lastDay];
            })} />
        </div>
        {usage.kind !== "ok" ? <AdminNotice state={usage} /> : totals.length === 0 ? <div className="empty"><b>No attributed model calls in this range.</b></div> : (
          <table className="dtable">
            <thead><tr><th>User</th><th>Calls</th><th>Tokens</th><th>Input</th><th>Output</th><th>Processing</th><th>Total</th><th>Share</th><th>Last active</th></tr></thead>
            <tbody>
              {totals.map((t) => (
                <tr key={t.subject}>
                  <td>{displayName(t.subject, people)}{!people.some((m) => m.subject === t.subject) && <span className="dim"> · not a current member</span>}</td>
                  <td>{t.calls}{t.failed > 0 && <span className="dim"> ({t.failed} failed)</span>}</td>
                  <td>{compact(t.tokens)}</td>
                  <td>{formatUsd(t.input)}</td><td>{formatUsd(t.output)}</td><td>{formatUsd(t.processing)}</td>
                  <td>{formatUsd(t.total)}{t.upperBoundCalls > 0 && <span className="dim"> · {t.upperBoundCalls} unpriced</span>}</td>
                  <td>{percent(t.total, grand) == null ? "—" : `${percent(t.total, grand)!.toFixed(1)}%`}</td>
                  <td>{t.lastDay}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="small dim">Users are identified by their identity-provider id. Spend is by user, not by provider key: keys belong to the connection, not to a person.</p>
      </div>

      <div className="panel scroll-x">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <h2 className="h3">Members</h2>
          <CsvButton filename={`members-${org.name}.csv`} header={["User id", "Name", "Email", "Roles", "Active", "Member since"]}
            rows={() => people.map((m) => [m.user_id, m.display_name, m.email, m.roles.join(" "), m.is_active, m.member_since])} />
        </div>
        {members.kind !== "ok" ? <AdminNotice state={members} /> : people.length === 0 ? <div className="empty"><b>No members.</b></div> : (
          <table className="dtable">
            <thead><tr><th>Name</th><th>Email</th><th>Roles</th><th>Status</th><th>Member since</th><th>User id</th></tr></thead>
            <tbody>
              {people.map((m) => (
                <tr key={m.user_id}>
                  <td>{m.display_name || "—"}</td><td>{m.email || "—"}</td><td>{m.roles.join(", ")}</td>
                  <td>{m.is_active ? "Active" : <span className="dim">Inactive</span>}</td>
                  <td>{m.member_since ? String(m.member_since).slice(0, 10) : "—"}</td>
                  <td className="mono">{shortId(m.user_id)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="small dim">Read-only for now. Inviting people, changing roles and removing members aren&rsquo;t available in the backend yet.</p>
      </div>
    </>
  );
}

export default function PeoplePage() {
  return (
    <>
      <Hero marker="GOVERNANCE" title="People" lead="Who is spending, where each person stands against today's cap, and who is in the organisation." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="person">{(org) => <PeoplePanel key={org.organization_id} org={org} />}</OrgGate>
      </section>
    </>
  );
}
