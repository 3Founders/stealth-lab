"use client";
import { usePathname, useRouter } from "next/navigation";

// A small "go back" affordance on every page except the homepage. Uses
// real browser history (router.back()) so it always returns to wherever
// the visitor actually came from, not a hardcoded parent route; falls
// back to "/" only when there's no history to go back to (e.g. the page
// was opened directly, such as from a bookmark or a shared link).
export default function BackButton() {
  const pathname = usePathname();
  const router = useRouter();
  // No back button on the homepage, and none on the full-screen Delegator (it has its own way home).
  if (pathname === "/" || pathname.startsWith("/delegator")) return null;

  const onClick = () => {
    if (typeof window !== "undefined" && window.history.length > 1) router.back();
    else router.push("/");
  };

  return (
    <button type="button" className="back-btn" onClick={onClick}>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M13 8H3M7 4 3 8l4 4" />
      </svg>
      <span>Back</span>
    </button>
  );
}
