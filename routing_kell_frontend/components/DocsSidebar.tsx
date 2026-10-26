"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { LegalDoc } from "@/lib/legal";
import InstallCommand from "@/components/InstallCommand";

const SECTIONS = [
  { id: "getting-started", label: "Getting started" },
  { id: "how-to-use", label: "How to use" },
  { id: "stealth-folder", label: "The .kell folder" },
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
            <pre><code>npx -y stealthlab-mcp login</code></pre>
            <p>This opens your browser. After that, keळ keeps your sign-in fresh on its own.</p>

            <h3>3. Restart your agent</h3>
            <p>Quit and reopen Claude Code or Cursor so it picks keळ up. For Cursor, tell setup which model you use with <code>--cursor-model &lt;model&gt;</code>.</p>

            <h3>4. Once per repository</h3>
            <pre><code>npx -y stealthlab-mcp survey</code></pre>
            <p>This writes your repository&rsquo;s <a href="#stealth-folder" style={{ textDecoration: "underline" }} onClick={(e) => { e.preventDefault(); go("stealth-folder"); }}>.kell folder</a>. It runs on your machine and uploads nothing.</p>

            <h3>Other commands</h3>
            <table><tbody>
              <tr><td><code>npx -y stealthlab-mcp doctor</code></td><td>Check that keळ&rsquo;s server is reachable.</td></tr>
              <tr><td><code>npx -y stealthlab-mcp undo</code></td><td>Put back the last change keळ made in this repository.</td></tr>
              <tr><td><code>npx -y stealthlab-mcp uninstall</code></td><td>Remove keळ from your agents.</td></tr>
            </tbody></table>
          </section>
        )}

        {active === "how-to-use" && (
          <section id="how-to-use" className="doc-sec">
            <h2>How to use</h2>
            <p>Work the way you already do. Ask your agent for what you want, in your own words. keळ takes the tasks it can do for less, and leaves the rest to your agent&rsquo;s own model.</p>
            <p>A change from keळ reaches your code only after it passes your project&rsquo;s own checks. If it doesn&rsquo;t pass, nothing is applied and your agent does the task as it always has.</p>

            <h3>Getting good results</h3>
            <ul>
              <li><b>Have tests.</b> Your project&rsquo;s tests are how keळ knows a change works. The better they cover your code, the more tasks keळ can safely take.</li>
              <li><b>Say what you want done.</b> &ldquo;The CSV export drops the last row; fix it&rdquo; gives keळ more to go on than &ldquo;look at the export.&rdquo;</li>
              <li><b>Give it time in a repository.</b> keळ learns which kinds of tasks it handles well in your project, so the savings grow as you use it.</li>
            </ul>

            <h3>You stay in control</h3>
            <table><tbody>
              <tr><td><code>!opus</code> at the start of a prompt</td><td>Skip keळ for that prompt. Your agent&rsquo;s own model does it.</td></tr>
              <tr><td><code>npx -y stealthlab-mcp undo</code></td><td>Put back the last change keळ made. <code>--list</code> shows earlier ones; it refuses if you have edited those files since.</td></tr>
              <tr><td><code>npx -y stealthlab-mcp uninstall</code></td><td>Turn keळ off completely.</td></tr>
            </tbody></table>
          </section>
        )}

        {active === "stealth-folder" && (
          <section id="stealth-folder" className="doc-sec">
            <h2>The .kell folder</h2>
            <p>What keळ learns about a repository lives in that repository, in a folder named <code>.kell</code>. It is plain text: you can read it, edit it, and commit it like any other file.</p>

            <h3>What is in it</h3>
            <table><tbody>
              <tr><td><code>claims.md</code></td><td>Facts about your project, each pointing at the file and line it comes from.</td></tr>
              <tr><td><code>library.md</code></td><td>Problems already solved in this repository, each with the check that proved the fix.</td></tr>
              <tr><td><code>SUMMARY.md</code></td><td>A short overview of the above.</td></tr>
            </tbody></table>

            <h3>Why it helps</h3>
            <p>Each new task starts from what the repository already knows, instead of from zero. A problem like one solved last week gets done faster and for less.</p>

            <h3>Share it, or keep it</h3>
            <p>Commit <code>.kell</code> and everyone on your team starts from the same knowledge. Leave it out of git and it stays on your machine. Either way, you can correct or delete anything in it.</p>
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
            <pre><code>npx -y stealthlab-mcp join &lt;invite&gt; --url &lt;mcp-address&gt; --api &lt;api-address&gt;</code></pre>
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
