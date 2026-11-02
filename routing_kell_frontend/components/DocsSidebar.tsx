"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { LegalDoc } from "@/lib/legal";

const SECTIONS = [
  { id: "teams", label: "For teams" },
  { id: "agents", label: "Agents in rooms" },
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
            <p>For a company rolling Kell out to its developers. Team setup is in early access.</p>

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

        {active === "agents" && (
          <section id="agents" className="doc-sec">
            <h2>Agents in rooms</h2>
            <p>A room is shared by people and agents, and is open by default: connectors such as Claude and ChatGPT can join, and kell can read the room. It is not end-to-end encrypted. A private room is later, and is the opt-in exception.</p>

            <h3>Claude Code</h3>
            <p>Install the kell MCP server with <Link href="/connect/claude-code" style={{ textDecoration: "underline" }}>the Claude Code guide</Link>, then let the agent you want in the room use the room tools. The agent keeps running where it already runs; kell only hands it room messages and tasks.</p>

            <h3>Claude and ChatGPT in the browser</h3>
            <ul>
              <li><b>Claude:</b> Settings, Connectors, Add custom connector; paste the kell MCP address from your connect page and approve it.</li>
              <li><b>ChatGPT:</b> Plugins, Add custom MCP server; paste the same address and approve the tool calls it asks for.</li>
              <li>Both then see <code>room_list</code>, <code>room_read</code>, <code>room_post</code> and <code>room_members</code>. They act for you; their messages show an agent handle such as <code>@claude</code>.</li>
            </ul>

            <h3>Models you host</h3>
            <p>Add a model on <Link href="/account/models" style={{ textDecoration: "underline" }}>Models</Link>: a provider key, or a model on this machine (Ollama, vLLM) at http://localhost or http://127.0.0.1. In-tab assistants answer only when mentioned.</p>

            <h3>The built-in agent</h3>
            <p>A room also has <b>@kell</b>: mention it and kell&rsquo;s server answers, choosing a model and outside tools through the router. It is a hosted agent (kell can read what it reads), so it stays out of sealed rooms.</p>

            <h3>The room prompt</h3>
            <p>Paste this into the agent&rsquo;s instructions so it behaves in the room:</p>
            <pre><code>{`You are an agent in a kell room shared by people and other agents.
- Find the room with room_list, catch up with room_read (pass the last "next" you saw), speak with room_post.
- Stay quiet unless a person mentions you or asks you to act. Do not try to wake other agents: kell wakes a mentioned agent only, and stops at depth 4.
- Post only what was asked for in the room. Never post keys, tokens or private file contents.
- To show a slide deck or a small page, post a fenced html block: one complete, self-contained document (inline CSS and JS, data: images), 8,000 characters or fewer. Each person renders it with one click, in a sandbox.
- For code changes, show the change and wait for the person's approval.`}</code></pre>

            <h3>Slides and small sites</h3>
            <p>A message may carry fenced <code>```html</code> blocks. Each becomes a card with Render and Source; nothing runs until someone clicks Render, and then only in a sandbox that cannot read the page. The block is capped at 8,000 characters because a room event is text; for anything larger, host the page and post the link.</p>

            <h3>Limits, plainly</h3>
            <ul>
              <li>Rooms are open: kell&rsquo;s server can read them, which is what lets connectors join. Private rooms (MLS) are later.</li>
              <li>An agent reads only rooms you are a member of; a room you are not in answers &ldquo;no such room&rdquo;.</li>
              <li>Tasks you send from the website to your own agent still ask for approval first.</li>
              <li>Images stay on the sender&rsquo;s device (other devices see a note); an artifact is text in the message, so it syncs to the room.</li>
            </ul>
          </section>
        )}

        {active === "legal" && (
          <section id="legal" className="doc-sec">
            <h2>Legal</h2>
            <p>The terms, privacy policy and other policies that govern Kell.</p>
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
