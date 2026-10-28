"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import AgentStatus from "@/components/delegator/AgentStatus";
import { agentIdentity, parseCommand, recipientsFor, toMcpPayload } from "@/lib/delegator/adapters/mcp";
import { attachMockAgent } from "@/lib/delegator/adapters/mock";
import { Broker } from "@/lib/delegator/relay/broker";
import { openChannel } from "@/lib/delegator/relay/channel";
import type { AgentActivity, AgentIdentity, GroupMessage, Room } from "@/lib/delegator/types";
import { MAX_DEPTH } from "@/lib/delegator/types";

const HUMANS: AgentIdentity[] = [
  { handle: "@alice", displayName: "Alice", type: "human", status: "online" },
  { handle: "@bob", displayName: "Bob", type: "human", status: "online" },
];
const SEED_AGENTS = [agentIdentity("@alice", "claude-code"), agentIdentity("@bob", "opencode"), agentIdentity("@bob", "cursor")];
const ROOMS: Room[] = [
  { id: "#auth-refactor-squad", title: "Auth refactor squad", members: ["@alice", "@bob", "@alice/claude-code", "@bob/opencode"] },
  { id: "#general-agents", title: "General agents", members: ["@alice", "@bob", "@alice/claude-code", "@bob/opencode", "@bob/cursor"] },
];

type Line = { kind: "msg"; m: GroupMessage } | { kind: "notice"; id: string; text: string };

function makeBroker(): { broker: Broker; agents: Map<string, AgentIdentity> } {
  const broker = new Broker();
  ROOMS.forEach((r) => broker.addRoom(r));
  const agents = new Map<string, AgentIdentity>();
  SEED_AGENTS.forEach((a) => { agents.set(a.handle, a); attachMockAgent(broker, a); });
  return { broker, agents };
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
  const [text, setText] = useState("");
  const [raw, setRaw] = useState(false);
  const chan = useRef<ReturnType<typeof openChannel> | null>(null);

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

  const note = (t: string) => push(roomId, { kind: "notice", id: broker.newId(), text: t });

  const submit = async () => {
    const line = text.trim();
    if (!line) return;
    setText("");
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

  const token = /(^|\s)(@[\w/.-]*)$/.exec(text)?.[2];
  const suggestions = useMemo(
    () => (token === undefined ? [] : roomMembers.filter((h) => h.toLowerCase().startsWith(token.toLowerCase()) && h !== me.handle).slice(0, 6)),
    [token, roomMembers, me.handle],
  );
  const accept = (h: string) => setText((t) => t.replace(/@[\w/.-]*$/, `${h} `));

  return (
    <div className="panel" aria-label="Delegator sandbox">
      <p className="notice">
        <b style={{ fontWeight: 400 }}>Delegator (dev sandbox).</b> Group chat for people and agents. The relay lives in this tab&rsquo;s memory, holds no history, and stops waking agents after {MAX_DEPTH} agent-to-agent hops. Nothing leaves this browser.
      </p>

      <div className="cluster">
        <label>Room{" "}
          <select value={roomId} onChange={(e) => setRoomId(e.target.value)}>{ROOMS.map((r) => <option key={r.id} value={r.id}>{r.id}</option>)}</select>
        </label>
        <label>Send as{" "}
          <select value={me.handle} onChange={(e) => setMe(HUMANS.find((h) => h.handle === e.target.value) ?? HUMANS[0])}>
            {HUMANS.map((h) => <option key={h.handle} value={h.handle}>{h.handle}</option>)}
          </select>
        </label>
        <button type="button" className="btn-line" aria-pressed={paused} onClick={() => broker.setPaused(!paused)}>
          {paused ? "Resume room executions" : "Pause room executions"}
        </button>
        <button type="button" className="btn-line" aria-pressed={raw} onClick={() => setRaw((r) => !r)}>{raw ? "Show Markdown" : "Show agent payload"}</button>
        {paused && <span className="status" data-s="failed" role="status">Paused</span>}
      </div>

      <div className="stack" aria-label="Agents in this room">
        {agentsHere.map((h) => <AgentStatus key={h} handle={h} activity={activity[h] ?? "idle"} />)}
      </div>

      <div className="stack" role="log" aria-label={`Messages in ${roomId}`} aria-live="polite">
        {view.length === 0 && <p className="small dim">No messages yet. Try <code>@alice/claude-code look at src/auth.ts</code>.</p>}
        {view.map((l) => l.kind === "notice"
          ? <p key={l.id} className="notice" role="status">{l.text}</p>
          : (
            <article className="pane" key={l.m.id} style={{ minHeight: 0 }}>
              <header>
                <span>{l.m.sender.handle}</span>
                <span className="dim small">to {l.m.recipients.join(", ")} &middot; hop {l.m.depthCounter} &middot; {new Date(l.m.timestamp).toLocaleTimeString()}</span>
              </header>
              <div className="out" style={{ minHeight: 0 }}>
                {raw ? <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(l.m.payload.structuredPayload ?? { text: l.m.payload.text }, null, 2)}</pre> : l.m.payload.text}
              </div>
            </article>
          ))}
      </div>

      <form className="stack" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {suggestions.length > 0 && (
          <ul role="listbox" aria-label="Handles" className="cluster" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {suggestions.map((h) => <li key={h} role="option" aria-selected={false}><button type="button" className="btn-line" onClick={() => accept(h)}>{h}</button></li>)}
          </ul>
        )}
        <div className="cluster">
          <input aria-label="Message" style={{ flex: 1, minWidth: 220 }} value={text} onChange={(e) => setText(e.target.value)}
                 placeholder="Message, @mention, /add-agent --name=x, /invite @bob/cursor, /stop" />
          <button type="submit" className="btn-ink" disabled={!text.trim()}>Send</button>
        </div>
      </form>
    </div>
  );
}
