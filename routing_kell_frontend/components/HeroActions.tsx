"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { track } from "@/lib/analytics";
import { getSession } from "@/lib/session";

/**
 * The two calls to action under the hero text. "Get started" leads a signed-out visitor to create an account and a
 * signed-in one to /connect, where an agent is added; "Talk to a founder" is the secondary, outlined button and goes to /contact.
 * The administrator sign-in is not here: it is a small link beside Sign in in the header, so it cannot be mistaken for these.
 */
export default function HeroActions() {
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSignedIn(Boolean(s)); });
    return () => { live = false; };
  }, []);
  return (
    <div className="hero-actions">
      <Link href={signedIn ? "/connect" : "/sign-in"} className="btn-ink" onClick={() => track("get_started_click", { where: "hero" })}>
        <span>Get started</span><span className="sq" aria-hidden="true">→</span>
      </Link>
      <Link href="/contact" className="btn-line" onClick={() => track("contact_click", { where: "hero" })}>
        Talk to a founder
      </Link>
    </div>
  );
}
