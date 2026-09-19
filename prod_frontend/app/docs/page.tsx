import type { Metadata } from "next";
import Link from "next/link";
import { LEGAL_DOCS } from "@/lib/legal";

export const metadata: Metadata = { title: "Docs" };

const nav = [
  ["getting-started", "Getting started"], ["concepts", "Concepts"], ["goals", "Goals"], ["procedures", "Procedures"],
  ["implementations", "Implementations"], ["routes", "Routes"], ["runs", "Runs"], ["execution", "Execution"],
  ["verification", "Verification"], ["benchmarks", "Benchmarks"], ["submissions", "Submissions"], ["api", "API"], ["legal", "Legal"],
];

export default function Docs() {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>DOCS</b></div>
        <h1 className="display">How to use it.</h1>
        <p className="lead">Practical and current. Where the code and these pages disagree, the code wins.</p>
      </section>

      <div className="frame docs">
        <nav className="docs-nav" aria-label="Documentation">
          {nav.map(([id, t]) => <a key={id} href={`#${id}`}>{t}</a>)}
        </nav>
        <div>
          <section id="getting-started" className="doc-sec">
            <h2>Getting started</h2>
            <p>keळ installs from source today. There is no hosted installer and no PyPI release yet.</p>
            <pre><code>{`git clone https://github.com/3Founders/stealth-lab
cd stealth-lab
pip install -e packaging/`}</code></pre>
            <p>This provides four entry points:</p>
            <table><tbody>
              <tr><td><code>stealthlab-mcp-server</code></td><td>The MCP server (Streamable HTTP on loopback, or <code>--stdio</code>).</td></tr>
              <tr><td><code>stealthlab-trace-hook</code></td><td>Claude Code hook: redact and collect one trace event.</td></tr>
              <tr><td><code>stealthlab-status-page</code></td><td>Read-only view: episodes → claims → procedures.</td></tr>
              <tr><td><code>stealthlab-public-board</code></td><td>Static scoreboard from a real-arms sweep and spend ledger.</td></tr>
            </tbody></table>
            <p>The MCP server needs Postgres 15+ with the <code>pgvector</code> extension (use <code>pgvector/pgvector:pg15</code>; stock <code>postgres:15</code> can’t run the migrations), and a <code>backend/.env</code> with at least <code>DATABASE_URL</code> and <code>VOYAGE_API_KEY</code>. HTTP mode also needs <code>STEALTHLAB_MCP_TOKEN</code>. Your private library is a local SQLite file and needs none of this.</p>
            <p>To bring in existing work:</p>
            <pre><code>{`cd backend
python scripts/bootstrap.py --repo-root /path/to/repo`}</code></pre>
            <p>Full setup: <code>packaging/README.md</code> and <code>backend/README_MCP_SERVER.md</code> in the repository.</p>
          </section>

          <section id="concepts" className="doc-sec">
            <h2>Concepts</h2>
            <p><b style={{ fontWeight: 400 }}>Goal</b> — what someone wants. <b style={{ fontWeight: 400 }}>Procedure</b> — how it can be done. <b style={{ fontWeight: 400 }}>Implementation</b> — with what mechanism. <b style={{ fontWeight: 400 }}>Route</b> — a candidate Goal → Procedure → Implementation path. <b style={{ fontWeight: 400 }}>Run</b> — what actually happened. <b style={{ fontWeight: 400 }}>Claim</b> — reusable general knowledge. <b style={{ fontWeight: 400 }}>Evidence</b> — support and provenance. <b style={{ fontWeight: 400 }}>Benchmark</b> — a defined evaluation.</p>
            <p>They are kept apart on purpose. Unknown ≠ false; candidate ≠ verified; a source statement ≠ an established fact.</p>
          </section>

          <section id="goals" className="doc-sec">
            <h2>Goals</h2>
            <p>A Goal states what should be true or done. Related Goals and the ways of reaching them are grouped as a <a href="/problems" style={{ textDecoration: "underline" }}>Problem</a>. Search starts from a Goal: <code>GET /v1/problems/find?q=…</code>.</p>
          </section>

          <section id="procedures" className="doc-sec">
            <h2>Procedures</h2>
            <p>Every Procedure is born a <code>candidate</code>. It reaches <code>verified</code> only on independent supporting evidence across distinct contexts; retrying the same context doesn’t count. A failure lowers capability, it doesn’t raise it. When a relevant precondition changes, a Procedure goes <code>stale</code> and stops being selected, with a cited reason.</p>
          </section>

          <section id="implementations" className="doc-sec">
            <h2>Implementations</h2>
            <p>The Implementation Registry holds the concrete mechanisms. Each exposes a deterministic, secret-free execution descriptor: <code>GET /v1/implementations/&#123;id&#125;/descriptor</code>.</p>
          </section>

          <section id="routes" className="doc-sec">
            <h2>Routes</h2>
            <p>A Route pairs a Procedure with an Implementation for a Goal. Several may exist. Selection weighs applicability, constraints, evidence, performance and cost. A local applicability check that returns <code>UNKNOWN</code> fails closed.</p>
          </section>

          <section id="runs" className="doc-sec">
            <h2>Runs</h2>
            <p>A Run is one attempt, kept as it happened. Production execution runs on a durable graph: a crashed Run can be resumed, completed nodes aren’t re-run, and exactly one immutable record is appended when it ends.</p>
          </section>

          <section id="execution" className="doc-sec">
            <h2>Execution</h2>
            <p>Execution is available through the MCP tools (for example <code>find_best_way</code> and <code>reproduce_procedure</code>). A Run may ask for a person: missing information, credentials, approval, an override, or verification.</p>
          </section>

          <section id="verification" className="doc-sec">
            <h2>Verification</h2>
            <p>Outcomes are checked against the Goal, not against the Run’s own claim of success. Chat evidence stays honest: a recommendation is never upgraded to “executed” by an unrelated later “tests passed”.</p>
          </section>

          <section id="benchmarks" className="doc-sec">
            <h2>Benchmarks</h2>
            <p>An Evaluation aggregates real Runs and Evidence under a version-pinned Benchmark. A Problem’s current-best Solution is derived on read from a Wilson lower bound — never stored — and is empty until something is verified. Results are contextual, not universal.</p>
          </section>

          <section id="submissions" className="doc-sec">
            <h2>Submissions</h2>
            <p>Publishing is explicit. A trusted private Procedure is scrubbed and enters the shared commons as a fresh <code>candidate</code>; the local verification count isn’t copied. Another user’s own applicability check and execution then produce independent evidence.</p>
          </section>

          <section id="api" className="doc-sec">
            <h2>API</h2>
            <p>The backend exposes a read-oriented REST API alongside the MCP server:</p>
            <pre><code>{`/v1/claims        /v1/procedures     /v1/solutions
/v1/problems      /v1/implementations /v1/search
/v1/repositories  /v1/projects       /v1/tasks   /v1/me`}</code></pre>
            <p>No client or quickstart exists for it yet beyond the code and the backend README. This site’s Problems and Search pages read <code>/v1/problems</code>, <code>/v1/problems/find</code> and <code>/v1/search</code> when <code>NEXT_PUBLIC_KEL_API_URL</code> is set.</p>
          </section>

          <section id="legal" className="doc-sec">
            <h2>Legal</h2>
            <p>Terms, privacy and policy documents. These are drafts awaiting legal review and are not yet in effect.</p>
            <ul style={{ listStyle: "none", padding: 0 }}>
              {LEGAL_DOCS.map((d) => (
                <li key={d.slug} style={{ borderBottom: "1px solid var(--rule)", padding: "10px 0" }}>
                  <Link href={`/docs/legal/${d.slug}`} style={{ fontWeight: 400, textDecoration: "underline" }}>{d.title}</Link>
                  <span className="dim small"> — {d.blurb}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </>
  );
}
