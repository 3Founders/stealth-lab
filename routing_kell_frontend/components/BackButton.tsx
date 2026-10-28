"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

/**
 * "Back" for every page except the homepage and the full-screen Delegator. Built to current guidance (WCAG 2.2 AA, the GOV.UK
 * back link, Next.js navigation):
 *  - it is a real link to "/", so it works without JavaScript, in a new tab, and for someone who arrived on a shared link;
 *  - once the visitor has moved between pages of THIS site, a plain click goes back in history instead (router.back()), so it
 *    returns to where they came from. A new-tab or modified click is left to the browser. History is never rewritten, so the
 *    browser's own Back button is never trapped;
 *  - the label says what it will do: "Back" when history is ours, "Back to home" when the link would go home;
 *  - 44px tall, 4.5:1 text, 3:1 border and focus ring (see .back-btn in globals.css); it sits before <main>, above the heading.
 * Whether earlier history is ours is tracked from route changes seen by this component (which stays mounted in the layout),
 * not guessed from history.length or the referrer, neither of which says where the previous entry is.
 */
export default function BackButton() {
  const pathname = usePathname();
  const router = useRouter();
  const first = useRef(true);
  const [ownHistory, setOwnHistory] = useState(false);

  useEffect(() => {
    if (first.current) { first.current = false; return; }
    setOwnHistory(true);
  }, [pathname]);

  // No back control on the homepage, and none on the full-screen Delegator (it has its own way home).
  if (pathname === "/" || pathname.startsWith("/delegator")) return null;

  const onClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (!ownHistory || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    router.back();
  };

  return (
    <Link href="/" className="back-btn" onClick={onClick}>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M13 8H3M7 4 3 8l4 4" />
      </svg>
      <span>{ownHistory ? "Back" : "Back to home"}</span>
    </Link>
  );
}
