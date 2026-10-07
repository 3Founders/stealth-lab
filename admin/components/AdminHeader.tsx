"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useOrg } from "@/components/OrgContext";
import { isAdminRole, isOwnerRole } from "@/lib/org-api";
import { getSession } from "@/lib/session";
import { getSupabase } from "@/lib/supabase";

// Operator tools that act on the whole platform (the backend's admin scope decides who may use them).
const platformLinks = [
  { href: "/", label: "Overview" },
  { href: "/ingestion", label: "Ingestion" },
  { href: "/review", label: "Review" },
  { href: "/moderation", label: "Moderation" },
  { href: "/credits", label: "Credits" },
];

// Per-organisation governance, shown on a second row for the organisation picked in the selector.
const orgLinks = [
  { href: "/policy", label: "Policy", need: "admin" },
  { href: "/usage", label: "Usage", need: "admin" },
  { href: "/performance", label: "Performance", need: "admin" },
  { href: "/people", label: "People", need: "admin" },
  { href: "/calls", label: "Calls", need: "admin" },
  { href: "/denials", label: "Denials", need: "admin" },
  { href: "/audit", label: "Audit", need: "admin" },
  { href: "/compliance", label: "Compliance", need: "owner" },
] as const;

export default function AdminHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [signedIn, setSignedIn] = useState<boolean | undefined>(undefined);
  const { orgs, current, select } = useOrg();
  const orgList = orgs.kind === "ok" ? orgs.data : [];
  const visibleOrgLinks = orgLinks.filter((l) => (l.need === "owner" ? isOwnerRole(current?.role) : isAdminRole(current?.role)));

  useEffect(() => {
    let cancelled = false;
    getSession().then((s) => { if (!cancelled) setSignedIn(!!s); });
    return () => { cancelled = true; };
  }, [pathname]);

  async function signOut() {
    await getSupabase()?.auth.signOut();
    setSignedIn(false);
    router.push("/sign-in");
  }

  return (
    <div className="nav-wrap">
      <div className="frame">
        <header className="nav">
          <Link href="/" className="logo" aria-label="keळ admin, overview" onClick={() => setOpen(false)}>
            <Image src="/kel-wordmark.png" alt="keळ" width={800} height={440} priority style={{ height: 54, width: "auto" }} />
          </Link>
          <nav aria-label="Platform" className="nav-links">
            {platformLinks.map((l) => (
              <Link key={l.href} href={l.href} aria-current={pathname === l.href ? "page" : undefined} onClick={() => setOpen(false)}>{l.label}</Link>
            ))}
          </nav>
          {signedIn === undefined ? (
            <span className="btn-ink desk" aria-hidden="true" style={{ visibility: "hidden" }}>Sign out</span>
          ) : signedIn ? (
            <button type="button" className="btn-ink desk" onClick={signOut}>Sign out</button>
          ) : (
            <Link href="/sign-in" className="btn-ink desk">Sign in</Link>
          )}
        </header>
        {orgList.length > 0 && (
          <div className="subnav">
            <select aria-label="Organisation" value={current?.organization_id ?? ""} onChange={(e) => select(e.target.value)}>
              {orgList.map((x) => <option key={x.organization_id} value={x.organization_id}>{x.name} ({x.role})</option>)}
            </select>
            <nav aria-label="Organisation" className="nav-links">
              {visibleOrgLinks.map((l) => (
                <Link key={l.href} href={l.href} aria-current={pathname === l.href ? "page" : undefined}>{l.label}</Link>
              ))}
            </nav>
          </div>
        )}
      </div>
    </div>
  );
}
