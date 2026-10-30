"use client";
import { MessageScroller, useMessageScrollerScrollable } from "@shadcn/react/message-scroller";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Composer from "@/components/chat/Composer";
import SmoothText from "@/components/chat/SmoothText";
import { useSpringScroll } from "@/components/chat/useSpringScroll";
import { recordUse, withinCap, type KeyValue } from "@/lib/byok/spend";
import { Vault, idbStore } from "@/lib/byok/vault";
import { HUMAN, MAX_AGENTS, newAgentHandle } from "@/lib/chat/agents";
import { assistantIdentityFor, attachAssistant, type AssistantDeps } from "@/lib/chat/adapters/assistant";
import { mentionsIn, parseCommand, recipientsFor, toMcpPayload } from "@/lib/chat/adapters/mcp";
import { describeBacking, makeResolver, type Backing } from "@/lib/chat/adapters/resolve";
import { Broker } from "@/lib/chat/relay/broker";
import { cursorOf, isFinished, mentionedNames, mergeTasks, relay, validAgentName, type RelayAgent, type RelayTask } from "@/lib/chat/relay-client";
import { DEFAULT_ROOM, newRoomName, readAgents, readRooms, withoutRoom, writeAgents, writeRooms, type RoomList } from "@/lib/chat/rooms";
import { roomsApi, type RoomMember } from "@/lib/chat/rooms-api";
import { SyncEngine, type RoomsChange, type SyncState } from "@/lib/chat/sync";
import { HISTORY_DB, HistoryStore } from "@/lib/chat/store";
import type { AgentActivity, AgentIdentity, GroupMessage } from "@/lib/chat/types";
import { MAX_DEPTH } from "@/lib/chat/types";
import { groupMessages, initials } from "@/lib/chat/ui";
import { getAccessToken } from "@/lib/session";
import NotificationBell from "@/components/chat/NotificationBell";
import PersonAvatar from "@/components/chat/PersonAvatar";
import { fallbackName } from "@/lib/chat/events";
import { DEFAULT_PREFS, chime, decide, loadPrefs, previewText, savePrefs, setBadge, showOs, titleWithCount, type Note, type NoteKind, type Prefs } from "@/lib/chat/notify";

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

