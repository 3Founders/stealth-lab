"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import SubmitWay from "@/components/submit/SubmitWay";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";
import { submissionsApi } from "@/lib/submissions-api";

/** Submit a Way: a goal and the steps to reach it, reviewed step by step by kell (docs/goal_submission.md). Signed-in accounts only. */
export default function SubmitPage() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const api = useMemo(() => submissionsApi({ token: getAccessToken }), []);
  const [initial, setInitial] = useState<string | undefined>(undefined);
  useEffect(() => {
    void getSession().then(setSession);
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) setInitial(id);
    return onSessionChange(setSession);
  }, []);
  return (
    <section className="page-hero frame grid">
      <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>SUBMIT A WAY</b></div>
      <h1 className="display"><AnimatedHeading>Teach kell a way to get something done.</AnimatedHeading></h1>
      <div style={{ gridColumn: "1 / -1", maxWidth: 820, width: "100%" }}>
        {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
        {session === null && <p>Submitting is for signed-in accounts. <Link href="/sign-in?redirect=/submit" style={{ textDecoration: "underline" }}>Sign in</Link>.</p>}
        {session && <SubmitWay api={api} initial={initial} key={initial || "new"} />}
      </div>
    </section>
  );
}
