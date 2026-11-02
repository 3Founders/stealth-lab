"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useDevicePhoto } from "@/components/DevicePhoto";
import { loadDeviceProfile, syncDevicePhoto } from "@/lib/device-profile";
import { billingApi, type PlanState } from "@/lib/billing";
import { planLine } from "@/lib/chat/plan";
import { getAccessToken } from "@/lib/session";

/**
 * A small card at the foot of the side panel: your photo and name, the plan you are on, and how syncing is going. It has a soft drop
 * shadow, the page's own fonts, and links to your profile. Its only requests are the photo sync and one read of your plan
 * (GET /v1/billing/plan); if billing is off or unreachable the plan line is simply not shown.
 */
export default function ProfileWidget({ subject, status, agents, onSignOut }: { subject: string; status: string; agents: number; onSignOut?: () => void }) {
  const photo = useDevicePhoto(subject);
  const [name, setName] = useState("");
  const [plan, setPlan] = useState<PlanState | null>(null);
  useEffect(() => {
    let live = true;
    void syncDevicePhoto(subject);
    void loadDeviceProfile().then((p) => { if (live && p) setName(p.name || (p.email ?? "").split("@")[0]); });
    void billingApi({ token: getAccessToken }).plan().then((r) => { if (live && r.kind === "ok") setPlan(r.data); }).catch(() => {});
    return () => { live = false; };
  }, [subject]);
  const initial = (name || "?").trim().charAt(0).toUpperCase();
  return (
    <div className="dg-me-wrap">
    <Link href="/account/profile" className="dg-me" aria-label="Your profile">
      <span className="dg-me-pic" aria-hidden="true">
        {/* eslint-disable-next-line @next/next/no-img-element -- a local data: URL */}
        {photo ? <img src={photo} alt="" /> : initial}
      </span>
      <span className="dg-me-text">
        <b>{name || "You"}</b>
        {planLine(plan) && <span className="dg-me-plan">{planLine(plan)}</span>}
        <span>{status} · {agents} agent{agents === 1 ? "" : "s"}</span>
      </span>
    </Link>
    {onSignOut && <button type="button" className="dg-signout" onClick={onSignOut}>Sign out</button>}
    </div>
  );
}
