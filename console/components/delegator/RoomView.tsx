"use client";
import { MessageScroller, useMessageScrollerScrollable } from "@shadcn/react/message-scroller";
import { useEffect, useMemo, useRef, useState } from "react";
import Composer from "@/components/delegator/Composer";
import { useSpringScroll } from "@/components/delegator/useSpringScroll";
import { agentIdentity, parseCommand, recipientsFor, toMcpPayload } from "@/lib/delegator/adapters/mcp";
import { attachMockAgent } from "@/lib/delegator/adapters/mock";
import { Broker } from "@/lib/delegator/relay/broker";
import { openChannel } from "@/lib/delegator/relay/channel";
import type { AgentActivity, AgentIdentity, GroupMessage, Room } from "@/lib/delegator/types";
import { MAX_DEPTH } from "@/lib/delegator/types";
import { groupMessages, initials } from "@/lib/delegator/ui";

const HUMANS: AgentIdentity[] = [
  { handle: "@alice", displayName: "Alice", type: "human", status: "online" },
  { handle: "@bob", displayName: "Bob", type: "human", status: "online" },
];
const SEED_AGENTS = [agentIdentity("@alice", "claude-code"), agentIdentity("@bob", "opencode"), agentIdentity("@bob", "cursor")];
const ROOMS: Room[] = [
  { id: "#auth-refactor-squad", title: "Auth refactor squad", members: ["@alice", "@bob", "@alice/claude-code", "@bob/opencode"] },
  { id: "#general-agents", title: "General agents", members: ["@alice", "@bob", "@alice/claude-code", "@bob/opencode", "@bob/cursor"] },
];
const LABEL: Record<AgentActivity, string> = { idle: "Idle", processing: "Processing", delivering: "Delivering via Relay" };

type Line = { kind: "msg"; m: GroupMessage } | { kind: "notice"; id: string; text: string };

function makeBroker(): { broker: Broker; agents: Map<string, AgentIdentity> } {
  const broker = new Broker();
  ROOMS.forEach((r) => broker.addRoom(r));
  const agents = new Map<string, AgentIdentity>();
  SEED_AGENTS.forEach((a) => { agents.set(a.handle, a); attachMockAgent(broker, a); });
  return { broker, agents };
}

const time = (t: number) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function Avatar({ who }: { who: AgentIdentity }) {
  return <span className="dg-avatar" data-kind={who.type === "human" ? "human" : "agent"} aria-hidden="true">{initials(who.handle)}</span>;
}

