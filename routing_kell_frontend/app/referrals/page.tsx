"use client";
import { useEffect, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import ReferralsPanel from "@/components/billing/ReferralsPanel";
import { billingApi } from "@/lib/billing";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";

const api = billingApi({ token: getAccessToken });

/**
 * Your referrals: share your code, see how many of your paid referrals it has earned on, and what has been paid into your
 * own wallet. A paid referral pays once, when the person you invited has paid; nothing is paid for a signup.
 */
export default function ReferralsPage() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); };
  }, []);
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ACCOUNT</b></div>
        <h1 className="display"><AnimatedHeading>Invite someone</AnimatedHeading></h1>
        <p className="lead">Share your code. When someone uses it and pays for a plan, you get credit for kell in your own wallet.</p>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="profile-wrap" style={{ gridColumn: "1 / span 12", display: "grid", gap: 28 }}>
          <ReferralsPanel api={api} session={session} />
        </div>
      </section>
    </>
  );
}
