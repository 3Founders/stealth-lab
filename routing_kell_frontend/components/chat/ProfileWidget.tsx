"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useDevicePhoto } from "@/components/DevicePhoto";
import { loadDeviceProfile, syncDevicePhoto } from "@/lib/device-profile";
import { usePlan } from "@/components/billing/PlanBadge";
import { planText } from "@/lib/chat/plan";

/**
 * A small card at the foot of the side panel: your photo and name, the plan you are on, and how syncing is going. It has a soft drop
 * shadow, the page's own fonts, and links to your profile. Its only requests are the photo sync and one read of your plan
 * (GET /v1/billing/plan). The plan line always says something: the plan and its end date, "No plan yet", or why it could not be read.
 */
export default function ProfileWidget({ subject, status, agents, onSignOut }: { subject: string; status: string; agents: number; onSignOut?: () => void }) {
  const photo = useDevicePhoto(subject);
  const [name, setName] = useState("");
  const plan = usePlan();
  useEffect(() => {
    let live = true;
    void syncDevicePhoto(subject);
    void loadDeviceProfile().then((p) => { if (live && p) setName(p.name || (p.email ?? "").split("@")[0]); });
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
        <span className="dg-me-plan">{planText(plan)}</span>
        <span>{status} · {agents} agent{agents === 1 ? "" : "s"}</span>
      </span>
    </Link>
    {onSignOut && <button type="button" className="dg-signout" onClick={onSignOut}>Sign out</button>}
    </div>
  );
}
