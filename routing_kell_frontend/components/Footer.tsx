import Link from "next/link";
import Image from "next/image";

// Only routes and anchors that exist in this build.
const cols = [
  { h: "Product", items: [["See it work", "/#action"], ["Docs", "/docs"]] },
  {
    h: "How to use",
    items: [
      ["Getting started", "/docs#getting-started"], ["For teams", "/docs#teams"],
    ],
  },
  { h: "Account", items: [["Sign in", "/sign-in"], ["Connected apps", "/account/connections"]] },
  { h: "About", items: [["About keळ", "/#about"]] },
];

export default function Footer() {
  return (
    <footer className="footer">
      <div className="frame grid">
        <div className="footer-mark">
          <span className="footer-logo"><Image src="/kel-wordmark.png" alt="keळ" width={800} height={440} /></span>
          <p>Recall, reuse, route, reduce.</p>
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
          <span>Pre-release · v0.1 · legal docs are drafts</span>
        </div>
      </div>
    </footer>
  );
}
