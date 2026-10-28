import Link from "next/link";
import Image from "next/image";
import { COMMONS_ENABLED } from "@/lib/features";

// Only routes and anchors that exist in this build.
const cols = [
  { h: "Product", items: [["Evidence", "/evidence"], ["Data & trust", "/trust"], ["How a pilot starts", "/pilot"], ["Docs", "/docs"]] },
  ...(COMMONS_ENABLED
    ? [
        { h: "Commons", items: [["Goals", "/goals"], ["Search", "/search"], ["Contribution & rewards", "/docs#contribution-rewards"]] },
      ]
    : []),
  { h: "Account", items: [["Sign in", "/sign-in"], ...(COMMONS_ENABLED ? [["Credits", "/account/credits"]] : [])] },
  { h: "About", items: [["About keळ", "/#about"]] },
];

export default function Footer() {
  return (
    <footer className="footer">
      <div className="frame grid">
        <div className="footer-mark">
          <span className="footer-logo"><Image src="/kel-wordmark.png" alt="keळ" width={800} height={440} /></span>
          <p>Routine work on cheaper models, checked before you see it.</p>
        </div>
        {cols.map((c) => (
          <nav key={c.h} className="footer-col" aria-label={c.h}>
            <h4>{c.h}</h4>
            <ul>
              {c.items.map(([label, href]) => (
                <li key={label}>
                  {href.startsWith("http") ? <a href={href} rel="noopener">{label}</a> : <Link href={href}>{label}</Link>}
                </li>
              ))}
            </ul>
          </nav>
        ))}
        <nav className="footer-legal" aria-label="Legal">
          <span>Legal (drafts)</span>
          <Link href="/docs#legal">Legal documents</Link>
        </nav>
        <div className="footer-base">
          <span>© {new Date().getFullYear()} keळ</span>
          <span>Pre-release · v0.1 · install from source · legal docs are drafts</span>
          <span>Unknown ≠ false. Candidate ≠ verified.</span>
        </div>
      </div>
    </footer>
  );
}
