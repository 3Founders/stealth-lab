"use client";
import { MessageScroller, useMessageScrollerScrollable } from "@shadcn/react/message-scroller";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Composer from "@/components/delegator/Composer";
import { useSpringScroll } from "@/components/delegator/useSpringScroll";
import { recordUse, withinCap, type KeyValue } from "@/lib/byok/spend";
import { Vault, idbStore } from "@/lib/byok/vault";
import { HUMAN, MAX_AGENTS, newAgentHandle } from "@/lib/delegator/agents";
import { assistantIdentityFor, attachAssistant, type AssistantDeps } from "@/lib/delegator/adapters/assistant";
import { mentionsIn, parseCommand, recipientsFor, toMcpPayload } from "@/lib/delegator/adapters/mcp";
import { describeBacking, makeResolver, type Backing } from "@/lib/delegator/adapters/resolve";
import { Broker } from "@/lib/delegator/relay/broker";
import { cursorOf, isFinished, mentionedNames, mergeTasks, relay, validAgentName, type RelayAgent, type RelayTask } from "@/lib/delegator/relay-client";
import { newRoomName, readRooms, withoutRoom, writeRooms, type RoomList } from "@/lib/delegator/rooms";
import type { AgentActivity, AgentIdentity, GroupMessage } from "@/lib/delegator/types";
import { MAX_DEPTH } from "@/lib/delegator/types";
import { groupMessages, initials } from "@/lib/delegator/ui";
import { getAccessToken } from "@/lib/session";

const LABEL: Record<AgentActivity, string> = { idle: "Idle", processing: "Working", delivering: "Sending" };
const STATUS: Record<RelayTask["status"], string> = {
  needs_approval: "Waiting for your approval", queued: "Waiting for the agent", claimed: "Working", done: "Done", failed: "Failed", cancelled: "Cancelled",
};

type Line = { kind: "msg"; m: GroupMessage } | { kind: "notice"; id: string; text: string; t: number };

const time = (t: number) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function Avatar({ who }: { who: AgentIdentity }) {
  return <span className="dg-avatar" data-kind={who.type === "human" ? "human" : "agent"} aria-hidden="true">{initials(who.handle)}</span>;
}

