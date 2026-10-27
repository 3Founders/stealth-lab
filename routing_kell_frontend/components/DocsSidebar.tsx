"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { LegalDoc } from "@/lib/legal";

const SECTIONS = [
  { id: "teams", label: "For teams" },
  { id: "legal", label: "Legal" },
] as const;
type SectionId = (typeof SECTIONS)[number]["id"];
const isSectionId = (v: string): v is SectionId => SECTIONS.some((s) => s.id === v);

export default function DocsSidebar({ legalDocs }: { legalDocs: LegalDoc[] }) {
  const [active, setActive] = useState<SectionId>("teams");

  useEffect(() => {
    const fromHash = () => {
      const h = window.location.hash.replace("#", "");
      if (isSectionId(h)) setActive(h);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  const go = (id: SectionId) => { window.location.hash = id; };

  return (
    <>
      <nav className="docs-nav" aria-label="Documentation">
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#${s.id}`} aria-current={active === s.id ? "page" : undefined} onClick={(e) => { e.preventDefault(); go(s.id); }}>
            {s.label}
          </a>
        ))}
      </nav>

      <div>
        {active === "teams" && (
          <section id="teams" className="doc-sec">
            <h2>For teams</h2>
            <p>For a company rolling keळ out to its developers. Team setup is in early access.</p>

            <h3>The admin, once</h3>
            <ol>
              <li>Connects the company&rsquo;s model provider key in the admin console. It is stored encrypted and never shown again.</li>
              <li>Sets a monthly budget and a daily budget per developer.</li>
              <li>Creates an invite. The console prints the one command developers run.</li>
            </ol>

            <h3>Each developer</h3>
            <pre><code>npx -y kell-mcp join &lt;invite&gt; --url &lt;mcp-address&gt; --api &lt;api-address&gt;</code></pre>
            <p>It signs them in with the company login, sets up their agents, and makes one small test call to confirm everything works. No provider key is ever stored on a developer&rsquo;s machine.</p>

            <h3>What the company gets</h3>
            <ul>
              <li>One provider key, held centrally, instead of one per developer.</li>
              <li>Budgets that apply to every call.</li>
              <li>Usage by person, and a record of every call, in the admin console.</li>
            </ul>
          </section>
        )}

        {active === "legal" && (
          <section id="legal" className="doc-sec">
            <h2>Legal</h2>
            <p>Terms, privacy and policy documents. These are drafts awaiting legal review and are not yet in effect.</p>
            <ul style={{ listStyle: "none", padding: 0 }}>
              {legalDocs.map((d) => (
                <li key={d.slug} style={{ borderBottom: "1px solid var(--rule)", padding: "10px 0" }}>
                  <Link href={`/docs/legal/${d.slug}`} style={{ fontWeight: 400, textDecoration: "underline" }}>{d.title}</Link>
                  <span className="dim small">: {d.blurb}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </>
  );
}
