"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useOrg } from "@/components/OrgContext";
import { canSeePerPerson, isAdminRole, isOwnerRole } from "@/lib/org-api";
import { getSession } from "@/lib/session";
import { getSupabase } from "@/lib/supabase";

// Operator tools that act on the whole platform (the backend's admin scope decides who may use them).
const platformLinks = [
  { href: "/operations", label: "Operations" },
  { href: "/ingestion", label: "Ingestion" },
  { href: "/review", label: "Review" },
  { href: "/moderation", label: "Moderation" },
  { href: "/credits", label: "Credits" },
];

// Per-organisation governance, shown on a second row for the organisation picked in the selector.
const orgLinks = [
  { href: "/", label: "Executive", need: "admin" },
  { href: "/policy", label: "Policy", need: "admin" },
  { href: "/usage", label: "Usage", need: "admin" },
  { href: "/performance", label: "Performance", need: "admin" },
  { href: "/people", label: "People", need: "person" },
  { href: "/calls", label: "Calls", need: "person" },
  { href: "/denials", label: "Denials", need: "person" },
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
  // Operator tools are for platform staff: executives never see them unless they open one, via the quiet link below.
  const onPlatformPage = platformLinks.some((l) => pathname === l.href || pathname.startsWith(`${l.href}/`));
  const visibleOrgLinks = orgLinks.filter((l) => (l.need === "owner" ? isOwnerRole(current?.role) : l.need === "person" ? canSeePerPerson(current?.role) : isAdminRole(current?.role)));

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
          <Link href="/" className="logo" aria-label="keळ admin, home" onClick={() => setOpen(false)}>
            <Image src="/kel-wordmark.png" alt="keळ" width={800} height={440} priority style={{ height: 54, width: "auto" }} />
          </Link>
          {onPlatformPage ? (
            <nav aria-label="Platform" className="nav-links">
              {platformLinks.map((l) => (
                <Link key={l.href} href={l.href} aria-current={pathname === l.href ? "page" : undefined} onClick={() => setOpen(false)}>{l.label}</Link>
              ))}
            </nav>
          ) : <span aria-hidden="true" />}
          {signedIn === undefined ? (
            <span className="btn-ink desk" aria-hidden="true" style={{ visibility: "hidden" }}>Sign out</span>
          ) : signedIn ? (
            <button type="button" className="btn-ink desk" onClick={signOut}>Sign out</button>
          ) : (
            <Link href="/sign-in" className="btn-ink desk">Sign in</Link>
          )}
        </header>
        {(orgList.length > 0 || !onPlatformPage) && (
          <div className="subnav">
            {orgList.length > 0 && (
              <select aria-label="Organisation" value={current?.organization_id ?? ""} onChange={(e) => select(e.target.value)}>
                {orgList.map((x) => <option key={x.organization_id} value={x.organization_id}>{x.name} ({x.role})</option>)}
              </select>
            )}
            <nav aria-label="Organisation" className="nav-links">
              {visibleOrgLinks.map((l) => (
                <Link key={l.href} href={l.href} aria-current={pathname === l.href ? "page" : undefined}>{l.label}</Link>
              ))}
              {visibleOrgLinks.length > 0 && <Link href="/coming" aria-current={pathname === "/coming" ? "page" : undefined} style={{ color: "var(--grey)" }}>Coming soon</Link>}
              {!onPlatformPage && <Link href="/operations" style={{ color: "var(--grey)" }}>Operator tools</Link>}
            </nav>
          </div>
        )}
      </div>
    </div>
  );
}
