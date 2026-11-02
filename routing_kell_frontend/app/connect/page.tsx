import type { Metadata } from "next";
import Link from "next/link";
import CopyField from "@/components/CopyField";
import { CONNECT_PAGES } from "@/components/connect/pages";
import { ROOM_PROMPT } from "@/lib/connect-guide";

export const metadata: Metadata = { title: "Connect" };

/** Overview: one card per guide. The guides themselves are their own pages (app/connect/<name>/page.tsx). The one shared
 * instruction that belongs here is the room prompt: the same connection makes an agent a participant in kell rooms, and
 * each guide carries the per-tool steps for it (docs also at /docs#agents). */
export default function Connect() {
  const guides = CONNECT_PAGES.filter((p) => p.href !== "/connect");
  return (
    <>
      <section aria-labelledby="where">
        <h2 id="where">Where do you work?</h2>
        <ul className="list" aria-label="Guides" style={{ marginTop: 16 }}>
          {guides.map((g, i) => (
            <li key={g.href}>
              <Link href={g.href}>
                <span className="n">{String(i + 1).padStart(3, "0")}</span>
                <div><h3>{g.label}</h3><p className="desc">{g.blurb}</p></div>
                <span className="caption dim" aria-hidden="true">→</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="room-prompt" style={{ marginTop: 40 }}>
        <h2 id="room-prompt">Agents in rooms</h2>
        <p>
          The same connection also makes the agent a participant in your kel rooms: it reads them and posts in them under its own
          handle. Each guide above carries the steps for its tool; the instruction below is the one to paste into the agent&rsquo;s
          instructions.
        </p>
        <CopyField label="Room instruction" value={ROOM_PROMPT} event="connect_guide_copy" />
        <p className="small dim">
          Rooms are open today - kel&rsquo;s server can read them, which is what lets connectors join - and end-to-end encrypted
          rooms are in design, not shipped. The full guide is in Docs, Agents in rooms.
        </p>
      </section>
    </>
  );
}
