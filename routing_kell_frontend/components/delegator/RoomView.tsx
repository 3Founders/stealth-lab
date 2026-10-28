"use client";
import { MessageScroller, useMessageScrollerScrollable } from "@shadcn/react/message-scroller";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import Composer from "@/components/delegator/Composer";
import { useSpringScroll } from "@/components/delegator/useSpringScroll";
import { streamChat } from "@/lib/byok/chat";
import { providerById } from "@/lib/byok/providers";
import { recordUse, withinCap, type KeyValue } from "@/lib/byok/spend";
import { Vault, idbStore } from "@/lib/byok/vault";
import { ASSISTANT_HANDLE, assistantIdentity, attachAssistant } from "@/lib/delegator/adapters/assistant";
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
// Neutral labels only: we do not mint handles that carry another company's product name.
const SEED_AGENTS = [agentIdentity("@alice", "agent-1"), agentIdentity("@bob", "agent-2"), agentIdentity("@bob", "agent-3")];
const ROOMS: Room[] = [
  { id: "#auth-refactor-squad", title: "Auth refactor squad", members: ["@alice", "@bob", "@alice/agent-1", "@bob/agent-2"] },
  { id: "#general-agents", title: "General agents", members: ["@alice", "@bob", "@alice/agent-1", "@bob/agent-2", "@bob/agent-3"] },
];
const LABEL: Record<AgentActivity, string> = { idle: "Idle", processing: "Processing", delivering: "Delivering via Relay" };
const SYSTEM = "You are the assistant in a group chat room with several people and agents. Each message is prefixed with its sender's handle. Be concise and helpful.";

type Line = { kind: "msg"; m: GroupMessage } | { kind: "notice"; id: string; text: string };

function makeBroker(withAssistant: boolean): { broker: Broker; agents: Map<string, AgentIdentity> } {
  const broker = new Broker();
  ROOMS.forEach((r) => broker.addRoom({ ...r, members: withAssistant ? [...r.members, ASSISTANT_HANDLE] : r.members }));
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

const memoryKv = (): KeyValue => { const m = new Map<string, string>(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) }; };
function localKv(): KeyValue {
  try { if (typeof window !== "undefined" && window.localStorage) return window.localStorage; } catch { /* blocked */ }
  return memoryKv();
}

/**
 * The dev sandbox: group chat for people and agents. The other agents here are still stand-ins that run in this tab.
 * When a signed-in `subject` is given, each room also has a built-in assistant backed by the person's OWN model key, which
 * lives encrypted in this browser and is sent only to the provider the person chose (see lib/byok).
 * `brand` is the logo shown at the top of the sidebar (the app supplies it, with its own link if it has one).
 */
