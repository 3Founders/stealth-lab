"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { LegalDoc } from "@/lib/legal";
import InstallCommand from "@/components/InstallCommand";

const SECTIONS = [
  { id: "getting-started", label: "Getting started" },
  { id: "teams", label: "For teams" },
  { id: "legal", label: "Legal" },
] as const;
type SectionId = (typeof SECTIONS)[number]["id"];
const isSectionId = (v: string): v is SectionId => SECTIONS.some((s) => s.id === v);

export default function DocsSidebar({ legalDocs }: { legalDocs: LegalDoc[] }) {
  const [active, setActive] = useState<SectionId>("getting-started");

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
        {active === "getting-started" && (
          <section id="getting-started" className="doc-sec">
            <h2>Getting started</h2>
            <p>Set keळ up once on your machine. It works in Claude Code and Cursor.</p>

            <h3>1. Install</h3>
            <p>You need Node.js 18.17 or later. One command sets up the agents it finds on your machine, then checks what is still missing and says how to fix each item.</p>
            <InstallCommand />
            <p className="small dim">It ends with a checklist. When something is missing, such as a sign-in or a model key, the fix is printed next to it. Run it again any time: it changes only what still needs changing. Add <code>--dry-run</code> to see what it would do first.</p>

            <h3>2. Sign in</h3>
            <pre><code>npx -y kell-mcp login</code></pre>
            <p>This opens your browser. After that, keळ keeps your sign-in fresh on its own.</p>

            <h3>3. Restart your agent</h3>
            <p>Quit and reopen Claude Code or Cursor so it picks keळ up. For Cursor, tell setup which model you use with <code>--cursor-model &lt;model&gt;</code>.</p>

            <h3>4. Once per repository</h3>
            <pre><code>npx -y kell-mcp survey</code></pre>
            <p>This writes your repository&rsquo;s .kell folder. It runs on your machine and uploads nothing.</p>

            <h3>Other commands</h3>
            <table><tbody>
              <tr><td><code>npx -y kell-mcp doctor</code></td><td>Check that keळ&rsquo;s server is reachable.</td></tr>
              <tr><td><code>npx -y kell-mcp undo</code></td><td>Put back the last change keळ made in this repository.</td></tr>
              <tr><td><code>npx -y kell-mcp uninstall</code></td><td>Remove keळ from your agents.</td></tr>
            </tbody></table>
          </section>
        )}

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
