"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { billingApi, type Loaded, type PlanState } from "@/lib/billing";
import { planText } from "@/lib/chat/plan";
import { getAccessToken } from "@/lib/session";

/** The plan you are on, read from your account: its name and when it ends, "No plan yet", or why it could not be read. */
export function usePlan(): Loaded<PlanState> | null {
  const [r, setR] = useState<Loaded<PlanState> | null>(null);
  useEffect(() => {
    let live = true;
    billingApi({ token: getAccessToken }).plan().then((x) => { if (live) setR(x); }).catch(() => { if (live) setR({ kind: "error", message: "Couldn't load your plan." }); });
    return () => { live = false; };
  }, []);
  return r;
}

export default function PlanBadge() {
  const r = usePlan();
  const none = r?.kind === "ok" && !r.data.plan;
  return (
    <p className="plan-badge" role="status">
      <b>{planText(r)}</b>{" "}
      <Link href={none ? "/pricing" : "/account/billing"} style={{ textDecoration: "underline" }}>{none ? "See plans" : "Billing"}</Link>
    </p>
  );
}
