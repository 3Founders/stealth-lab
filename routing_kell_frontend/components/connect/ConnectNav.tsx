"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { CONNECT_PAGES } from "./pages";

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