function Body({ m }: { m: GroupMessage }) {
  const sp = m.payload.structuredPayload;
  return (
    <div className="dg-body">
      <p>{m.payload.text}</p>
      {sp && m.sender.type !== "human" && (
        <details className="dg-payload">
          <summary><span>{sp.action}</span><code>{sp.protocol}</code><span className="dg-hop">hop {m.depthCounter}</span></summary>
          <pre>{JSON.stringify(sp, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

/** Only offered when there is more below the viewport. */
function JumpToLatest({ onJump }: { onJump: () => void }) {
  const { end } = useMessageScrollerScrollable();
  if (!end) return null;
  return <button type="button" className="dg-jump" onClick={onJump}>Jump to latest</button>;
}

/** The dev sandbox: group chat for people and agents over an in-memory relay. Nothing here leaves this browser. */
export default function RoomView() {
  const world = useRef<ReturnType<typeof makeBroker> | null>(null);
  if (!world.current) world.current = makeBroker();
  const { broker, agents } = world.current;

  const [roomId, setRoomId] = useState(ROOMS[0].id);
  const [me, setMe] = useState(HUMANS[0]);
  const [lines, setLines] = useState<Record<string, Line[]>>({});
  const [activity, setActivity] = useState<Record<string, AgentActivity>>({});
  const [members, setMembers] = useState<Record<string, string[]>>(Object.fromEntries(ROOMS.map((r) => [r.id, r.members])));
  const [paused, setPaused] = useState(false);
  const chan = useRef<ReturnType<typeof openChannel> | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const { scrollToEnd, follow } = useSpringScroll(viewportRef, contentRef, roomId);

  const push = (rid: string, l: Line) => setLines((p) => {
    const cur = p[rid] ?? [];
    if (l.kind === "msg" && cur.some((x) => x.kind === "msg" && x.m.id === l.m.id)) return p;
    return { ...p, [rid]: [...cur, l] };
  });

  useEffect(() => {
    const off = broker.on((e) => {
      if (e.kind === "message") push(e.message.roomId, { kind: "msg", m: e.message });
      else if (e.kind === "notice") push(e.roomId, { kind: "notice", id: broker.newId(), text: e.text });
      else if (e.kind === "activity") setActivity((p) => ({ ...p, [e.handle]: e.activity }));
      else if (e.kind === "paused") setPaused(e.paused);
      else if (e.kind === "members") setMembers((p) => ({ ...p, [e.roomId]: e.members }));
    });
    chan.current = openChannel("delegator-dev", (m) => { if (!broker.isAgent(m.sender.handle)) push(m.roomId, { kind: "msg", m }); });
    return () => { off(); chan.current?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [broker]);

  const roomMembers = members[roomId] ?? [];
  const agentsHere = roomMembers.filter((h) => broker.isAgent(h));
  const view = lines[roomId] ?? [];
  const working = agentsHere.filter((h) => (activity[h] ?? "idle") !== "idle");

  // Notices split the timeline; messages between them are grouped by sender.
  const blocks = useMemo(() => {
    const out: Array<{ kind: "run"; run: ReturnType<typeof groupMessages>[number] } | { kind: "notice"; id: string; text: string }> = [];
    let buf: GroupMessage[] = [];
    const flush = () => { for (const run of groupMessages(buf)) out.push({ kind: "run", run }); buf = []; };
    for (const l of view) { if (l.kind === "msg") buf.push(l.m); else { flush(); out.push(l); } }
    flush();
    return out;
  }, [view]);

  // A new room starts at its latest message; later messages spring into view only if the reader was already there.
  useEffect(() => { scrollToEnd({ instant: true }); }, [roomId, scrollToEnd]);
  useEffect(() => { follow(); }, [blocks, follow]);

  const note = (t: string) => push(roomId, { kind: "notice", id: broker.newId(), text: t });

  const submit = async (line: string) => {
    const c = parseCommand(line);
    if (c) {
      if (c.cmd === "error") return note(c.message);
      if (c.cmd === "stop") { broker.setPaused(true); return note("/stop: room paused. No agent will be woken until you resume."); }
      const handle = c.cmd === "invite" ? c.handle : `${me.handle}/${c.name}`;
      if (!agents.has(handle)) {
        const id = c.cmd === "invite" ? { ...agentIdentity(handle.split("/")[0], handle.split("/")[1] ?? "agent", "remote_agent"), handle } : agentIdentity(me.handle, c.name);
        agents.set(handle, id);
        attachMockAgent(broker, id);
      }
      broker.addMember(roomId, handle);
      return note(`${handle} joined ${roomId}.`);
    }
    const m: GroupMessage = {
      id: broker.newId(), roomId, sender: me, recipients: recipientsFor(line),
      payload: toMcpPayload(line), timestamp: Date.now(), depthCounter: 0,
    };
    chan.current?.post(m);
    await broker.send(m);
  };

  return (
    <div className="dg-shell" data-paused={paused}>
      <aside className="dg-side" aria-label="Rooms and members">
        <h2>Rooms</h2>
        <nav>
          {ROOMS.map((r) => (
            <button key={r.id} type="button" className="dg-room" aria-current={r.id === roomId ? "page" : undefined} onClick={() => setRoomId(r.id)}>
              <span aria-hidden="true">#</span>{r.id.slice(1)}
            </button>
          ))}
        </nav>
        <h2>In this room</h2>
        <ul className="dg-members">
          {roomMembers.map((h) => {
            const isAgent = broker.isAgent(h);
            const a = activity[h] ?? "idle";
            return (
              <li key={h}>
                <span className="dg-dot" data-a={isAgent ? a : "human"} aria-hidden="true" />
                <span className="dg-handle">{h}</span>
                {isAgent && <span className="dg-badge">agent</span>}
                {isAgent && <span className="dg-state" data-a={a}>{LABEL[a]}</span>}
              </li>
            );
          })}
        </ul>
        <label className="dg-as">Send as
          <select value={me.handle} onChange={(e) => setMe(HUMANS.find((h) => h.handle === e.target.value) ?? HUMANS[0])}>
            {HUMANS.map((h) => <option key={h.handle} value={h.handle}>{h.handle}</option>)}
          </select>
        </label>
        <p className="dg-note">Runs in this tab only. The relay keeps no history, and nothing leaves this machine.</p>
      </aside>

      <section className="dg-main" aria-label={roomId}>
        <header className="dg-head">
          <h1>{roomId}</h1>
          <span className="dg-limit">Agents hand off at most {MAX_DEPTH} times before a person must step in.</span>
          <button type="button" className="dg-breaker" aria-pressed={paused} onClick={() => broker.setPaused(!paused)}>
            <span aria-hidden="true">{paused ? "▶" : "⏸"}</span>{paused ? "Resume agents" : "Pause agents"}
          </button>
        </header>
        {paused && <div className="dg-banner" role="status">Paused. Agents will not be woken until you resume.</div>}

        <MessageScroller.Provider autoScroll={false} defaultScrollPosition="end">
          <MessageScroller.Root className="dg-scroll">
            <MessageScroller.Viewport ref={viewportRef} className="dg-viewport" aria-label={`Messages in ${roomId}`}>
              <MessageScroller.Content ref={contentRef} key={roomId} className="dg-content">
                {blocks.length === 0 && <p className="dg-empty">No messages yet. Try <code>@alice/claude-code look at src/auth.ts</code>.</p>}
                {blocks.map((b) => b.kind === "notice"
                  ? <MessageScroller.Item key={b.id} className="dg-notice" role="status">{b.text}</MessageScroller.Item>
                  : (
                    <MessageScroller.Item key={b.run.key} messageId={b.run.key} className="dg-run" data-agent={b.run.sender.type !== "human"}>
                      <Avatar who={b.run.sender} />
                      <div className="dg-col">
                        <div className="dg-meta">
                          <b>{b.run.sender.handle}</b>
                          {b.run.sender.type !== "human" && <span className="dg-badge">agent</span>}
                          <time>{time(b.run.messages[0].timestamp)}</time>
                        </div>
                        {b.run.messages.map((m) => <Body key={m.id} m={m} />)}
                      </div>
                    </MessageScroller.Item>
                  ))}
              </MessageScroller.Content>
            </MessageScroller.Viewport>
            <JumpToLatest onJump={() => scrollToEnd()} />
          </MessageScroller.Root>
        </MessageScroller.Provider>

        <div className="dg-typing" role="status" aria-live="polite">
          {working.length > 0 && <>
            <span className="dg-pulse" aria-hidden="true" />
            {working.map((h) => `${h} ${LABEL[activity[h] ?? "idle"].toLowerCase()}`).join(" · ")}
          </>}
        </div>
        <Composer handles={roomMembers.filter((h) => h !== me.handle)} onSend={(t) => void submit(t)} />
      </section>
    </div>
  );
}
