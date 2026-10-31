"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

export const CONNECT_PAGES: Array<{ href: string; label: string; blurb: string }> = [
  { href: "/connect", label: "Overview", blurb: "Pick where you work." },
  { href: "/connect/claude-code", label: "Claude Code", blurb: "Install keळ in your terminal. About 10 minutes." },
  { href: "/connect/claude", label: "Claude on the web", blurb: "Add keळ as a connector on claude.ai. About 5 minutes." },
  { href: "/connect/chatgpt", label: "ChatGPT", blurb: "Add keळ as a custom connector." },
  { href: "/connect/help", label: "If something goes wrong", blurb: "Common problems and what to do." },
];

/** The buttons under the page header: one per subpage, the current one filled and marked with aria-current. */
export default function ConnectNav() {
  const path = (usePathname() ?? "").replace(/\/+$/, "") || "/";
  return (
    <nav className="frame" aria-label="Connect guides" style={{ display: "flex", flexWrap: "wrap", gap: 10, paddingBottom: 28 }}>
      {CONNECT_PAGES.map((p) => {
        const here = path === p.href;
        return (
          <Link key={p.href} href={p.href} className={here ? "btn-ink" : "btn-line"} aria-current={here ? "page" : undefined}>
            <span>{p.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
