"use client";
import { Hero } from "@/components/Admin";
import { ComingCard } from "@/components/ExecWidgets";

const GROUPS: { title: string; lead: string; items: [string, string][] }[] = [
  {
    title: "Keys and connections",
    lead: "How the organisation connects to model providers.",
    items: [
      ["Provider keys", "Add, test, rotate and disable a provider key. Only the last 4 characters are ever shown; a key can't be viewed again after it is saved."],
    ],
  },
  {
    title: "Model control",
    lead: "Deciding which models may be used, by rule instead of by list.",
    items: [
      ["Model catalog and policy rules", "Allow models by provider, family, price ceiling, data class, region or open weights, instead of listing each model. New models are included automatically or held for approval."],
      ["Home region", "Choose where the organisation's data is kept."],
    ],
  },
  {
    title: "Teams and limits",
    lead: "Budgets that follow how the organisation is organised.",
    items: [
      ["Teams and engagements", "Nested budgets, with each member's limit inside their team's budget."],
      ["Request a higher limit", "A member asks for more; an admin approves or denies the request."],
    ],
  },
  {
    title: "Roles and approvals",
    lead: "Separating who proposes a change from who approves it.",
    items: [
      ["New roles", "Executive (team-level dashboards and approving changes, no keys), team lead, and auditor."],
      ["Change proposals", "Policy and budget changes become proposals that an executive approves."],
    ],
  },
  {
    title: "Value delivered",
    lead: "What the spend achieved.",
    items: [
      ["Savings against the default model", "Savings = cost on the default model − actual cost, with the formula and each input visible and adjustable. It needs the backend to record the cost on the default model for every call, which it doesn't yet."],
      ["Verified pass rate", "The share of checks that actually passed, from reported check results. It needs those results added to the organisation data; today only whether a model call completed is known."],
    ],
  },
];

export default function ComingPage() {
  return (
    <>
      <Hero marker="ROADMAP" title="Coming soon" lead="Planned, not built. Nothing here has numbers, because there is nothing to measure yet." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        {GROUPS.map((g) => (
          <div className="panel" key={g.title}>
            <div><h2 className="h3">{g.title}</h2><p className="small dim">{g.lead}</p></div>
            <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
              {g.items.map(([t, d]) => (<ComingCard key={t} title={t}>{d}</ComingCard>))}
            </div>
          </div>
        ))}
      </section>
    </>
  );
}
