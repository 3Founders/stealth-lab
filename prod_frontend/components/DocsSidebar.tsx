"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { LegalDoc } from "@/lib/legal";

const SECTIONS = [
  { id: "getting-started", label: "Getting started" },
  { id: "how-to-use", label: "How to use" },
  { id: "contribution-rewards", label: "Contribution & rewards" },
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
            <p>Get MCP installed and connected.</p>

            <h3>1. Clone and install</h3>
            <pre><code>{`git clone https://github.com/3Founders/stealth-lab
cd stealth-lab
pip install -e packaging/`}</code></pre>
            <p>Installs from source. There is no hosted installer or PyPI release yet.</p>

            <h3>2. Configure the MCP server</h3>
            <p>In <code>backend/.env</code>, set at minimum:</p>
            <table><tbody>
              <tr><td><code>DATABASE_URL</code></td><td>Your Postgres 15+ database (needs the <code>pgvector</code> extension).</td></tr>
              <tr><td><code>VOYAGE_API_KEY</code></td><td>Used for embeddings.</td></tr>
              <tr><td><code>STEALTHLAB_MCP_TOKEN</code></td><td>Only needed when hosting the server over HTTP.</td></tr>
            </tbody></table>

            <h3>3. Start MCP locally</h3>
            <p>The MCP server lets your coding agent search keळ, inspect ways, check whether they apply, run/reproduce them, and record what happened.</p>
            <p>Quickstart with the MCP Inspector:</p>
            <pre><code>{`cd backend
MCP_SERVER_REQUEST_TIMEOUT=300000 mcp dev app/mcp_server/server.py:server --with-editable .`}</code></pre>

            <p className="small dim" style={{ marginTop: 14 }}>
              For deeper setup, see <code>backend/README_MCP_SERVER.md</code> in the repository.
            </p>
          </section>
        )}

        {active === "how-to-use" && (
          <section id="how-to-use" className="doc-sec">
            <h2>How to use</h2>

            <h3>Use MCP</h3>
            <p>Tell keळ what you are trying to accomplish, including relevant environment and constraints. For example:</p>
            <pre><code>{`"I need to deploy this service to staging without changing the existing database configuration."`}</code></pre>
            <p>keळ can:</p>
            <ul>
              <li>find relevant ways</li>
              <li>inspect a way</li>
              <li>check whether it applies</li>
              <li>run/reproduce it</li>
              <li>record what happened</li>
            </ul>

            <h3>Navigate the website</h3>
            <table><tbody>
              <tr><td><Link href="/problems" style={{ textDecoration: "underline" }}>Problems</Link></td><td>Start from a Goal and see the available ways, evidence, benchmarks, and contributor activity.</td></tr>
              <tr><td><Link href="/search" style={{ textDecoration: "underline" }}>Search</Link></td><td>Search the shared knowledge directly.</td></tr>
              <tr><td>Procedure</td><td>Open a way to see when it applies, its steps, evidence, and recent outcomes.</td></tr>
              <tr><td><Link href="/account/credits" style={{ textDecoration: "underline" }}>Credits</Link></td><td>Signed-in contributors can see their private Credits and Standing.</td></tr>
            </tbody></table>

            <h3>Search manually</h3>
            <p><Link href="/search" style={{ textDecoration: "underline" }}>/search</Link> lets you search without MCP. Today it covers Problems, plus Claims (coming soon). Some things to try:</p>
            <ul>
              <li>deploy a service to staging</li>
              <li>rotate a leaked credential</li>
              <li>add a health check endpoint</li>
            </ul>
            <p className="small dim">These are examples of the kind of goal to search for, not a guarantee that a matching result exists yet.</p>

            <h3>Contribution & rewards</h3>
            <p>You can add a problem, contribute a way, improve a way, add a benchmark, record evidence, or report a failure.</p>
            <p>Useful accepted contributions and verified independent reuse can earn Credits.</p>
            <p><a href="#contribution-rewards" style={{ textDecoration: "underline" }} onClick={(e) => { e.preventDefault(); go("contribution-rewards"); }}>Read the full contribution &amp; reward system →</a></p>
          </section>
        )}

        {active === "contribution-rewards" && (
          <section id="contribution-rewards" className="doc-sec">
            <h2>Contribution &amp; rewards</h2>

            <h3>How contribution works</h3>
            <ol>
              <li>You submit something useful.</li>
              <li>keळ checks it and it becomes a candidate or needs review.</li>
              <li>A reviewer can accept or reject it.</li>
              <li>Accepted does not mean verified.</li>
              <li>Real execution and independent evidence are what build verification.</li>
            </ol>

            <h3>What you can contribute</h3>
            <table><tbody>
              <tr><td><Link href="/problems/add" style={{ textDecoration: "underline" }}>Add a problem</Link></td><td>A goal worth accomplishing that isn&rsquo;t in keळ yet.</td></tr>
              <tr><td>Add a way</td><td>A procedure for accomplishing a Goal.</td></tr>
              <tr><td>Improve a way</td><td>A correction or better version of an existing procedure.</td></tr>
              <tr><td>Add a benchmark</td><td>A clear way to check whether a Goal was achieved.</td></tr>
              <tr><td>Add evidence</td><td>What actually happened during use.</td></tr>
              <tr><td>Report a failure</td><td>Evidence that a way did not work in a particular context.</td></tr>
            </tbody></table>

            <h3>What earns Credits</h3>
            <table><tbody>
              <tr><td>+10</td><td>Accepted new procedure</td></tr>
              <tr><td>+20</td><td>Accepted improvement</td></tr>
              <tr><td>+5</td><td>Verified independent reuse</td></tr>
            </tbody></table>
            <p>When someone else independently uses your way and the outcome is verified, the contributor can earn the reuse reward. When that way is an improvement, its immediate parent can receive a smaller share.</p>

            <h3>What does not earn Credits</h3>
            <ul>
              <li>Views</li>
              <li>Copies</li>
              <li>Your own use</li>
              <li>A claimed success</li>
              <li>Duplicate submissions</li>
            </ul>

            <h3>Credits vs Standing</h3>
            <table><tbody>
              <tr><td>Credits</td><td>Internal reward. Not cash. Not transferable/cash-out in V1.</td></tr>
              <tr><td>Standing</td><td>Separate contribution trust/history. It cannot be spent.</td></tr>
            </tbody></table>

            <p className="lead" style={{ marginTop: 18 }}>Make something useful → let others use it → verified reuse can earn Credits → keep improving it.</p>
            <p style={{ marginTop: 24 }}><Link href="/problems" style={{ textDecoration: "underline" }}>Start contributing →</Link></p>
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