function Body({ m }: { m: GroupMessage }) {
  return <div className="dg-body"><p>{m.payload.text}</p></div>;
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

type Item = { t: number; key: string; node: React.ReactNode };

/**
 * Your rooms and agents. Two kinds of agent, both real:
 *  - an agent that runs in this tab: a model call with the key or keळ models you chose under Models. It works only while this
 *    page is open;
 *  - your own agent on keळ's relay (docs/delegate_relay_api.md): it lives on the server, tied to your account, and keeps taking
 *    and answering tasks when this page is closed. It reads its inbox with the `delegate_inbox` tool.
 * Rooms are labels you create and remove. There are no pretend people, rooms or agents here. `subject` is the signed-in account.
 */
export default function RoomView({ brand, back, subject }: { brand?: React.ReactNode; back?: React.ReactNode; subject: string }) {
  const broker = useMemo(() => new Broker(), []);
  const vault = useMemo(() => new Vault(idbStore(), subject), [subject]);
  const kv = useMemo(localKv, []);
  const api = useMemo(() => relay({ token: getAccessToken }), []);

  const [roomList, setRoomList] = useState<RoomList>(() => readRooms(kv, subject));
  const room = roomList.current;
  const roomRef = useRef(room);
  roomRef.current = room;

  const [agents, setAgents] = useState<string[]>([]); // agents that run in this tab
  const agentsRef = useRef<string[]>([]);
  const [, bump] = useState(0);
  const lineStore = useRef<Record<string, Line[]>>({});
  const [activity, setActivity] = useState<Record<string, AgentActivity>>({});
  const [streaming, setStreaming] = useState<Record<string, { room: string; text: string }>>({});
  const [paused, setPaused] = useState(false);
  const [backing, setBacking] = useState<Backing | undefined>(undefined);
  const [menuOpen, setMenuOpen] = useState(false); // phones: the sidebar is a drawer
  const controllers = useRef(new Map<string, AbortController>());
  const detachers = useRef(new Map<string, () => void>());
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const { scrollToEnd, follow } = useSpringScroll(viewportRef, contentRef, room);

  // relay state
  const [relayAgents, setRelayAgents] = useState<RelayAgent[]>([]);
  const [tasksByRoom, setTasksByRoom] = useState<Record<string, RelayTask[]>>({});
  const [relayError, setRelayError] = useState("");
  const tasksRef = useRef(tasksByRoom);
  tasksRef.current = tasksByRoom;

  // sidebar forms
  const [newRoom, setNewRoom] = useState<string | null>(null); // null = closed
  const [adding, setAdding] = useState(false);
  const [addKind, setAddKind] = useState<"mine" | "tab">("mine");
  const [addName, setAddName] = useState("");
  const [formError, setFormError] = useState("");
  const [justAdded, setJustAdded] = useState("");

  const push = useCallback((r: string, l: Line) => {
    const cur = lineStore.current[r] ?? [];
    if (l.kind === "msg" && cur.some((x) => x.kind === "msg" && x.m.id === l.m.id)) return;
    lineStore.current = { ...lineStore.current, [r]: [...cur, l] }; // read straight away by the agents' history, before any re-render
    bump((n) => n + 1);
  }, []);
  const note = useCallback((text: string, r: string = roomRef.current) => push(r, { kind: "notice", id: broker.newId(), text, t: Date.now() }), [broker, push]);

  const deps = useMemo<AssistantDeps>(() => ({
    resolve: makeResolver({ vault, token: getAccessToken, kv, subject }),
    allowed: () => withinCap(kv, subject),
    used: () => void recordUse(kv, subject),
    history: (r) => (lineStore.current[r] ?? []).flatMap((l) => (l.kind === "msg" ? [l.m] : [])),
    onStart: (r, handle, c) => { controllers.current.set(handle, c); setStreaming((p) => ({ ...p, [handle]: { room: r, text: "" } })); },
    onDelta: (r, handle, text) => setStreaming((p) => ({ ...p, [handle]: { room: r, text } })),
    onEnd: (_r, handle) => { controllers.current.delete(handle); setStreaming((p) => { const n = { ...p }; delete n[handle]; return n; }); },
  }), [vault, kv, subject]);

  // Every room you have exists in the in-tab relay with the in-tab agents as members.
  useEffect(() => {
    for (const r of roomList.rooms) if (!broker.room(r)) broker.addRoom({ id: r, title: r, members: [HUMAN.handle, ...agentsRef.current] });
    writeRooms(kv, subject, roomList);
  }, [broker, kv, subject, roomList]);

  const addAgent = (handle: string) => {
    for (const r of roomList.rooms) broker.addMember(r, handle);
    detachers.current.set(handle, attachAssistant(broker, deps, handle));
    agentsRef.current = [...agentsRef.current, handle];
    setAgents(agentsRef.current);
  };

  const removeAgent = (handle: string) => {
    controllers.current.get(handle)?.abort();
    detachers.current.get(handle)?.();
    detachers.current.delete(handle);
    for (const r of roomList.rooms) broker.removeMember(r, handle);
    agentsRef.current = agentsRef.current.filter((h) => h !== handle);
    setAgents(agentsRef.current);
    setActivity((p) => { const n = { ...p }; delete n[handle]; return n; });
    note(`${handle} left the room.`);
  };

  useEffect(() => {
    const off = broker.on((e) => {
      if (e.kind === "message") push(e.message.roomId, { kind: "msg", m: e.message });
      else if (e.kind === "notice") push(e.roomId, { kind: "notice", id: broker.newId(), text: e.text, t: Date.now() });
      else if (e.kind === "activity") setActivity((p) => ({ ...p, [e.handle]: e.activity }));
      else if (e.kind === "paused") setPaused(e.paused);
    });
    return () => { off(); };
  }, [broker, push]);

  // The room starts with one in-tab agent. (A ref guards the development double-run of effects.)
  const started = useRef(false);
  useEffect(() => {
    if (!started.current) { started.current = true; addAgent("@assistant-1"); }
    const all = detachers.current;
    return () => { all.forEach((d) => d()); all.clear(); started.current = false; agentsRef.current = []; setAgents([]); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [broker, deps]);

  useEffect(() => {
    let live = true;
    describeBacking({ vault, kv, subject }).then((b) => { if (live) setBacking(b); }).catch(() => { if (live) setBacking({ ready: false, text: "Add a key to chat here" }); });
    return () => { live = false; };
  }, [vault, kv, subject]);

  // The relay: your agents and this room's tasks, fetched now and again every few seconds while the page is visible.
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      if (!live) return;
      let busy = false;
      if (typeof document === "undefined" || document.visibilityState !== "hidden") {
        try {
          const have = tasksRef.current[room] ?? [];
          const [a, t] = await Promise.all([api.agents(), api.tasks(room, cursorOf(have))]);
          if (!live) return;
          setRelayAgents(a);
          if (t.length) setTasksByRoom((p) => ({ ...p, [room]: mergeTasks(p[room] ?? [], t) }));
          setRelayError("");
          busy = [...have, ...t].some((x) => !isFinished(x));
        } catch (err) {
          if (!live) return;
          setRelayError(err instanceof Error ? err.message : "The agent relay is unavailable.");
        }
      }
      if (live) timer = setTimeout(tick, busy ? 2500 : 7000);
    };
    void tick();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [api, room]);

  const tasks = tasksByRoom[room] ?? [];
  const lines = lineStore.current[room] ?? [];

  async function act(id: string, what: "approve" | "cancel") {
    try {
      const t = await (what === "approve" ? api.approve(id) : api.cancel(id));
      setTasksByRoom((p) => ({ ...p, [t.room]: mergeTasks(p[t.room] ?? [], [t]) }));
    } catch (err) { note(err instanceof Error ? err.message : "That didn't work."); }
  }

  const items: Item[] = (() => {
    const out: Item[] = [];
    let buf: GroupMessage[] = [];
    const flush = () => {
      for (const run of groupMessages(buf)) {
        out.push({
          t: run.messages[0].timestamp, key: run.key,
          node: (
            <MessageScroller.Item key={run.key} messageId={run.key} className="dg-run" data-agent={run.sender.type !== "human"}>
              <Avatar who={run.sender} />
              <div className="dg-col">
                <div className="dg-meta">
                  <b>{run.sender.handle}</b>
                  {run.sender.type !== "human" && <span className="dg-badge">agent</span>}
                  <time>{time(run.messages[0].timestamp)}</time>
                </div>
                {run.messages.map((m) => <Body key={m.id} m={m} />)}
              </div>
            </MessageScroller.Item>
          ),
        });
      }
      buf = [];
    };
    for (const l of lines) {
      if (l.kind === "msg") buf.push(l.m);
      else { flush(); out.push({ t: l.t, key: l.id, node: <MessageScroller.Item key={l.id} className="dg-notice" role="status">{l.text}</MessageScroller.Item> }); }
    }
    flush();
    for (const task of tasks) {
      const ag = relayAgents.find((a) => a.id === task.agent_id);
      const handle = ag?.handle ?? "@agent";
      const t = Date.parse(task.created_at);
      out.push({
        t, key: `task-${task.id}`,
        node: (
          <MessageScroller.Item key={`task-${task.id}`} className="dg-task" data-status={task.status}>
            <div className="dg-meta">
              <b>@you</b><span aria-hidden="true">→</span><b>{handle}</b>
              <span className="dg-status" data-status={task.status}>{STATUS[task.status]}</span>
              <time>{time(t)}</time>
            </div>
            <div className="dg-body"><p>{task.text}</p></div>
            {task.status === "queued" && ag && !ag.online && <p className="dg-hint">{handle} is offline. It will pick this up the next time it checks its inbox.</p>}
            {task.result && (
              <div className="dg-result">
                <div className="dg-meta"><b>{handle}</b><span className="dg-badge">agent</span></div>
                <div className="dg-body"><p>{task.result}</p></div>
              </div>
            )}
            {!isFinished(task) && (
              <div className="dg-task-actions">
                {task.status === "needs_approval" && <button type="button" className="dg-mini" onClick={() => void act(task.id, "approve")}>Approve</button>}
                <button type="button" className="dg-mini" onClick={() => void act(task.id, "cancel")}>Cancel</button>
              </div>
            )}
          </MessageScroller.Item>
        ),
      });
    }
    return out.sort((a, b) => a.t - b.t);
  })();

  const streamingHere = Object.entries(streaming).filter(([, v]) => v.room === room);
  const liveText = JSON.stringify(streaming);
  useEffect(() => { scrollToEnd({ instant: true }); }, [scrollToEnd, room]);
  const itemKeys = items.map((i) => i.key).join("|");
  useEffect(() => { follow(); }, [itemKeys, tasks, liveText, follow]);

  // The shell is as tall as what is actually visible (a phone's on-screen keyboard covers part of the layout viewport).
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const fit = () => { document.documentElement.style.setProperty("--dg-h", `${Math.round(vv.height)}px`); if (vv.offsetTop) window.scrollTo(0, 0); };
    fit();
    vv.addEventListener("resize", fit);
    vv.addEventListener("scroll", fit);
    return () => { vv.removeEventListener("resize", fit); vv.removeEventListener("scroll", fit); document.documentElement.style.removeProperty("--dg-h"); };
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  // ---- rooms
  const chooseRoom = (r: string) => { setRoomList((l) => ({ ...l, current: r })); setMenuOpen(false); };
  const createRoom = (e: React.FormEvent) => {
    e.preventDefault();
    const r = newRoomName(roomList.rooms, newRoom ?? "");
    if ("error" in r) return setFormError(r.error);
    setFormError(""); setNewRoom(null);
    setRoomList({ rooms: [...roomList.rooms, r.name], current: r.name });
  };
  const deleteRoom = (r: string) => {
    const next = withoutRoom(roomList, r);
    if (next !== roomList) setRoomList(next);
  };

  // ---- agents
  const submitAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    if (addKind === "tab") {
      const r = newAgentHandle(agents, addName.trim() || undefined);
      if ("error" in r) return setFormError(r.error);
      addAgent(r.handle);
      note(`${r.handle} joined the room.`);
      setAdding(false); setAddName(""); return;
    }
    const v = validAgentName(addName);
    if ("error" in v) return setFormError(v.error);
    try {
      const a = await api.addAgent(v.name);
      setRelayAgents((p) => [...p.filter((x) => x.id !== a.id), a]);
      setJustAdded(a.name);
      setAdding(false); setAddName("");
    } catch (err) { setFormError(err instanceof Error ? err.message : "That didn't work."); }
  };
  const removeRelayAgent = async (a: RelayAgent) => {
    try {
      const r = await api.removeAgent(a.id);
      setRelayAgents((p) => p.filter((x) => x.id !== a.id));
      note(`${a.handle} was removed${r.cancelled ? ` and ${r.cancelled} unfinished task${r.cancelled === 1 ? "" : "s"} cancelled` : ""}.`);
      if (justAdded === a.name) setJustAdded("");
    } catch (err) { note(err instanceof Error ? err.message : "That didn't work."); }
  };

  const submit = async (line: string) => {
    const c = parseCommand(line);
    if (c) {
      if (c.cmd === "error") return note(c.message);
      if (c.cmd === "stop") {
        broker.setPaused(true);
        controllers.current.forEach((ctl) => ctl.abort());
        return note("/stop: paused. No agent in this tab will be woken until you resume.");
      }
      const r = newAgentHandle(agents, c.name);
      if ("error" in r) return note(r.error);
      addAgent(r.handle);
      return note(`${r.handle} joined the room.`);
    }
    const names = new Set(relayAgents.map((a) => a.name));
    const toRelay = mentionedNames(line).filter((n) => names.has(n));
    const localMentions = mentionsIn(line).filter((h) => agents.includes(h));
    if (toRelay.length) {
      try {
        const r = await api.send(line, room);
        setTasksByRoom((p) => ({ ...p, [room]: mergeTasks(p[room] ?? [], r.tasks) }));
      } catch (err) { note(err instanceof Error ? err.message : "That didn't send."); }
      if (!localMentions.length) return;
    } else {
      const unknown = mentionedNames(line).filter((n) => !agents.includes(`@${n}`) && !names.has(n));
      if (unknown.length && !localMentions.length) note(`No agent named ${unknown.map((n) => `@${n}`).join(", ")}. Add one with + Add agent.`);
    }
    const m: GroupMessage = {
      id: broker.newId(), roomId: room, sender: HUMAN, recipients: toRelay.length ? localMentions : recipientsFor(line),
      payload: toMcpPayload(line), timestamp: Date.now(), depthCounter: 0,
    };
    await broker.send(m);
  };

  const working = agents.filter((h) => (activity[h] ?? "idle") !== "idle" || streaming[h] !== undefined);
  const mentionable = [...agents, ...relayAgents.map((a) => a.handle)];
  const full = agents.length >= MAX_AGENTS;
  const count = agents.length + relayAgents.length;

  return (
    <div className="dg-shell" data-paused={paused} data-menu={menuOpen ? "open" : "closed"}>
      <div className="dg-scrim" onClick={() => setMenuOpen(false)} aria-hidden="true" />
      <aside id="dg-side" className="dg-side" aria-label="Your rooms and agents">
        <button type="button" className="dg-close" onClick={() => setMenuOpen(false)} aria-label="Close the room list">×</button>
        {brand && <div className="dg-brand">{brand}</div>}
        {back && <div className="dg-backrow">{back}</div>}

        <h2>Rooms</h2>
        <ul className="dg-rooms">
          {roomList.rooms.map((r) => (
            <li key={r}>
              <button type="button" className="dg-room" aria-current={r === room ? "page" : undefined} onClick={() => chooseRoom(r)}><span aria-hidden="true">#</span>{r.slice(1)}</button>
              {roomList.rooms.length > 1 && <button type="button" className="dg-x" onClick={() => deleteRoom(r)} aria-label={`Remove room ${r}`} title={`Remove ${r}`}>×</button>}
            </li>
          ))}
        </ul>
        {newRoom === null
          ? <button type="button" className="btn-line dg-add" onClick={() => { setNewRoom(""); setFormError(""); }}>+ New room</button>
          : (
            <form className="dg-form" onSubmit={createRoom}>
              <label htmlFor="dg-room-name">Room name</label>
              <input id="dg-room-name" autoFocus value={newRoom} onChange={(e) => setNewRoom(e.target.value)} placeholder="design-review" maxLength={30} autoComplete="off" />
              {formError && <p className="dg-err" role="alert">{formError}</p>}
              <div className="dg-form-row"><button type="submit" className="btn-ink dg-mini-btn"><span>Create</span></button><button type="button" className="btn-line dg-mini-btn" onClick={() => { setNewRoom(null); setFormError(""); }}>Cancel</button></div>
            </form>
          )}

        <h2>Agents</h2>
        <ul className="dg-members">
          <li>
            <span className="dg-dot" data-a="human" aria-hidden="true" />
            <span className="dg-handle">{HUMAN.handle}</span>
          </li>
          {relayAgents.map((a) => (
            <li key={a.id}>
              <span className="dg-dot" data-a={a.online ? "human" : "idle"} aria-hidden="true" />
              <span className="dg-handle">{a.handle}</span>
              <span className="dg-badge">agent</span>
              <span className="dg-state" data-a="idle">{a.online ? "Online · keeps running when this page is closed" : "Offline · keeps its tasks until it checks in"}</span>
              <button type="button" className="dg-remove" onClick={() => void removeRelayAgent(a)} aria-label={`Remove ${a.handle}`} title={`Remove ${a.handle}`}>×</button>
            </li>
          ))}
          {agents.map((h) => {
            const a = streaming[h] !== undefined ? "processing" : (activity[h] ?? "idle");
            return (
              <li key={h}>
                <span className="dg-dot" data-a={a} aria-hidden="true" />
                <span className="dg-handle">{h}</span>
                <span className="dg-badge" data-kind="tab">in this tab</span>
                {backing && !backing.ready
                  ? <Link className="dg-state dg-addkey" href="/account/models">{backing.text}</Link>
                  : <span className="dg-state" data-a={a}>{a === "idle" ? (backing?.text ?? "") : LABEL[a]}</span>}
                <button type="button" className="dg-remove" onClick={() => removeAgent(h)} aria-label={`Remove ${h} from the room`} title={`Remove ${h}`}>×</button>
              </li>
            );
          })}
        </ul>
        {relayError && <p className="dg-err" role="status">{relayError}</p>}

        {!adding
          ? <button type="button" className="btn-line dg-add" onClick={() => { setAdding(true); setFormError(""); }}>+ Add agent</button>
          : (
            <form className="dg-form" onSubmit={(e) => void submitAgent(e)}>
              <fieldset>
                <legend>What kind of agent?</legend>
                <label><input type="radio" name="dg-kind" checked={addKind === "mine"} onChange={() => setAddKind("mine")} /> My own agent. It runs on keळ&rsquo;s relay and keeps working when this page is closed.</label>
                <label><input type="radio" name="dg-kind" checked={addKind === "tab"} onChange={() => setAddKind("tab")} disabled={full} /> A model in this tab. It stops when you close the page.</label>
              </fieldset>
              <label htmlFor="dg-agent-name">Name{addKind === "tab" ? " (optional)" : ""}</label>
              <input id="dg-agent-name" autoFocus value={addName} onChange={(e) => setAddName(e.target.value)} placeholder={addKind === "mine" ? "web-chat" : "reviewer"} maxLength={40} autoComplete="off" />
              {formError && <p className="dg-err" role="alert">{formError}</p>}
              <div className="dg-form-row"><button type="submit" className="btn-ink dg-mini-btn"><span>Add</span></button><button type="button" className="btn-line dg-mini-btn" onClick={() => { setAdding(false); setFormError(""); }}>Cancel</button></div>
            </form>
          )}
        {justAdded && (
          <p className="dg-note dg-connect" role="status">
            <b>@{justAdded} is added.</b> To connect it, open your agent (Claude Code, Cursor, …), connect the keळ server from <Link href="/connect" style={{ textDecoration: "underline" }}>Connect</Link>, and ask it to check its keळ inbox as <code>{justAdded}</code> (it calls <code>delegate_inbox</code>).
          </p>
        )}
        <p className="dg-note">
          Agents on the relay stay on your account and keep working with this page closed. Agents in this tab use the model you chose under <Link href="/account/models" style={{ textDecoration: "underline" }}>Models</Link>{backing?.text.startsWith("keळ") ? " (public material only)" : " with your own key"}.
        </p>
      </aside>

      <section className="dg-main" aria-label={`Room ${room}`}>
        <header className="dg-head">
          <button type="button" className="dg-menu" onClick={() => setMenuOpen(true)} aria-expanded={menuOpen} aria-controls="dg-side" aria-label="Open rooms and agents">
            <span aria-hidden="true">☰</span> <span>{count} agent{count === 1 ? "" : "s"}</span>
          </button>
          <h1>{room}</h1>
          <span className="dg-limit">In-tab agents hand off at most {MAX_DEPTH} times before a person must step in.</span>
          <button type="button" className="dg-breaker" aria-pressed={paused} onClick={() => broker.setPaused(!paused)}>
            <span aria-hidden="true">{paused ? "▶" : "⏸"}</span>{paused ? "Resume agents" : "Pause agents"}
          </button>
        </header>
        {paused && <div className="dg-banner" role="status">Paused. Agents in this tab will not be woken until you resume.</div>}

        <MessageScroller.Provider autoScroll={false} defaultScrollPosition="end">
          <MessageScroller.Root className="dg-scroll">
            <MessageScroller.Viewport ref={viewportRef} data-lenis-prevent className="dg-viewport" aria-label={`Messages in ${room}`}>
              <MessageScroller.Content ref={contentRef} className="dg-content">
                {items.length === 0 && streamingHere.length === 0 && (
                  <p className="dg-empty">No messages in {room} yet. Say hello: <code>@assistant-1 hello</code>. Add your own agents with <b>+ Add agent</b>.</p>
                )}
                {items.map((i) => i.node)}
                {streamingHere.map(([h, v]) => (
                  <MessageScroller.Item key={`live-${h}`} className="dg-run" data-agent="true">
                    <Avatar who={assistantIdentityFor(h)} />
                    <div className="dg-col">
                      <div className="dg-meta"><b>{h}</b><span className="dg-badge">agent</span></div>
                      <div className="dg-body"><p>{v.text || "…"}</p></div>
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
            {working.map((h) => `${h} is working`).join(" · ")}
          </>}
          {streamingHere.length > 0 && (
            <button type="button" className="dg-stop" onClick={() => controllers.current.forEach((c) => c.abort())}>Stop</button>
          )}
        </div>
        <Composer handles={mentionable} onSend={(t) => void submit(t)} />
      </section>
    </div>
  );
}
