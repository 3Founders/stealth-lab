"use client";
import type { ReactNode } from "react";
import { AdminNotice } from "@/components/Admin";
import { useOrg } from "@/components/OrgContext";
import { isAdminRole, isOwnerRole, type Org } from "@/lib/org-api";

/** Renders `children(org)` only for an organisation the caller can act on at `need`. Hiding is a courtesy: the
 * backend refuses a wrong-role request anyway, and says so, which the pages surface. */
export default function OrgGate({ need, children }: { need: "admin" | "owner"; children: (org: Org) => ReactNode }) {
  const { orgs, current } = useOrg();
  if (orgs.kind !== "ok") return <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={orgs} /></div>;
  if (!current) {
    return (
      <div className="empty" style={{ gridColumn: "1 / span 12" }}>
        <b>No organisation.</b>
        <p>This account isn&rsquo;t a member of any organisation, so there is nothing to administer.</p>
      </div>
    );
  }
  const ok = need === "owner" ? isOwnerRole(current.role) : isAdminRole(current.role);
  if (!ok) {
    return (
      <div className="empty" style={{ gridColumn: "1 / span 12" }}>
        <b>{need === "owner" ? "Owner access needed." : "Admin access needed."}</b>
        <p>You are {current.role === "admin" ? "an" : "a"} <b>{current.role}</b> of {current.name}. This page needs the <b>{need === "owner" ? "owner" : "owner or admin"}</b> role.</p>
      </div>
    );
  }
  return <>{children(current)}</>;
}
