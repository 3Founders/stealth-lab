"use client";
import Link from "next/link";
import { notFound, usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import { canAdmin, orgAdminEnabled, orgApi, type OrgApi, type OrgSummary } from "@/lib/org-admin";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";

interface Ctx { api: OrgApi; org: OrgSummary; orgs: OrgSummary[]; admin: boolean }
const OrgContext = createContext<Ctx | null>(null);

/** The signed-in organization, its role and the typed client. Only usable inside the org pages. */
export function useOrg(): Ctx {
  const c = useContext(OrgContext);
  if (!c) throw new Error("useOrg must be used inside the organization pages");
  return c;
}

const CURRENT_KEY = "kel.org.current";
const readCurrent = (): string | null => { try { return window.localStorage.getItem(CURRENT_KEY); } catch { return null; } };
const writeCurrent = (id: string) => { try { window.localStorage.setItem(CURRENT_KEY, id); } catch { /* per-viewer convenience only */ } };

const NAV: Array<{ href: string; label: string; admin: boolean }> = [
  { href: "/org", label: "Overview", admin: false },
  { href: "/org/members", label: "Members", admin: true },
  { href: "/org/policy", label: "Policy", admin: true },
  { href: "/org/activity", label: "Activity", admin: true },
  { href: "/org/keys", label: "Provider keys", admin: true },
];

/**
 * The frame of every organization page: the flag (off by default: the pages don't exist while it is off), the sign-in gate, the
 * organization picker, and navigation. Which organizations you are in, and your role in each, come from the server.
 */
export default function OrgShell({ children }: { children: React.ReactNode }) {
  if (!orgAdminEnabled()) notFound();
  return <Shell>{children}</Shell>;
}

function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const api = useMemo(() => orgApi({ token: getAccessToken }), []);
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [orgs, setOrgs] = useState<OrgSummary[] | null | "error">(null);
  const [error, setError] = useState("");
  const [currentId, setCurrentId] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); };
  }, []);

  const load = useCallback(() => {
    setOrgs(null); setError("");
    api.mine().then((o) => { setOrgs(o); setCurrentId(readCurrent()); }).catch((e) => { setOrgs("error"); setError(e instanceof Error ? e.message : "Couldn't load your organizations."); });
  }, [api]);
  useEffect(() => { if (session) load(); }, [session, load]);

  const head = (
    <section className="page-hero frame grid">
      <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ORGANIZATION</b></div>
      <h1 className="display"><AnimatedHeading>Your organization</AnimatedHeading></h1>
    </section>
  );
  const body = (node: React.ReactNode) => <>{head}<section className="frame grid" style={{ paddingBottom: 120 }}><div className="org-wrap">{node}</div></section></>;

  if (session === undefined) return body(<p className="small dim" role="status">Checking your session…</p>);
  if (session === null) return body(<div className="empty" role="status"><b>Sign in to see your organization.</b><p><Link href={`/sign-in?redirect=${path || "/org"}`} style={{ textDecoration: "underline" }}>Sign in</Link>.</p></div>);
  if (orgs === null) return body(<p className="small dim" role="status">Loading your organizations…</p>);
  if (orgs === "error") return body(<div className="empty" role="alert"><b>Couldn&rsquo;t load your organizations.</b><p>{error}</p><div className="cluster"><button type="button" className="btn-line" onClick={load}>Try again</button></div></div>);
  if (orgs.length === 0) {
    return body(
      <div className="empty" role="status">
        <b>You&rsquo;re not in an organization yet.</b>
        <p>Ask an admin of your organization for an invite code, then <Link href="/join" style={{ textDecoration: "underline" }}>join with the code</Link>.</p>
      </div>,
    );
  }

  const org = orgs.find((o) => o.organization_id === currentId) ?? orgs[0];
  const admin = canAdmin(org.role);
  const nav = NAV.filter((n) => admin || !n.admin);
  return (
    <OrgContext.Provider value={{ api, org, orgs, admin }}>
      {body(
        <>
          <div className="org-bar">
            {orgs.length > 1
              ? (
                <label className="org-picker">Organization
                  <select value={org.organization_id} onChange={(e) => { writeCurrent(e.target.value); setCurrentId(e.target.value); }}>
                    {orgs.map((o) => <option key={o.organization_id} value={o.organization_id}>{o.name}</option>)}
                  </select>
                </label>
              )
              : <h2 className="h3">{org.name}</h2>}
            <span className="org-role" title="What you can do here depends on your role">{org.role}</span>
          </div>
          <nav className="org-nav" aria-label="Organization">
            {nav.map((n) => <Link key={n.href} href={n.href} aria-current={path === n.href ? "page" : undefined}>{n.label}</Link>)}
          </nav>
          {children}
        </>,
      )}
    </OrgContext.Provider>
  );
}