export default function RoomView({ brand, subject }: { brand?: React.ReactNode; subject?: string }) {
  const world = useRef<ReturnType<typeof makeBroker> | null>(null);
  if (!world.current) world.current = makeBroker(Boolean(subject));
  const { broker, agents } = world.current;

  const [roomId, setRoomId] = useState(ROOMS[0].id);
  const [me, setMe] = useState(HUMANS[0]);
  const [lines, setLines] = useState<Record<string, Line[]>>({});
  const [activity, setActivity] = useState<Record<string, AgentActivity>>({});
  const [members, setMembers] = useState<Record<string, string[]>>(() => Object.fromEntries(ROOMS.map((r) => [r.id, subject ? [...r.members, ASSISTANT_HANDLE] : r.members])));
  const [paused, setPaused] = useState(false);
  const [streaming, setStreaming] = useState<Record<string, string>>({});
  const [hasKey, setHasKey] = useState<boolean | undefined>(undefined);
  const chan = useRef<ReturnType<typeof openChannel> | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const linesRef = useRef(lines);
  linesRef.current = lines;
  const controllers = useRef(new Map<string, AbortController>());
  const { scrollToEnd, follow } = useSpringScroll(viewportRef, contentRef, roomId);
  const vault = useMemo(() => (subject ? new Vault(idbStore(), subject) : null), [subject]);

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

  // The assistant: backed by the person's own key. The key is decrypted per message, used for that one request, and not kept.
  useEffect(() => {
    if (!vault || !subject) return;
    const kv = localKv();
    return attachAssistant(broker, {
      resolve: async () => {
        const metas = await vault.list();
        const meta = metas.find((k) => k.model) ?? metas[0];
        if (!meta) return null;
        if (!meta.model) throw new Error("Open Models in your account menu and press Test on your key to choose a model.");
        const provider = providerById(meta.provider);
        const apiKey = await vault.reveal(meta.id);
        if (!provider || !apiKey) throw new Error("That key can't be read in this browser any more. Add it again under Models.");
        return {
          secrets: [apiKey],
          stream: (messages, signal) => streamChat({ provider, baseUrl: meta.baseUrl, apiKey }, meta.model, messages, { system: SYSTEM, signal }),
        };
      },
      allowed: () => withinCap(kv, subject),
      used: () => void recordUse(kv, subject),
      history: (rid) => (linesRef.current[rid] ?? []).flatMap((l) => (l.kind === "msg" ? [l.m] : [])),
      onStart: (rid, c) => { controllers.current.set(rid, c); setStreaming((p) => ({ ...p, [rid]: "" })); },
      onDelta: (rid, text) => setStreaming((p) => ({ ...p, [rid]: text })),
      onEnd: (rid) => { controllers.current.delete(rid); setStreaming((p) => { const n = { ...p }; delete n[rid]; return n; }); },
    });
  }, [broker, vault, subject]);

  useEffect(() => {
    if (!vault) return;
    let live = true;
    vault.list().then((k) => { if (live) setHasKey(k.length > 0); }).catch(() => { if (live) setHasKey(false); });
    return () => { live = false; };
  }, [vault, roomId]);

  const roomMembers = members[roomId] ?? [];
  const agentsHere = roomMembers.filter((h) => broker.isAgent(h));
  const view = lines[roomId] ?? [];
  const working = agentsHere.filter((h) => (activity[h] ?? "idle") !== "idle");
  const liveText = streaming[roomId];

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
  useEffect(() => { follow(); }, [blocks, liveText, follow]);

  const note = (t: string) => push(roomId, { kind: "notice", id: broker.newId(), text: t });

  const submit = async (line: string) => {
    const c = parseCommand(line);
    if (c) {
      if (c.cmd === "error") return note(c.message);
      if (c.cmd === "stop") {
        broker.setPaused(true);
        controllers.current.forEach((ctl) => ctl.abort());
        return note("/stop: room paused. No agent will be woken until you resume.");
      }
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
        {brand && <div className="dg-brand">{brand}</div>}
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
                {h === ASSISTANT_HANDLE && hasKey === false
                  ? <Link className="dg-state dg-addkey" href="/account/models">Add a key to chat here</Link>
                  : isAgent && <span className="dg-state" data-a={a}>{LABEL[a]}</span>}
              </li>
            );
          })}
        </ul>
        <label className="dg-as">Send as
          <select value={me.handle} onChange={(e) => setMe(HUMANS.find((h) => h.handle === e.target.value) ?? HUMANS[0])}>
            {HUMANS.map((h) => <option key={h.handle} value={h.handle}>{h.handle}</option>)}
          </select>
        </label>
        <p className="dg-note">
          {subject
            ? "The other agents here run in this tab. The assistant uses your own model key, which stays in this browser and is sent only to your provider."
            : "Runs in this tab only. The relay keeps no history, and nothing leaves this machine."}
        </p>
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
            <MessageScroller.Viewport ref={viewportRef} data-lenis-prevent className="dg-viewport" aria-label={`Messages in ${roomId}`}>
              <MessageScroller.Content ref={contentRef} key={roomId} className="dg-content">
                {blocks.length === 0 && liveText === undefined && (
                  <p className="dg-empty">No messages yet. Try <code>@alice/agent-1 look at src/auth.ts</code>{subject ? <> or <code>@assistant hello</code></> : null}.</p>
                )}
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
                {liveText !== undefined && (
                  <MessageScroller.Item key="streaming" className="dg-run" data-agent="true">
                    <Avatar who={assistantIdentity} />
                    <div className="dg-col">
                      <div className="dg-meta"><b>{ASSISTANT_HANDLE}</b><span className="dg-badge">agent</span></div>
                      <div className="dg-body"><p>{liveText || "…"}</p></div>
                    </div>
                  </MessageScroller.Item>
                )}
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
          {liveText !== undefined && (
            <button type="button" className="dg-stop" onClick={() => controllers.current.get(roomId)?.abort()}>Stop</button>
          )}
        </div>
        <Composer handles={roomMembers.filter((h) => h !== me.handle)} onSend={(t) => void submit(t)} />
      </section>
    </div>
  );
}
