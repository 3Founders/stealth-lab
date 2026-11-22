import type { Metadata } from "next";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";

export const metadata: Metadata = {
  title: "Data & trust",
  description: "Where your data goes, what is recorded, who can change what, and what we do not have yet.",
};

export default function Trust() {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>DATA &amp; TRUST</b></div>
        <h1 className="display"><AnimatedHeading>Data &amp; trust</AnimatedHeading></h1>
        <p className="lead">Where your data goes, what is recorded, who can change what, and what we do not have yet.</p>
      </section>

      <div className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="doc-sec" style={{ gridColumn: "1 / span 12" }}>
          <h2>Where your data goes</h2>
          <p><b>keळ is not a zero-egress product.</b> When a task is routed, this is what leaves your machine:</p>
          <table>
            <thead><tr><th>What</th><th>Goes to</th><th>Why</th></tr></thead>
            <tbody>
              <tr><td>The task prompt and facts about your repository</td><td>The keळ server</td><td>To find a procedure that fits and to decide where to send the task.</td></tr>
              <tr><td>The task and the files it needs</td><td>The lower-cost model providers configured for your workspace</td><td>To have the cheaper model attempt the task.</td></tr>
              <tr><td>Requests to Anthropic</td><td>Anthropic, through your own login</td><td>keळ does not sit in that path or hold that login.</td></tr>
              <tr><td>Text sent for embedding and judging</td><td>Third-party providers used by the keळ backend</td><td>Retrieval and checking. They are listed on the <Link href="/docs/legal/subprocessors" style={{ textDecoration: "underline" }}>subprocessors page</Link>, which is still a draft.</td></tr>
            </tbody>
          </table>
          <p className="small dim">
            Trace text is passed through a redaction step before it is stored. Redaction is pattern-based and can miss things; do not rely on it to make
            confidential material safe to send.
          </p>

          <h2>What is recorded, and what can be changed</h2>
          <ul>
            <li><b>Nothing is deleted.</b> An update closes the old record&rsquo;s validity window and adds a new one, so you can see what was believed at any earlier time.</li>
            <li><b>An audit log.</b> Administrative actions and policy changes are recorded in an append-only log whose integrity the admin console checks.</li>
            <li><b>Recorded approval for shared knowledge.</b> The tools an agent can call do not write to the shared knowledge graph directly. Changes go through a decision step that is stored with who made it.</li>
            <li><b>Routed tasks leave a record on your machine</b> (a hook log and a cost file) of which model answered and what it cost. Showing this per answer in the admin console is not built yet.</li>
            <li><b>Refusals are advisory for now.</b> When a procedure&rsquo;s conditions no longer hold, the agent is told and the reason is recorded. It is not yet blocked.</li>
          </ul>

          <h2>Your controls</h2>
          <ul>
            <li>An organisation-wide kill switch and a spending policy in the admin console.</li>
            <li>Routing can be turned off for a person or a machine, and the work then goes straight to the model they chose.</li>
            <li>Keys for model providers are referenced by name and are never shown in full after they are saved.</li>
          </ul>

          <h2>What we do not have yet</h2>
          <ul>
            <li>No SOC 2 report.</li>
            <li>No third-party penetration test.</li>
            <li>No signed zero-retention terms with every model provider, so we do not claim zero retention.</li>
            <li>No measured availability: the service runs as a single instance and we have no uptime history to quote.</li>
            <li>The <Link href="/docs#legal" style={{ textDecoration: "underline" }}>legal documents</Link> are drafts.</li>
          </ul>
          <p className="small dim">We would rather you hear this from us before a security review than during one.</p>
        </div>
      </div>
    </>
  );
}
