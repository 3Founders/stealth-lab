"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useOrg } from "@/components/org/OrgShell";
import { useLoad } from "@/components/org/useLoad";
import { relay } from "@/lib/chat/relay-client";
import { getAccessToken } from "@/lib/session";

const DISMISSED = "kell.org.setup.dismissed";
const REVIEWED = "kell.org.setup.reviewed";
const read = (key: string): boolean => { try { return window.localStorage.getItem(key) === "1"; } catch { return false; } };
const write = (key: string, on: boolean) => { try { window.localStorage.setItem(key, on ? "1" : "0"); } catch { /* per-browser only */ } };

interface Step {
  id: string;
  title: string;
  why: string;
  href: string;
  cta: string;
  done: boolean;
  onOpen?: () => void;
}

/**
 * The organization's first-run spine: six steps, in the order a team actually does them.
 *
 * It is a view over the real pages, not a second product: every action goes to the page that owns it, and the
 * checklist ticks when that page's data says the step is done. The onboarding endpoint supplies the key, policy,
 * invite and first-call facts; the relay supplies the connected-agent count.
 */
export default function SetupChecklist() {
  const { api, org, admin } = useOrg();
  const id = org.organization_id;
  const onboarding = useLoad(() => api.onboarding(id), `onb${id}`);
  const rel = useMemo(() => relay({ token: getAccessToken }), []);
  const agents = useLoad(() => rel.agents(), `agents${id}`);
  const [dismissed, setDismissed] = useState(false);
  const [reviewed, setReviewed] = useState(false);

  useEffect(() => { setDismissed(read(DISMISSED)); setReviewed(read(REVIEWED)); }, []);

  if (!admin) return null;
  if (onboarding.loaded.state === "loading") return <p className="small dim" role="status">Loading your setup…</p>;
  if (onboarding.loaded.state === "error") {
    return (
      <div className="org-setup org-setup-error" role="alert">
        <b>Couldn&rsquo;t read your setup.</b> <span className="dim">{onboarding.loaded.message}</span>{" "}
        <button type="button" className="btn-line dg-mini-btn" onClick={onboarding.reload}>Try again</button>
      </div>
    );
  }

  const o = onboarding.loaded.data;
  const agentCount = agents.loaded.state === "ok" ? agents.loaded.data.length : 0;
  const steps: Step[] = [
    { id: "key", title: "Connect a provider key", why: "Your organization's calls run on a key you control, not ours.", href: "/org/keys", cta: "Connect a key", done: o.provider_keys > 0 },
    { id: "policy", title: "Set the policy", why: "Choose the models, data types and budgets. Nothing runs until this exists.", href: "/org/policy", cta: "Set the policy", done: o.policy_set },
    { id: "invite", title: "Invite your team", why: "Everyone joins as a member; you can change a role or remove someone later.", href: "/org/members", cta: "Invite people", done: o.active_invites > 0 || o.members > 1 },
    { id: "agent", title: "Connect an agent", why: "Give an agent an owner, a scope and a budget before it does any work.", href: "/org/agents", cta: "Add an agent", done: agentCount > 0 },
    { id: "task", title: "Run a first task", why: "A settled call through the gateway is what makes a person “ready”.", href: "/chat", cta: "Open the chat", done: o.members_ready > 0 },
    { id: "review", title: "Review the activity", why: "See what was called, what it cost, and what the policy refused.", href: "/org/activity", cta: "See activity", done: reviewed, onOpen: () => { setReviewed(true); write(REVIEWED, true); } },
  ];
  const done = steps.filter((s) => s.done).length;
  const allDone = done === steps.length;

  if (dismissed && !allDone) {
    return (
      <button type="button" className="btn-line org-setup-show" onClick={() => { setDismissed(false); write(DISMISSED, false); }}>
        Show setup · {done} of {steps.length} done
      </button>
    );
  }
  if (dismissed && allDone) return null;

  const next = steps.find((s) => !s.done);

  return (
    <section className="org-setup" aria-labelledby="org-setup-title">
      <div className="org-setup-head">
        <div>
          <h2 id="org-setup-title" className="h3">Set up {org.name}</h2>
          <p className="small dim">A short path from a new organization to a working one. You can do these in any order; the checklist ticks itself.</p>
        </div>
        <button type="button" className="btn-line dg-mini-btn" onClick={() => { setDismissed(true); write(DISMISSED, true); }}>Hide</button>
      </div>
      <div className="org-setup-progress" role="group" aria-label={`Setup progress: ${done} of ${steps.length} done`}>
        <div className="org-bar-track" aria-hidden="true">
          <div className="org-bar-fill" style={{ width: `${(done / steps.length) * 100}%` }} />
        </div>
        <span className="small dim">{done} of {steps.length} done</span>
      </div>
      <ol className="org-setup-steps">
        {steps.map((s, i) => (
          <li key={s.id} className="org-setup-step" data-done={s.done || undefined} data-next={s.id === next?.id || undefined}>
            <span className="org-setup-mark" aria-hidden="true">{s.done ? "✓" : i + 1}</span>
            <div className="org-setup-body">
              <b>{s.title}</b>
              <span className="small dim">{s.why}</span>
            </div>
            {!s.done && (
              <Link className={s.id === next?.id ? "btn-ink dg-mini-btn" : "btn-line dg-mini-btn"} href={s.href} onClick={s.onOpen}>
                <span>{s.cta}</span>
              </Link>
            )}
            {s.done && <span className="org-setup-done small">Done</span>}
          </li>
        ))}
      </ol>
      {allDone && <p className="small dim">Everything is set up. The checklist stays here until you hide it.</p>}
    </section>
  );
}
