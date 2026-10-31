import type { Metadata } from "next";
import Link from "next/link";
import { CONNECT_PAGES } from "@/components/connect/pages";

export const metadata: Metadata = { title: "Connect" };

/** Overview: one card per guide. The guides themselves are their own pages (app/connect/<name>/page.tsx). */
export default function Connect() {
  const guides = CONNECT_PAGES.filter((p) => p.href !== "/connect");
  return (
    <section aria-labelledby="where">
      <h2 id="where">Where do you work?</h2>
      <ul className="list" aria-label="Guides" style={{ marginTop: 16 }}>
        {guides.map((g, i) => (
          <li key={g.href}>
            <Link href={g.href}>
              <span className="n">{String(i + 1).padStart(3, "0")}</span>
              <div><h3>{g.label}</h3><p className="desc">{g.blurb}</p></div>
              <span className="caption dim" aria-hidden="true">→</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