const lineTime = (l: Line) => (l.kind === "msg" ? l.m.timestamp : l.t);

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
  const history = useMemo(() => new HistoryStore(idbStore(HISTORY_DB), subject), [subject]);
  const historyWarned = useRef(false);
  const rapi = useMemo(() => roomsApi({ token: getAccessToken }), []);
  const engineRef = useRef<SyncEngine | null>(null);
  const [sync, setSync] = useState<SyncState>({ status: "idle", pending: 0, lastSyncAt: null, error: null });
  const [info, setInfo] = useState<RoomsChange["info"]>({});
  const infoRef = useRef(info);
  infoRef.current = info;
  const [members, setMembers] = useState<RoomMember[]>([]);
  const [invite, setInvite] = useState<{ url: string; days: number; uses: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const [peopleError, setPeopleError] = useState("");

  const [roomList, setRoomList] = useState<RoomList>(() => readRooms(kv, subject));
  const room = roomList.current;
  const roomRef = useRef(room);
  roomRef.current = room;
  const roomListRef = useRef(roomList);
  roomListRef.current = roomList;

  const [agents, setAgents] = useState<string[]>([]); // agents that run in this tab
  const agentsRef = useRef<string[]>([]);
  const [, bump] = useState(0);
  const lineStore = useRef<Record<string, Line[]>>({});
  const [activity, setActivity] = useState<Record<string, AgentActivity>>({});
  const [streaming, setStreaming] = useState<Record<string, { room: string; text: string }>>({});
  const streamingRef = useRef(streaming);
  streamingRef.current = streaming;
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
  const relayAgentsRef = useRef<RelayAgent[]>([]);
  relayAgentsRef.current = relayAgents;
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

  const warnHistory = () => {
    if (historyWarned.current) return;
    historyWarned.current = true;
    push(roomRef.current, { kind: "notice", id: "history-warn", text: "This browser won't let keळ save your history, so it will be gone when you close the page.", t: Date.now() });
  };

  const push = useCallback((r: string, l: Line, remote = false) => {
    const cur = lineStore.current[r] ?? [];
    if (l.kind === "msg" && cur.some((x) => x.kind === "msg" && x.m.id === l.m.id)) return;
    lineStore.current = { ...lineStore.current, [r]: [...cur, l] }; // read straight away by the agents' history, before any re-render
    bump((n) => n + 1);
    if (l.kind === "msg") {
      history.appendMessage(l.m).catch(() => warnHistory());
      if (!remote) void engineRef.current?.enqueue(r, l.m); // typed or produced on this device: it goes to your account
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history]);
  const firstRooms = useRef(true);
  const note = useCallback((text: string, r: string = roomRef.current) => push(r, { kind: "notice", id: broker.newId(), text, t: Date.now() }), [broker, push]);

  // Notifications (lib/chat/notify.ts): what reaches you, in the bell, the tab title, the app badge, a chime and, in the background, an OS alert.
  const [prefs, setPrefsState] = useState<Prefs>(DEFAULT_PREFS);
  const prefsRef = useRef(prefs);
  const [notes, setNotes] = useState<Note[]>([]);
  const openRoomRef = useRef<(r: string | null) => void>(() => {});
  useEffect(() => { const p = loadPrefs(subject); prefsRef.current = p; setPrefsState(p); }, [subject]);
  const setPrefs = useCallback((p: Prefs) => { prefsRef.current = p; setPrefsState(p); savePrefs(subject, p); }, [subject]);
  const notify = useCallback((kind: NoteKind, r: string | null, title: string, body: string) => {
    const hidden = typeof document !== "undefined" && document.visibilityState === "hidden";
    const d = decide({ kind, room: r, onScreen: r !== null && r === roomRef.current, hidden }, prefsRef.current);
    if (!d.record) return;
    const n: Note = { id: crypto.randomUUID(), kind, room: r, title, body: previewText(prefsRef.current, body), at: Date.now(), read: false };
    setNotes((p) => [n, ...p].slice(0, 50));
    if (d.sound) chime();
    if (d.os) showOs(n, (x) => openRoomRef.current(x));
  }, []);

  const deps = useMemo<AssistantDeps>(() => ({
    resolve: makeResolver({ vault, token: getAccessToken, kv, subject }),
    shared: (r) => (infoRef.current[r]?.members ?? 1) > 1,
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
    writeAgents(kv, subject, agentsRef.current);
  };

  const removeAgent = (handle: string) => {
    controllers.current.get(handle)?.abort();
    detachers.current.get(handle)?.();
    detachers.current.delete(handle);
    for (const r of roomList.rooms) broker.removeMember(r, handle);
    agentsRef.current = agentsRef.current.filter((h) => h !== handle);
    setAgents(agentsRef.current);
    writeAgents(kv, subject, agentsRef.current);
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
    if (!started.current) { started.current = true; for (const h of readAgents(kv, subject, MAX_AGENTS)) addAgent(h); }
    const all = detachers.current;
    return () => { all.forEach((d) => d()); all.clear(); started.current = false; agentsRef.current = []; setAgents([]); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [broker, deps]);

  useEffect(() => {
    let live = true;
    describeBacking({ vault, kv, subject }).then((b) => { if (live) setBacking(b); }).catch(() => { if (live) setBacking({ ready: false, text: "Add a key to chat here" }); });
    return () => { live = false; };
  }, [vault, kv, subject]);

  // Keep your rooms the same on every device: push what was sent here, pull what was sent elsewhere.
  useEffect(() => {
    const engine = new SyncEngine({
      api: rapi, history, labels: () => roomListRef.current.rooms,
      busy: () => Object.keys(streamingRef.current).length > 0 || (tasksRef.current[roomRef.current] ?? []).some((t) => !isFinished(t)),
      hidden: () => document.visibilityState === "hidden",
      hooks: {
        onMessages: (label, ms) => {
          for (const m of ms) {
            push(label, { kind: "msg", m }, true);
            if (m.sender.handle === HUMAN.handle) continue;
            const mention = m.recipients.includes(HUMAN.handle);
            const kind: NoteKind = mention ? "mention" : m.sender.type === "human" ? "message" : "agent";
            notify(kind, label, mention ? `${m.sender.displayName} mentioned you` : m.sender.displayName, m.payload.text);
          }
        },
        onRooms: (c) => {
          if (firstRooms.current) firstRooms.current = false;
          else for (const a of c.added) notify("room", a.label, `Added to ${a.title || a.label}`, "A room was added to your account.");
          if (c.added.length || c.removed.length) {
            setRoomList((l) => {
              let rooms = l.rooms.filter((r) => !c.removed.includes(r));
              for (const a of c.added) if (!rooms.includes(a.label)) rooms = [...rooms, a.label];
              if (!rooms.length) rooms = [DEFAULT_ROOM];
              return { rooms, current: rooms.includes(l.current) ? l.current : rooms[0] };
            });
            for (const r of c.removed) {
              const { [r]: _gone, ...rest } = lineStore.current; // eslint-disable-line @typescript-eslint/no-unused-vars
              lineStore.current = rest;
              void history.clearRoom(r).catch(() => {});
              push(roomRef.current, { kind: "notice", id: crypto.randomUUID(), text: `${r} is no longer on your account (it was deleted, or you left it), so it was removed here.`, t: Date.now() });
            }
          }
          setInfo((p) => ({ ...p, ...c.info }));
        },
        onNotice: (label, text) => push(label ?? roomRef.current, { kind: "notice", id: crypto.randomUUID(), text, t: Date.now() }),
        onState: setSync,
      },
    });
    engineRef.current = engine;
    engine.start();
    const wake = () => engine.kick();
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", wake);
    return () => { engine.stop(); window.removeEventListener("online", wake); document.removeEventListener("visibilitychange", wake); engineRef.current = null; };
  }, [rapi, history, push, notify]);

  // Bring this room's saved history back from this device (what is already on screen wins on a clash).
  const hydrate = useCallback(async (r: string) => {
    try {
      const saved = await history.load(r);
      const cur = lineStore.current[r] ?? [];
      const have = new Set(cur.flatMap((l) => (l.kind === "msg" ? [l.m.id] : [])));
      const add: Line[] = saved.messages.filter((m) => !have.has(m.id)).map((m) => ({ kind: "msg", m }));
      if (add.length) { lineStore.current = { ...lineStore.current, [r]: [...add, ...cur].sort((a, b) => lineTime(a) - lineTime(b)) }; bump((n) => n + 1); }
      if (saved.tasks.length) setTasksByRoom((p) => ({ ...p, [r]: mergeTasks(saved.tasks, p[r] ?? []) }));
    } catch { /* nothing saved, or storage blocked: the room simply starts empty */ }
  }, [history]);
  useEffect(() => { void hydrate(room); }, [hydrate, room]);

  // Keep each relay task's latest state on this device, so it outlives the server's 7 days.
  const savedTasks = useRef(new Map<string, string>());
  useEffect(() => {
    for (const list of Object.values(tasksByRoom)) {
      for (const t of list) {
        const sig = `${t.status}|${t.claimed_at}|${t.finished_at}|${t.result?.length ?? 0}`;
        if (savedTasks.current.get(t.id) === sig) continue;
        savedTasks.current.set(t.id, sig);
        history.saveTask(t).catch(() => warnHistory());
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasksByRoom, history]);

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
          if (t.length) {
            const before = new Map(have.map((x) => [x.id, x.status]));
            for (const x of t) {
              const was = before.get(x.id);
              if (was === x.status) continue;
              const who = relayAgentsRef.current.find((a) => a.id === x.agent_id)?.handle ?? "An agent";
              if (x.status === "needs_approval") notify("approval", room, `${who} needs your approval`, x.text);
              else if (x.status === "done") notify("task", room, `${who} finished a task`, x.result ?? x.text);
              else if (x.status === "failed") notify("task", room, `${who} could not finish a task`, x.result ?? x.text);
            }
            setTasksByRoom((p) => ({ ...p, [room]: mergeTasks(p[room] ?? [], t) }));
          }
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
  }, [api, room, notify]);

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
              {run.sender.type === "human"
                ? <PersonAvatar sub={run.sender.sub} mine={run.sender.handle === HUMAN.handle} selfSub={subject} initials={initials(run.sender.handle)} />
                : <Avatar who={run.sender} />}
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

  // Tell your account how far you have read here, so unread counts follow you to your other devices.
  useEffect(() => {
    const t = setTimeout(() => void engineRef.current?.markRead(room), 1200);
    return () => clearTimeout(t);
  }, [room, itemKeys]);

  // Who is in this room (once it exists on your account), and an invite link for it.
  const synced = info[room] !== undefined;
  useEffect(() => {
    setInvite(null); setCopied(false); setPeopleError("");
    const sid = engineRef.current?.roomId(room);
    if (!sid) { setMembers([]); return; }
    let live = true;
    rapi.members(sid).then((m) => { if (live) setMembers(m); }).catch(() => { if (live) setMembers([]); });
    return () => { live = false; };
  }, [room, synced, rapi]);
  const iAmOwner = (info[room]?.role ?? "owner") !== "member";
  const makeInvite = async () => {
    const sid = engineRef.current?.roomId(room);
    if (!sid) return;
    setPeopleError("");
    try {
      const inv = await rapi.createInvite(sid, 7, 20);
      setInvite({ url: `${window.location.origin}/room/join?code=${encodeURIComponent(inv.code)}`, days: 7, uses: inv.max_uses });
      setCopied(false);
    } catch (err) { setPeopleError(err instanceof Error ? err.message : "That didn't work."); }
  };
  const copyInvite = async () => { try { await navigator.clipboard.writeText(invite?.url ?? ""); setCopied(true); } catch { setCopied(false); } };
  const removePerson = async (m: RoomMember) => {
    const sid = engineRef.current?.roomId(room);
    if (!sid || !m.sub) return;
    try { await rapi.removeMember(sid, m.sub); setMembers((p) => p.filter((x) => x.sub !== m.sub)); }
    catch (err) { setPeopleError(err instanceof Error ? err.message : "That didn't work."); }
  };

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
  openRoomRef.current = (r) => { if (r && roomList.rooms.includes(r)) chooseRoom(r); };
  useEffect(() => { setNotes((p) => (p.some((n) => !n.read && n.room === room) ? p.map((n) => (n.room === room ? { ...n, read: true } : n)) : p)); }, [room]);
  const unreadTotal = Object.entries(info).reduce((a, [r, v]) => a + (r === room ? 0 : v.unread), 0);
  useEffect(() => {
    const base = document.title.replace(/^\(\d+\+?\) /, "");
    document.title = titleWithCount(base, unreadTotal);
    setBadge(unreadTotal);
    return () => { document.title = base; setBadge(0); };
  }, [unreadTotal]);
  const createRoom = (e: React.FormEvent) => {
    e.preventDefault();
    const r = newRoomName(roomList.rooms, newRoom ?? "");
    if ("error" in r) return setFormError(r.error);
    setFormError(""); setNewRoom(null);
    setRoomList({ rooms: [...roomList.rooms, r.name], current: r.name });
  };
  const deleteRoom = async (r: string) => {
    const next = withoutRoom(roomList, r);
    if (next === roomList) return;
    const has = (lineStore.current[r] ?? []).some((l) => l.kind === "msg") || (tasksRef.current[r] ?? []).length > 0;
    const sid = engineRef.current?.roomId(r);
    const owner = (info[r]?.role ?? "owner") !== "member";
    const ask = sid
      ? owner ? `Delete ${r} for everyone? Its history is removed from your account and from everyone you invited. This can't be undone.` : `Leave ${r}? It is removed from your account.`
      : `Remove ${r}? Its saved history on this device is deleted too.`;
    if ((has || sid) && !window.confirm(ask)) return;
    if (sid) {
      try { if (owner) await rapi.deleteRoom(sid); else await rapi.removeMember(sid, subject); }
      catch (err) { note(err instanceof Error ? err.message : "That didn't work."); return; }
      await engineRef.current?.forget(r);
    }
    void history.clearRoom(r).catch(() => {});
    const { [r]: _l, ...rest } = lineStore.current; // eslint-disable-line @typescript-eslint/no-unused-vars
    lineStore.current = rest;
    setTasksByRoom((p) => { const { [r]: _t, ...others } = p; return others; }); // eslint-disable-line @typescript-eslint/no-unused-vars
    setRoomList(next);
  };

  // ---- your data
  const importFile = useRef<HTMLInputElement>(null);
  const download = async () => {
    try {
      const blob = new Blob([JSON.stringify(await history.exportAll(), null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `kel-rooms-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } catch { note("Couldn't read your saved history."); }
  };
  const importHistory = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (importFile.current) importFile.current.value = "";
    if (!f) return;
    try {
      const r = await history.importAll(JSON.parse(await f.text()));
      await hydrate(room);
      note(`Imported ${r.messages} message${r.messages === 1 ? "" : "s"} and ${r.tasks} task${r.tasks === 1 ? "" : "s"}.`);
    } catch (err) { note(err instanceof Error ? err.message : "Couldn't import that file."); }
  };
  const wipe = async () => {
    if (!window.confirm("Clear this device's copy of your rooms? Your rooms stay on your account, so they download again. To remove a room for good, remove the room.")) return;
    await history.clearAll().catch(() => {});
    lineStore.current = {};
    setTasksByRoom({});
    savedTasks.current.clear();
    bump((n) => n + 1);
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
      <aside id="dg-side" className="dg-side" data-lenis-prevent aria-label="Your rooms and agents">
        <button type="button" className="dg-close" onClick={() => setMenuOpen(false)} aria-label="Close the room list">×</button>
        {brand && <div className="dg-brand">{brand}</div>}
        {back && <div className="dg-backrow">{back}</div>}

        <h2>Rooms</h2>
        <ul className="dg-rooms">
          {roomList.rooms.map((r) => (
            <li key={r}>
              <button type="button" className="dg-room" aria-current={r === room ? "page" : undefined} onClick={() => chooseRoom(r)}><span aria-hidden="true">#</span>{r.slice(1)}{r !== room && (info[r]?.unread ?? 0) > 0 && <span className="dg-unread" aria-label={`${info[r]!.unread} unread`}>{info[r]!.unread > 99 ? "99+" : info[r]!.unread}</span>}</button>
              {roomList.rooms.length > 1 && <button type="button" className="dg-x" onClick={() => void deleteRoom(r)} aria-label={`Remove room ${r}`} title={`Remove ${r}`}>×</button>}
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

        <p className="dg-note">Rooms are open: they follow you to your other devices, Claude, ChatGPT and other connectors can join them, and keळ can read them. They are not end-to-end encrypted.</p>

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

        {synced && (
          <>
            <h2>People in {room}</h2>
            <ul className="dg-members">
              {members.filter((m) => m.kind === "person").map((m) => (
                <li key={m.sub ?? m.agent_id}>
                  <PersonAvatar sub={m.sub ?? undefined} mine={m.me} selfSub={subject} initials={initials(`@${m.name ?? "member"}`)} className="dg-avatar dg-avatar-sm" />
                  <span className="dg-handle">{m.me ? "You" : m.name ?? fallbackName(m.sub)}</span>
                  <span className="dg-badge" data-kind="tab">{m.role}</span>
                  {iAmOwner && !m.me && m.role !== "owner" && <button type="button" className="dg-remove" onClick={() => void removePerson(m)} aria-label={`Remove ${m.name ?? "this person"} from ${room}`} title="Remove from room">×</button>}
                </li>
              ))}
            </ul>
            {iAmOwner && !invite && <button type="button" className="btn-line dg-add" onClick={() => void makeInvite()}>Invite someone</button>}
            {invite && (
              <div className="dg-form" role="status">
                <label htmlFor="dg-invite-link">Invite link</label>
                <input id="dg-invite-link" readOnly value={invite.url} onFocus={(e) => e.currentTarget.select()} />
                <p className="dg-note" style={{ padding: 0 }}>Anyone with a keळ account who opens this link can join {room}. It works {invite.uses} times and expires in {invite.days} days. Share it only with people you want here.</p>
                <div className="dg-form-row"><button type="button" className="btn-ink dg-mini-btn" onClick={() => void copyInvite()}><span>{copied ? "Copied" : "Copy link"}</span></button><button type="button" className="btn-line dg-mini-btn" onClick={() => setInvite(null)}>Done</button></div>
              </div>
            )}
            {peopleError && <p className="dg-err" role="alert">{peopleError}</p>}
          </>
        )}

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
        <h2>Your data</h2>
        <p className="dg-note">Your rooms and chats are saved in this browser, encrypted, and synced to your account so they follow you to your other devices. Download keeps a copy of your own.</p>
        <div className="dg-data">
          <button type="button" className="btn-line dg-mini-btn" onClick={() => void download()}>Download</button>
          <button type="button" className="btn-line dg-mini-btn" onClick={() => importFile.current?.click()}>Import</button>
          <button type="button" className="btn-line dg-mini-btn" onClick={() => void wipe()}>Delete history</button>
          <input ref={importFile} type="file" accept="application/json,.json" className="visually-hidden" aria-label="Choose a history file to import" onChange={(e) => void importHistory(e)} />
        </div>
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
          <span className="dg-sync" data-status={sync.status} role="status" title={sync.error ?? "Your rooms sync to your account, so they follow you to other devices."}>
            {sync.status === "syncing" ? "Syncing…" : sync.status === "offline" ? `Offline${sync.pending ? ` · ${sync.pending} waiting` : ""}` : sync.status === "error" ? "Not syncing" : sync.pending ? `${sync.pending} sending` : "Synced"}
          </span>
          <NotificationBell notes={notes} prefs={prefs} room={room} onPrefs={setPrefs} onOpen={(r) => openRoomRef.current(r)}
            onReadAll={() => setNotes((p) => p.map((n) => ({ ...n, read: true })))} onClear={() => setNotes([])} />
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
                  <p className="dg-empty">No messages in {room} yet. {agents.length > 0 ? <>Say hello: <code>{agents[0]} hello</code>. </> : <>Invite people with the invite link, or add a model assistant to chat with. </>}Add your own agents with <b>+ Add agent</b>.</p>
                )}
                {items.map((i) => i.node)}
                {streamingHere.map(([h, v]) => (
                  <MessageScroller.Item key={`live-${h}`} className="dg-run" data-agent="true">
                    <Avatar who={assistantIdentityFor(h)} />
                    <div className="dg-col">
                      <div className="dg-meta"><b>{h}</b><span className="dg-badge">agent</span></div>
                      <div className="dg-body"><p><SmoothText text={v.text} /></p></div>
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
