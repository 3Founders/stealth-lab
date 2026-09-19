import Link from "next/link";
import Image from "next/image";
import { LEGAL_DOCS } from "@/lib/legal";

// Only routes and anchors that exist in this build.
const cols = [
  { h: "Product", items: [["Problems", "/problems"], ["Search", "/search"], ["Docs", "/docs"]] },
  {
    h: "Knowledge",
    items: [
      ["Goals", "/docs#goals"], ["Procedures", "/docs#procedures"], ["Implementations", "/docs#implementations"],
      ["Routes", "/docs#routes"], ["Runs", "/docs#runs"], ["Benchmarks", "/docs#benchmarks"],
    ],
  },
  { h: "Contribute", items: [["Submissions", "/docs#submissions"], ["API", "/docs#api"]] },
  { h: "Account & about", items: [["Sign in", "/sign-in"], ["About keळ", "/#about"], ["Source", "https://github.com/3Founders/stealth-lab"]] },
];

export default function Footer() {
  return (
    <footer className="footer">
      <div className="frame grid">
        <div className="footer-mark">
          <span className="footer-logo"><Image src="/kel-wordmark.png" alt="keळ" width={800} height={440} /></span>
          <p>Remember how things actually get done.</p>
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
          {LEGAL_DOCS.map((d) => <Link key={d.slug} href={`/docs/legal/${d.slug}`}>{d.title}</Link>)}
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
