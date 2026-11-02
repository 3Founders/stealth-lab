"use client";
import { MessageScroller, useMessageScrollerScrollable } from "@shadcn/react/message-scroller";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Composer from "@/components/chat/Composer";
import OnboardButton from "@/components/chat/OnboardButton";
import SearchPalette from "@/components/chat/SearchPalette";
import { drain, enqueue, inRoom, removeAt, type Queues } from "@/lib/chat/queue";
import { tempLabel, toTurns } from "@/lib/chat/temporary";
import CanvasPanel from "@/components/chat/CanvasPanel";
import ThemeToggle from "@/components/ThemeToggle";
import { CanvasContext } from "@/components/chat/CanvasCard";
import { captureFrom, hideCanvasWhileStreaming, newCanvas, type Canvas } from "@/lib/chat/canvas";
import { FilesContext } from "@/components/chat/FileCard";
import { FileError, filesApi, fileToken } from "@/lib/chat/files";
import { CONNECTOR_PAGES, connectorInstructions, newOnboardName, onboardPrompt } from "@/lib/chat/onboard";
import { KellMark } from "@/components/chat/brand";
import type { OnboardResult, OnboardTarget } from "@/components/chat/OnboardButton";
import { cap, nameOf, roomName } from "@/components/chat/bare";
import SmoothText, { Thinking } from "@/components/chat/SmoothText";
import { useSpringScroll } from "@/components/chat/useSpringScroll";
import type { KeyValue } from "@/lib/byok/spend";
import { Vault, idbStore } from "@/lib/byok/vault";
import { HUMAN, MAX_AGENTS, newAgentHandle } from "@/lib/chat/agents";
import { assistantIdentityFor } from "@/lib/chat/adapters/assistant";
import { mentionsIn, parseCommand, recipientsFor, toMcpPayload } from "@/lib/chat/adapters/mcp";
import { describeBacking, type Backing } from "@/lib/chat/adapters/resolve";
import { Broker } from "@/lib/chat/relay/broker";
import { cursorOf, isFinished, mentionedNames, mergeTasks, relay, RelayError, type RelayAgent, type RelayTask } from "@/lib/chat/relay-client";
import { DEFAULT_ROOM, newRoomName, readAgents, readPresets, readRooms, withoutRoom, writeAgents, writePresets, writeRooms, type RoomList } from "@/lib/chat/rooms";
import { RoomsApiError, roomsApi, type RoomMember } from "@/lib/chat/rooms-api";
import { SyncEngine, type RoomsChange, type SyncState } from "@/lib/chat/sync";
import { HISTORY_DB, HistoryStore } from "@/lib/chat/store";
import { chooseFolder, folderBackupSupported, folderName, forgetFolder, readBackup, writeBackup } from "@/lib/chat/folder-backup";
import type { AgentActivity, AgentIdentity, GroupMessage, ImageAttachment } from "@/lib/chat/types";
import { MAX_DEPTH } from "@/lib/chat/types";
import { groupMessages, initials, routeMessage } from "@/lib/chat/ui";
import { getAccessToken, signOut } from "@/lib/session";
import PersonAvatar from "@/components/chat/PersonAvatar";
import ProfileWidget from "@/components/chat/ProfileWidget";
import ManageRooms from "@/components/chat/ManageRooms";
import { PanelCloseIcon, RemoveIcon } from "@/components/chat/icons";
import HamburgerMenu from "@/components/chat/HamburgerMenu";
import SettingsDialog, { type SectionId } from "@/components/chat/SettingsDialog";
import { DEFAULT_CHAT_PREFS, TEXT_PX, loadChatPrefs, saveChatPrefs, type ChatPrefs } from "@/lib/chat/prefs";
import { fallbackName, personHandle } from "@/lib/chat/events";
import MessageText from "@/components/chat/MessageText";
import MessageActions from "@/components/chat/MessageActions";
import Markdown from "@/components/chat/Markdown";
import AddAgentDialog from "@/components/chat/AddAgentDialog";
import { withoutDataImages } from "@/lib/chat/images";
import { DEFAULT_PREFS, chime, decide, loadPrefs, previewText, savePrefs, setBadge, showOs, titleWithCount, type Note, type NoteKind, type Prefs } from "@/lib/chat/notify";

const LABEL: Record<AgentActivity, string> = { idle: "Idle", processing: "Working", delivering: "Sending" };
const STATUS: Record<RelayTask["status"], string> = {
  needs_approval: "Waiting for your approval", queued: "Waiting for the agent", claimed: "Working", done: "Done", failed: "Failed", cancelled: "Cancelled",
};

type Line = { kind: "msg"; m: GroupMessage } | { kind: "notice"; id: string; text: string; t: number };

const time = (t: number) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function Avatar({ who }: { who: AgentIdentity }) {
  // Every agent is kell, so every agent wears kell's mark; a person keeps their initials.
  if (who.type !== "human") return <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>;
  return <span className="dg-avatar" data-kind="human" aria-hidden="true">{initials(who.handle)}</span>;
}

function Body({ m }: { m: GroupMessage }) {
  return <div className="dg-body" data-mid={m.id}><MessageText text={m.payload.text} attachments={m.payload.attachments} /></div>;
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
  /** The other people in this room, as the @handles you can mention (their account name, the way the server stores a mention of them). */
  const peopleHandles = members.filter((m) => m.kind === "person" && !m.me).map((m) => personHandle(m.name ?? fallbackName(m.sub)));
  /** Your own @handle in a shared room: a message that mentions it is a mention of you. */
  const myHandleRef = useRef<string | null>(null);
  const meMember = members.find((m) => m.kind === "person" && m.me);
  myHandleRef.current = meMember ? personHandle(meMember.name ?? fallbackName(meMember.sub)) : null;

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
  const [streaming, setStreaming] = useState<Record<string, { room: string; text: string; final?: GroupMessage }>>({});
  const [kellBusy, setKellBusy] = useState<Record<string, boolean>>({});
  const streamingRef = useRef(streaming);
  streamingRef.current = streaming;
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);
  pausedRef.current = paused;
  const [backing, setBacking] = useState<Backing | undefined>(undefined);
  const [menuOpen, setMenuOpen] = useState(false); // phones: the sidebar is a drawer
  const [manageOpen, setManageOpen] = useState(false);
  const [panelHidden, setPanelHidden] = useState(false); // wide screens: the room list can be closed to give the chat the whole window
  const controllers = useRef(new Map<string, AbortController>());
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

  // Temporary rooms live in memory only: never saved to this device, never synced (lib/chat/temporary.ts).
  const tempRef = useRef<Set<string>>(new Set());
  // An agent's canvas blocks become versions of canvases kept on this device (lib/chat/canvas.ts); set below, called from push().
  const captureRef = useRef<(m: GroupMessage, live?: boolean) => void>(() => {});

  // sidebar forms
  const [adding, setAdding] = useState(false);
  const [addName, setAddName] = useState("");
  const [addAbout, setAddAbout] = useState("");
  const [presets, setPresets] = useState<Record<string, string>>({});
  const presetsRef = useRef(presets);
  presetsRef.current = presets;
  useEffect(() => { setPresets(readPresets(kv, subject)); }, [kv, subject]);
  const [formError, setFormError] = useState("");

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
    if (l.kind === "msg" && l.m.sender.type !== "human") captureRef.current(l.m);
    if (l.kind === "msg" && !tempRef.current.has(r)) {
      history.appendMessage(l.m).catch(() => warnHistory());
      if (!remote) { // typed or produced on this device: it goes to your account
        engineRef.current?.enqueue(r, l.m).catch(() => {
          push(r, { kind: "notice", id: `queue-${l.m.id}`, text: "That message could not be saved to send to your account, so it is only on this screen. Check that this browser allows site storage, then send it again.", t: Date.now() });
        });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history]);
  const firstRooms = useRef(true);
  const note = useCallback((text: string, r: string = roomRef.current) => push(r, { kind: "notice", id: broker.newId(), text, t: Date.now() }), [broker, push]);

  // The built-in room agent (@kell) runs on the server (mcp_server/room_agent_routes.py): a mention asks it, and its
  // answer is posted into the room as an agent message, so it arrives through sync on every device. The API needs the
  // SERVER room id (the label here is the title, e.g. #general), which arrives with the first sync.
  const roomsAgent = useMemo(() => roomsApi({ token: getAccessToken }), []);
  const filesClient = useMemo(() => filesApi({ token: getAccessToken }), []);
  const [sendingFiles, setSendingFiles] = useState("");
  const askingKell = useRef<Set<string>>(new Set());
  const queues = useRef<Queues>({});
  const [queueView, setQueueView] = useState<Queues>({});
  const askKellRef = useRef<((r: string, text: string, who?: string) => Promise<void>) | null>(null);
  /** Copy a message's text: to the clipboard, with a small notice as feedback. */
  const copyRoomText = useCallback(async (t: string): Promise<boolean> => {
    if (!t) return true;
    try { await navigator.clipboard.writeText(t); return true; }       // success is shown by the button itself (tick); no room notice
    catch { note("Copying was blocked by the browser."); return false; }
  }, [note]);
  /** Ask a relay agent the same task again: a new task, the old one stays as it ended. */
  const resendTask = useCallback(async (t: RelayTask) => {
    const r = roomRef.current;
    try {
      const out = await api.send(t.text, r);
      setTasksByRoom((p) => ({ ...p, [r]: mergeTasks(p[r] ?? [], out.tasks) }));
      note("Asked the agent again.", r);
    } catch (err) { note(err instanceof Error ? err.message : "That didn't send.", r); }
  }, [api, note]);
  /** Asks an agent to answer in this room. Every agent is kell: "@kell" is the default one, any other handle is an instance of it. */
  const askKell = useCallback(async (r: string, text: string, who: string = "@kell") => {
    if (pausedRef.current) { note("Agents are paused. Resume them from the menu to get an answer.", r); return; }
    const key = `${r}|${who}`;
    if (askingKell.current.has(key)) {                 // still answering: keep what was sent and ask once it has finished
      queues.current = enqueue(queues.current, key, text);
      setQueueView(queues.current);
      return;
    }
    askingKell.current.add(key);
    if (who === "@kell") setKellBusy((p) => ({ ...p, [r]: true }));
    const temp = tempRef.current.has(r);
    const id = temp ? "temporary" : engineRef.current?.roomId(r) ?? infoRef.current[r]?.id;
    const done = () => { askingKell.current.delete(key); if (who === "@kell") setKellBusy((p) => { const n = { ...p }; delete n[r]; return n; }); };
    if (!id) { note("This room is still syncing. Give it a second, then send again.", r); done(); return; }
    const ctl = new AbortController();
    let held = false;
    controllers.current.set(who, ctl);
    if (who !== "@kell") setStreaming((p) => ({ ...p, [who]: { room: r, text: "" } }));
    try {
      const out = await roomsAgent.agentStream(id, text, (delta) => {
        if (who !== "@kell") setStreaming((p) => ({ ...p, [who]: { room: r, text: ((p[who]?.text) ?? "") + delta } }));
        else setStreaming((p) => ({ ...p, "@kell": { room: r, text: ((p["@kell"]?.text) ?? "") + delta } }));
      }, ctl.signal, who, presetsRef.current[who],
      temp ? { history: toTurns((lineStore.current[r] ?? []).flatMap((l) => (l.kind === "msg" ? [l.m] : [])), who) } : undefined);
      for (const link of out.consent) note(`Allow ${nameOf(who)} to use that tool here (15 minutes): ${link}`, r); // shown to you only, never posted into the room
      if (out.event?.id || temp) {
        // Already on the server: the sync pull will find the same id and not add it twice. The streaming bubble stays until its text
        // has been revealed (so a reply that arrives all at once still animates), then this message takes its place.
        const m: GroupMessage = {
          id: out.event?.id ?? crypto.randomUUID(), roomId: r, sender: assistantIdentityFor(who), recipients: [],
          payload: toMcpPayload(out.text), timestamp: Date.now(), depthCounter: 0,
        };
        held = true;
        setStreaming((p) => ({ ...p, [who]: { room: r, text: out.text, final: m } }));
      } else if (!out.posted) note(`${nameOf(who)} answered but could not post it in the room.`, r);
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") { queues.current = drain(queues.current, key).rest; setQueueView(queues.current); note("Stopped.", r); }
      else if (err instanceof RoomsApiError && err.status === 401) note(`Sign in to ask ${nameOf(who)}.`, r);
      else if (err instanceof RoomsApiError && err.status === 404) note("This room is still syncing. Try again in a moment.", r);
      else note(err instanceof Error ? err.message : `${nameOf(who)} could not answer just now.`, r);
    } finally {
      controllers.current.delete(who);
      if (!held) setStreaming((p) => { const n = { ...p }; delete n[who]; return n; });
      done();
      const next = drain(queues.current, key);
      if (next.text !== null) {                        // everything sent meanwhile goes in one follow-up, oldest first
        const followUp = next.text;
        queues.current = next.rest;
        setQueueView(queues.current);
        window.setTimeout(() => void askKellRef.current?.(r, followUp, who), 0);
      }
    }
  }, [roomsAgent, note, push]);
  askKellRef.current = askKell;
  /** The streamed bubble has been fully revealed: the posted message takes its place. Safe to call twice. */
  const flushFinal = useCallback((who: string) => {
    const e = streamingRef.current[who];
    if (!e?.final) return;
    const m = e.final;
    streamingRef.current = (() => { const n = { ...streamingRef.current }; delete n[who]; return n; })();
    setStreaming((p) => { const n = { ...p }; delete n[who]; return n; });
    push(e.room, { kind: "msg", m }, true);
  }, [push]);
  // A hidden tab pauses the reveal; never keep a posted reply waiting longer than its text could take to read.
  useEffect(() => {
    const timers = Object.entries(streaming).filter(([, v]) => v.final).map(([who, v]) =>
      window.setTimeout(() => flushFinal(who), Math.min(9000, 1200 + v.text.length * 28)));
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [streaming, flushFinal]);


  // Notifications (lib/chat/notify.ts): what reaches you, in the bell, the tab title, the app badge, a chime and, in the background, an OS alert.
  const [prefs, setPrefsState] = useState<Prefs>(DEFAULT_PREFS);
  const prefsRef = useRef(prefs);
  const [notes, setNotes] = useState<Note[]>([]);
  // Settings window (opened from the menu) and the look-and-feel preferences it holds (lib/chat/prefs.ts).
  const [settings, setSettings] = useState<{ open: boolean; section: SectionId }>({ open: false, section: "preferences" });
  const [chatPrefs, setChatPrefsState] = useState<ChatPrefs>(DEFAULT_CHAT_PREFS);
  useEffect(() => { setChatPrefsState(loadChatPrefs(subject)); }, [subject]);
  const setChatPrefs = useCallback((p: ChatPrefs) => { setChatPrefsState(p); saveChatPrefs(subject, p); }, [subject]);
  const [accountName, setAccountName] = useState("");
  useEffect(() => { void import("@/lib/device-profile").then((m) => m.loadDeviceProfile()).then((p) => { if (p) setAccountName(p.name || (p.email ?? "")); }); }, [subject]);
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

  // Every room you have exists in the in-tab relay with the in-tab agents as members.
  useEffect(() => {
    for (const r of roomList.rooms) if (!broker.room(r)) broker.addRoom({ id: r, title: r, members: [HUMAN.handle, ...agentsRef.current] });
    const keep = roomList.rooms.filter((r) => !tempRef.current.has(r));     // temporary rooms are never written down
    writeRooms(kv, subject, { rooms: keep.length ? keep : [DEFAULT_ROOM], current: keep.includes(roomList.current) ? roomList.current : (keep[0] ?? DEFAULT_ROOM) });
  }, [broker, kv, subject, roomList]);

  // An added agent is another instance of kell: just a name. It answers through the same server agent as @kell.
  const addAgent = (handle: string, about?: string) => {
    if (about?.trim()) { const next = { ...presetsRef.current, [handle]: about.trim().slice(0, 1500) }; presetsRef.current = next; setPresets(next); writePresets(kv, subject, next); }
    if (agentsRef.current.includes(handle)) return;
    agentsRef.current = [...agentsRef.current, handle];
    setAgents(agentsRef.current);
    writeAgents(kv, subject, agentsRef.current);
  };

  const removeAgent = (handle: string) => {
    controllers.current.get(handle)?.abort();
    agentsRef.current = agentsRef.current.filter((h) => h !== handle);
    { const { [handle]: _gone, ...rest } = presetsRef.current; presetsRef.current = rest; setPresets(rest); writePresets(kv, subject, rest); }
    setAgents(agentsRef.current);
    writeAgents(kv, subject, agentsRef.current);
    setActivity((p) => { const n = { ...p }; delete n[handle]; return n; });
    note(`${nameOf(handle)} left the room.`);
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
    return () => { started.current = false; agentsRef.current = []; setAgents([]); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [broker]);

  useEffect(() => {
    let live = true;
    describeBacking({ vault, kv, subject }).then((b) => { if (live) setBacking(b); }).catch(() => { if (live) setBacking({ ready: false, text: "Add a key to chat here" }); });
    return () => { live = false; };
  }, [vault, kv, subject]);

  // Keep your rooms the same on every device: push what was sent here, pull what was sent elsewhere.
  useEffect(() => {
    const engine = new SyncEngine({
      api: rapi, history, labels: () => roomListRef.current.rooms.filter((r) => !tempRef.current.has(r)),
      busy: () => Object.keys(streamingRef.current).length > 0 || (tasksRef.current[roomRef.current] ?? []).some((t) => !isFinished(t)),
      hidden: () => document.visibilityState === "hidden",
      live: (id, after, onSignal, signal, onOpen) => rapi.watch(id, after, onSignal, signal, onOpen),
      hooks: {
        onMessages: (label, ms) => {
          for (const m of ms) {
            push(label, { kind: "msg", m }, true);
            if (m.sender.handle === HUMAN.handle) continue;
            const mine = myHandleRef.current;
            const mention = m.recipients.includes(HUMAN.handle) || (mine !== null && m.recipients.includes(mine));
            const kind: NoteKind = mention ? "mention" : m.sender.type === "human" ? "message" : "agent";
            notify(kind, label, mention ? `${m.sender.displayName} mentioned you` : m.sender.displayName, m.payload.text);
          }
        },
        onRooms: (c) => {
          if (firstRooms.current) firstRooms.current = false;
          else for (const a of c.added) notify("room", a.label, `Added to ${a.title || a.label}`, "A room was added to your account.");
          if (c.added.length) {
            setRoomList((l) => {
              let rooms = l.rooms;
              for (const a of c.added) if (!rooms.includes(a.label)) rooms = [...rooms, a.label];
              return { rooms, current: rooms.includes(l.current) ? l.current : rooms[0] };
            });
          }
          // A room the server no longer lists is NEVER deleted here: an empty or partial answer (a redeploy, a reset, a hiccup) must not
          // erase what is on this device. The room and its history stay, and sync re-links it and uploads it again. Removing a room is
          // only ever something you do yourself (the Remove button).
          for (const r of c.removed) {
            push(roomRef.current, { kind: "notice", id: crypto.randomUUID(), text: `${r} was not found on your account. Your copy here is kept and will sync again.`, t: Date.now() });
          }
          setInfo((p) => ({ ...p, ...c.info }));
        },
        onNotice: (label, text) => push(label ?? roomRef.current, { kind: "notice", id: crypto.randomUUID(), text, t: Date.now() }),
        onState: setSync,
      },
    });
    engineRef.current = engine;
    engine.start();
    engine.watch(roomRef.current);
    const wake = () => engine.kick();
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", wake);
    return () => { engine.stop(); window.removeEventListener("online", wake); document.removeEventListener("visibilitychange", wake); engineRef.current = null; };
  }, [rapi, history, push, notify]);

  // Ask the browser not to evict this site's storage (your history lives in it), and notice if the browser threw away our encryption key.
  useEffect(() => { try { void navigator.storage?.persist?.(); } catch { /* not supported */ } }, []);
  useEffect(() => {
    let live = true;
    void history.load(room).then(async () => {
      if (!live || !history.keyReplaced) return;
      await history.recoverFromKeyLoss();
      await engineRef.current?.resetLocal();
      push(room, { kind: "notice", id: "key-lost", text: "This browser cleared the key that protects your saved history, so the copy on this device could not be opened. Your rooms are on your account and are downloading again.", t: Date.now() });
    }).catch(() => {});
    return () => { live = false; };
  }, [history, room, push]);

  // The open room is followed live: the server tells us the moment something is written there.
  useEffect(() => { engineRef.current?.watch(room); }, [room]);

  // Bring this room's saved history back from this device (what is already on screen wins on a clash).
  const hydrate = useCallback(async (r: string) => {
    try {
      const saved = await history.load(r);
      const cur = lineStore.current[r] ?? [];
      const have = new Set(cur.flatMap((l) => (l.kind === "msg" ? [l.m.id] : [])));
      const add: Line[] = saved.messages.filter((m) => !have.has(m.id)).map((m) => ({ kind: "msg", m }));
      if (add.length) { lineStore.current = { ...lineStore.current, [r]: [...add, ...cur].sort((a, b) => lineTime(a) - lineTime(b)) }; bump((n) => n + 1); }
      for (const l of add) if (l.kind === "msg" && l.m.sender.type !== "human") captureRef.current(l.m, false); // rebuild canvases from saved history (a version already made from a message is not added twice)
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
              const who = nameOf(relayAgentsRef.current.find((a) => a.id === x.agent_id)?.handle ?? "An agent");
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
    type Run = ReturnType<typeof groupMessages>[number];
    const seq: Array<{ kind: "run"; run: Run } | { kind: "notice"; l: Extract<Line, { kind: "notice" }> }> = [];
    let buf: GroupMessage[] = [];
    const flush = () => {
      for (const run of groupMessages(buf)) seq.push({ kind: "run", run });
      buf = [];
    };
    for (const l of lines) {
      if (l.kind === "msg") buf.push(l.m);
      else { flush(); seq.push({ kind: "notice", l }); }
    }
    flush();
    // What Retry re-runs, per run: one of your own messages re-sends it; an agent's answer re-runs the latest human
    // message before it (the mention that woke it), and @kell answers again without re-posting that message.
    let lastHuman = "";
    for (const s of seq) {
      if (s.kind !== "run") {
        const l = s.l;
        out.push({ t: l.t, key: l.id, node: <MessageScroller.Item key={l.id} className="dg-notice" role="status">{l.text}</MessageScroller.Item> });
        continue;
      }
      const run = s.run;
      const texts = run.messages.map((m) => m.payload.text).filter((t): t is string => Boolean(t));
      const mine = run.sender.type === "human" && run.sender.handle === HUMAN.handle;
      if (run.sender.type === "human") {
        const t = texts[texts.length - 1];
        if (t) lastHuman = t;
      }
      const agentRun = run.sender.type !== "human";
      const isKell = agentRun && nameOf(run.sender.handle) === "kell";
      const prompt = mine ? texts[texts.length - 1] ?? "" : agentRun ? lastHuman : "";
      out.push({
        t: run.messages[0].timestamp, key: run.key,
        node: (
          <MessageScroller.Item key={run.key} messageId={run.key} className="dg-run" data-agent={agentRun}>
            {run.sender.type === "human"
              ? <PersonAvatar sub={run.sender.sub} mine={run.sender.handle === HUMAN.handle} selfSub={subject} initials={initials(run.sender.handle)} />
              : <Avatar who={run.sender} />}
            <div className="dg-col">
              <div className="dg-meta">
                <b>{nameOf(run.sender.handle)}</b>
                {agentRun && <span className="dg-badge">agent</span>}
                <time>{time(run.messages[0].timestamp)}</time>
                <MessageActions
                  copyText={texts.join("\n\n")}
                  retryText={prompt || undefined}
                  onCopy={(t) => copyRoomText(t)}
                  onRetry={(t) => { if (mine || !isKell) void submit(t); else void askKell(roomRef.current, t); }}
                  retryLabel={mine ? "Send this again" : isKell ? "Ask @kell again" : "Ask again"}
                />
              </div>
              {run.messages.map((m) => <Body key={m.id} m={m} />)}
            </div>
          </MessageScroller.Item>
        ),
      });
    }
    for (const task of tasks) {
      const ag = relayAgents.find((a) => a.id === task.agent_id);
      const handle = ag?.handle ?? "@agent";
      const t = Date.parse(task.created_at);
      out.push({
        t, key: `task-${task.id}`,
        node: (
          <MessageScroller.Item key={`task-${task.id}`} className="dg-task" data-status={task.status}>
            <div className="dg-meta">
              <b>You</b><span aria-hidden="true">→</span><b>{nameOf(handle)}</b>
              <span className="dg-status" data-status={task.status}>{STATUS[task.status]}</span>
              <time>{time(t)}</time>
            </div>
            <div className="dg-body"><p>{task.text}</p></div>
            {task.status === "queued" && ag && !ag.online && <p className="dg-hint">{nameOf(handle)} is offline. It will pick this up the next time it checks its inbox.</p>}
            {(task.status === "queued" || task.status === "claimed") && (
              <p className="dg-working"><span className="dg-pulse" aria-hidden="true" /> {nameOf(handle)} is working…</p>
            )}
            {task.result && (
              <div className="dg-result">
                <div className="dg-meta">
                  <b>{nameOf(handle)}</b><span className="dg-badge">agent</span>
                  <MessageActions copyText={task.result} onCopy={(t) => copyRoomText(t)} />
                </div>
                <div className="dg-body"><Markdown text={task.result} /></div>
              </div>
            )}
            {!isFinished(task) && (
              <div className="dg-task-actions">
                {task.status === "needs_approval" && <button type="button" className="dg-mini" onClick={() => void act(task.id, "approve")}>Approve</button>}
                <button type="button" className="dg-mini" onClick={() => void act(task.id, "cancel")}>Cancel</button>
              </div>
            )}
            {isFinished(task) && (
              <div className="dg-task-actions">
                <button type="button" className="dg-mini" onClick={() => void resendTask(task)}>Ask again</button>
              </div>
            )}
          </MessageScroller.Item>
        ),
      });
    }
    return out.sort((a, b) => a.t - b.t);
  })();

  const streamingHere = Object.entries(streaming).filter(([, v]) => v.room === room);
  const kellHere = kellBusy[room] === true && streaming["@kell"] === undefined;
  const liveText = JSON.stringify(streaming);
  useEffect(() => { scrollToEnd({ instant: true }); }, [scrollToEnd, room]);
  const itemKeys = items.map((i) => i.key).join("|");
  useEffect(() => { follow(); }, [itemKeys, tasks, liveText, follow]);

  // Tell your account how far you have read here, so unread counts follow you to your other devices.
  useEffect(() => {
    const t = setTimeout(() => void engineRef.current?.markRead(room), 1200);
    return () => clearTimeout(t);
  }, [room, itemKeys]);

  // Who is in this room (once it exists on your account). Changing them happens in Manage rooms.
  const synced = info[room] !== undefined;
  useEffect(() => {
    const sid = engineRef.current?.roomId(room);
    if (!sid) { setMembers([]); return; }
    let live = true;
    rapi.members(sid).then((m) => { if (live) setMembers(m); }).catch(() => { if (live) setMembers([]); });
    return () => { live = false; };
  }, [room, synced, rapi, manageOpen]); // reloaded when Manage rooms closes, since roles and people may have changed

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
  /** Wide screens have a panel that can be closed; phones have a drawer. Closing or opening must touch only the one that is on screen,
   *  or a drawer closed with the x leaves the panel flag set and the next "Open sidebar" tap opens nothing. */
  const isWide = () => typeof window === "undefined" || typeof window.matchMedia !== "function" || window.matchMedia("(min-width: 761px)").matches;
  const closeSidebar = () => { if (isWide()) setPanelHidden(true); else setMenuOpen(false); };
  const openSidebar = () => { if (isWide()) setPanelHidden(false); else setMenuOpen(true); };
  const toggleSidebar = () => { if (isWide()) setPanelHidden((h) => !h); else setMenuOpen((o) => !o); };
  const chooseRoom = (r: string) => { setRoomList((l) => ({ ...l, current: r })); setMenuOpen(false); };
  openRoomRef.current = (r) => { if (r && roomList.rooms.includes(r)) chooseRoom(r); };

  // Search (Cmd+K or Ctrl+K, or the menu): reads the messages saved on this device, and opens a room at the message found.
  const [searchOpen, setSearchOpen] = useState(false);

  // Canvas: documents, code and pages the agents wrote, with their versions, on this device (encrypted).
  const [canvases, setCanvases] = useState<Canvas[]>([]);
  const canvasesRef = useRef<Canvas[]>([]);
  canvasesRef.current = canvases;
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [canvasId, setCanvasId] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ text: string; n: number }>({ text: "", n: 0 });
  useEffect(() => { void history.listDocs<Canvas>("canvas").then((l) => setCanvases(l.map((d) => d.value))).catch(() => {}); }, [history]);
  const storeCanvas = (c: Canvas) => {
    const next = canvasesRef.current.some((x) => x.id === c.id) ? canvasesRef.current.map((x) => (x.id === c.id ? c : x)) : [...canvasesRef.current, c];
    canvasesRef.current = next; setCanvases(next);
    if (!tempRef.current.has(c.room)) void history.putDoc("canvas", c.id, c).catch(() => {});
  };
  captureRef.current = (m, live = true) => {
    const changed = captureFrom(canvasesRef.current, { id: m.id, room: m.roomId, by: m.sender.handle, text: m.payload.text, at: m.timestamp }, () => crypto.randomUUID());
    for (const c of changed) storeCanvas(c);
    if (changed.length) { setCanvasId(changed[0].id); if (live && m.roomId === roomRef.current) setCanvasOpen(true); } // a canvas an agent just wrote opens beside the chat
  };
  const canvasCtx = useMemo(() => ({
    open: (title: string) => {
      const c = canvasesRef.current.find((x) => x.room === roomRef.current && x.title.toLowerCase() === title.toLowerCase());
      if (c) setCanvasId(c.id);
      setCanvasOpen(true);
    },
  }), []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); setSearchOpen((o) => !o); } };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  const loadForSearch = useCallback(async () => {
    const seen = new Set<string>();
    const out: Array<{ room: string; m: GroupMessage }> = [];
    const add = (room: string, m: GroupMessage) => { const key = `${room}:${m.id}`; if (!seen.has(key)) { seen.add(key); out.push({ room, m }); } };
    for (const [room, lines] of Object.entries(lineStore.current)) for (const l of lines) if (l.kind === "msg") add(room, l.m);
    try { for (const room of await history.rooms()) for (const m of (await history.load(room)).messages) add(room, m); } catch { /* storage blocked: search what is on screen */ }
    return out;
  }, [history]);
  const pickSearch = (r: string, id: string | null) => {
    if (r !== roomRef.current) chooseRoom(r);
    if (!id) return;
    const find = (tries: number) => {
      const el = document.querySelector<HTMLElement>(`[data-mid="${CSS.escape(id)}"]`);
      if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.setAttribute("data-found", ""); window.setTimeout(() => el.removeAttribute("data-found"), 2400); }
      else if (tries > 0) window.setTimeout(() => find(tries - 1), 150);
    };
    window.setTimeout(() => find(10), 120);
  };
  useEffect(() => { setNotes((p) => (p.some((n) => !n.read && n.room === room) ? p.map((n) => (n.room === room ? { ...n, read: true } : n)) : p)); }, [room]);
  const unreadTotal = Object.entries(info).reduce((a, [r, v]) => a + (r === room ? 0 : v.unread), 0);
  useEffect(() => {
    const base = document.title.replace(/^\(\d+\+?\) /, "");
    document.title = titleWithCount(base, unreadTotal);
    setBadge(unreadTotal);
    return () => { document.title = base; setBadge(0); };
  }, [unreadTotal]);
  /** Called from Manage rooms: makes the room and switches to it, or returns why it cannot. */
  const createRoomNamed = (raw: string): { id: string } | { error: string } => {
    const r = newRoomName(roomList.rooms, raw);
    if ("error" in r) return r;
    setRoomList({ rooms: [...roomList.rooms, r.name], current: r.name });
    return { id: r.name };
  };
  const deleteRoom = async (r: string) => {
    const next = withoutRoom(roomList, r);
    if (next === roomList) return;
    const has = (lineStore.current[r] ?? []).some((l) => l.kind === "msg") || (tasksRef.current[r] ?? []).length > 0;
    const sid = engineRef.current?.roomId(r);
    const owner = (info[r]?.role ?? "owner") === "owner";
    const ask = sid
      ? owner ? `Delete ${r} for everyone? Its history is removed from your account and from everyone you invited. This can't be undone.` : `Leave ${r}? It is removed from your account.`
      : `Remove ${r}? Its saved history on this device is deleted too.`;
    if ((has || sid) && !window.confirm(ask)) return;
    if (sid) {
      try { if (owner) await rapi.deleteRoom(sid); else await rapi.removeMember(sid, subject); }
      catch (err) { note(err instanceof Error ? err.message : "That didn't work."); return; }
      await engineRef.current?.forget(r);
    }
    tempRef.current.delete(r);
    void history.clearRoom(r).catch(() => {});
    const { [r]: _l, ...rest } = lineStore.current; // eslint-disable-line @typescript-eslint/no-unused-vars
    lineStore.current = rest;
    setTasksByRoom((p) => { const { [r]: _t, ...others } = p; return others; }); // eslint-disable-line @typescript-eslint/no-unused-vars
    setRoomList(next);
  };

  // ---- your data
  const importFile = useRef<HTMLInputElement>(null);
  // A copy in a folder on this computer: chosen once, then kept up to date a little after each change.
  const folderStore = useMemo(() => idbStore("kel-folder"), []);
  const [folder, setFolder] = useState<string | null>(null);
  useEffect(() => { void folderName(folderStore, subject).then(setFolder).catch(() => {}); }, [folderStore, subject]);
  const lastBackup = useRef(0);
  useEffect(() => {
    if (!folder) return;
    const t = window.setInterval(async () => {
      if (document.visibilityState === "hidden" || Date.now() - lastBackup.current < 25_000) return;
      lastBackup.current = Date.now();
      try { await writeBackup(folderStore, subject, await history.exportAll()); } catch { /* the next round tries again */ }
    }, 30_000);
    return () => window.clearInterval(t);
  }, [folder, folderStore, subject, history]);
  const chooseBackupFolder = async () => {
    const n = await chooseFolder(folderStore, subject);
    if (!n) return;
    setFolder(n);
    note((await writeBackup(folderStore, subject, await history.exportAll(), true)) ? `Keeping a copy of your chats in "${n}".` : "Couldn't write to that folder.");
  };
  const restoreFromFolder = async () => {
    const b = await readBackup(folderStore, subject);
    if (!b) { note("Couldn't find a backup in that folder."); return; }
    try { const r = await history.importAll(b); await hydrate(room); note(`Restored ${r.messages} message${r.messages === 1 ? "" : "s"} from the folder.`); } catch (err) { note(err instanceof Error ? err.message : "Couldn't restore from that folder."); }
  };
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
    await engineRef.current?.resetLocal().catch(() => {}); // forget where sync had read up to, so everything downloads again
    lineStore.current = {};
    setTasksByRoom({});
    savedTasks.current.clear();
    bump((n) => n + 1);
  };

  // ---- agents
  const submitAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    if (full) return setFormError(`A room can have ${MAX_AGENTS} added agents. Remove one first.`);
    const r = newAgentHandle(agents, addName.trim() || undefined);
    if ("error" in r) return setFormError(r.error);
    addAgent(r.handle, addAbout);
    note(`${nameOf(r.handle)} joined the room.`);
    setAdding(false); setAddName(""); setAddAbout("");
  };
  /** "Onboard Your Agent": registers a fresh agent name on the account and returns the text the person pastes into their agent. */
  const onboardAgent = async (target: OnboardTarget = "code"): Promise<OnboardResult> => {
    for (let tries = 0; tries < 3; tries++) {
      const name = newOnboardName();
      try {
        const a = await api.addAgent(name);
        setRelayAgents((p) => [...p.filter((x) => x.id !== a.id), a]);
        if (target === "code") return { text: onboardPrompt({ name: a.name, room, kind: "code" }) };
        return { text: connectorInstructions({ name: a.name, room, kind: target }), openUrl: CONNECTOR_PAGES[target] };
      } catch (err) {
        if (tries === 2 || (err instanceof RelayError && err.status !== 409)) { setFormError(""); note(err instanceof Error ? err.message : "That didn't work."); throw err; }
      }
    }
    throw new Error("unreachable");
  };
  const removeRelayAgent = async (a: RelayAgent) => {
    try {
      const r = await api.removeAgent(a.id);
      setRelayAgents((p) => p.filter((x) => x.id !== a.id));
      note(`${nameOf(a.handle)} was removed${r.cancelled ? ` and ${r.cancelled} unfinished task${r.cancelled === 1 ? "" : "s"} cancelled` : ""}.`);
    } catch (err) { note(err instanceof Error ? err.message : "That didn't work."); }
  };

  /** A room that is not saved or synced: gone when this page closes. */
  const newTemporary = () => {
    const label = tempLabel(roomList.rooms);
    tempRef.current = new Set(tempRef.current).add(label);
    setRoomList((l) => ({ rooms: [...l.rooms, label], current: label }));
    setMenuOpen(false);
  };
  const shownName = (r: string) => (tempRef.current.has(r) ? "Temporary chat" : roomName(r));

  /** Saves this room as a Markdown file: who said what and when. It is made on this device from what is already here. */
  const exportRoom = () => {
    const lines = (lineStore.current[room] ?? []).flatMap((l) => (l.kind === "msg" ? [l.m] : []));
    if (!lines.length) return note("There is nothing in this room to export yet.");
    const md = [`# ${roomName(room)}`, "", ...lines.flatMap((m) => [`**${nameOf(m.sender.handle)}** · ${new Date(m.timestamp).toLocaleString()}`, "", m.payload.text, ""])].join("\n");
    const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url; a.download = `${roomName(room).toLowerCase().replace(/[^a-z0-9]+/g, "-")}.md`;
    document.body.appendChild(a); a.click(); a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
    note("Saved this room as a Markdown file.");
  };

  const submit = async (line: string, pictures?: ImageAttachment[], docs?: File[]) => {
    // Files go to the server first; the message carries only their names, sizes and ids. If one fails, nothing is sent.
    if (docs?.length && tempRef.current.has(room)) return note("Files can't be sent in a temporary room.");
    if (docs?.length) {
      const rid = engineRef.current?.roomId(room) ?? infoRef.current[room]?.id;
      if (!rid) return note("This room is still syncing. Give it a second, then send again.");
      const tokens: string[] = [];
      try {
        for (const f of docs) {
          setSendingFiles(`Sending ${f.name}`);
          const up = await filesClient.upload(rid, f, (p) => setSendingFiles(`Sending ${f.name} ${Math.round(p * 100)}%`));
          tokens.push(fileToken(up));
        }
      } catch (err) {
        setSendingFiles("");
        return note(`${err instanceof FileError || err instanceof Error ? err.message : "The file didn't send."} Nothing was sent.`);
      }
      setSendingFiles("");
      line = [line, ...tokens].filter(Boolean).join("\n");
    }
    const c = parseCommand(line);
    if (c) {
      if (c.cmd === "error") return note(c.message);
      if (c.cmd === "stop") {
        broker.setPaused(true);
        controllers.current.forEach((ctl) => ctl.abort());
        return note("/stop: paused. No agent in this tab will be woken until you resume.");
      }
      if (c.cmd === "export") return exportRoom();
      if (c.cmd === "temporary") return newTemporary();
      const r = newAgentHandle(agents, c.name);
      if ("error" in r) return note(r.error);
      addAgent(r.handle, c.about);
      return note(`${nameOf(r.handle)} joined the room.`);
    }
    const route = routeMessage(line, { local: agents, relay: relayAgents.map((a) => a.name), people: peopleHandles });
    if (route.toRelay.length) {
      if (pictures?.length) note("Pictures can't go to connector agents yet, so only the text was sent to them.");
      try {
        const r = await api.send(line, room);
        setTasksByRoom((p) => ({ ...p, [room]: mergeTasks(p[room] ?? [], r.tasks) }));
      } catch (err) { note(err instanceof Error ? err.message : "That didn't send."); }
      const offline = route.toRelay.filter((n) => !relayAgents.find((a) => a.name.toLowerCase() === n)?.online);
      if (offline.length) note(`${offline.join(", ")} ${offline.length === 1 ? "is" : "are"} offline; the message waits in the inbox for when it starts.`);
      if (!route.localMentions.length && !route.asksKell) return;
    } else if (route.unknown.length && !route.localMentions.length) {
      note(`No agent named ${route.unknown.join(", ")}. Add one with + Add agent.`);
      return;                                        // an unknown @name is never handed to @kell
    }
    const m: GroupMessage = {
      id: broker.newId(), roomId: room, sender: HUMAN,
      recipients: route.toRelay.length ? route.localMentions : route.defaultToKell ? ["@kell"] : recipientsFor(line),
      payload: { ...toMcpPayload(line), ...(pictures?.length ? { attachments: pictures } : {}) }, timestamp: Date.now(), depthCounter: 0,
    };
    await broker.send(m);
    if (route.asksKell) void askKell(room, line);
    for (const h of route.localMentions) if (h !== "@kell") void askKell(room, line, h);
  };

  const working = agents.filter((h) => (activity[h] ?? "idle") !== "idle" || streaming[h] !== undefined);
  const relayWorkingHandles = tasks
    .filter((t) => t.status === "queued" || t.status === "claimed")
    .map((t) => relayAgents.find((a) => a.id === t.agent_id)?.handle ?? "@agent");
  const mentionable = [...new Set([...agents, "@kell", ...relayAgents.map((a) => a.handle), ...peopleHandles])];
  const filesCtx = useMemo(() => ({
    fetchBlob: async (fileId: string) => {
      const rid = engineRef.current?.roomId(roomRef.current) ?? infoRef.current[roomRef.current]?.id;
      if (!rid) throw new FileError("This room is still syncing. Try again in a moment.");
      return filesClient.download(rid, fileId);
    },
  }), [filesClient]);
  const full = agents.length >= MAX_AGENTS;
  const count = 1 + agents.length + relayAgents.length;   // kell itself, its instances, and connected agents: all agents

  return (
    <FilesContext.Provider value={filesCtx}>
    <CanvasContext.Provider value={canvasCtx}>
    <div className="dg-shell" data-paused={paused} data-panel={panelHidden ? "closed" : "open"} data-menu={menuOpen ? "open" : "closed"} data-compact={chatPrefs.compact} data-times={chatPrefs.showTimes} style={{ fontSize: TEXT_PX[chatPrefs.textSize] }}>
      <div className="dg-scrim" onClick={() => setMenuOpen(false)} aria-hidden="true" />
      <aside id="dg-side" className="dg-side" data-lenis-prevent aria-label="Your rooms and agents">
        <button type="button" className="dg-close" onClick={closeSidebar} aria-label="Close the room list" title="Close the room list"><PanelCloseIcon /></button>
        {brand && <div className="dg-brand">{brand}</div>}
        {back && <div className="dg-backrow">{back}</div>}

        <h2>Rooms</h2>
        <ul className="dg-rooms">
          {roomList.rooms.map((r) => (
            <li key={r}>
              <button type="button" className="dg-room" aria-current={r === room ? "page" : undefined} onClick={() => chooseRoom(r)}><span aria-hidden="true">#</span>{tempRef.current.has(r) ? "Temporary" : cap(r.slice(1))}{tempRef.current.has(r) && <span className="dg-badge">not saved</span>}{r !== room && (info[r]?.unread ?? 0) > 0 && <span className="dg-unread" aria-label={`${info[r]!.unread} unread`}>{info[r]!.unread > 99 ? "99+" : info[r]!.unread}</span>}</button>
            </li>
          ))}
        </ul>
        <button type="button" className="btn-line dg-add" onClick={() => setManageOpen(true)}>Manage rooms</button>

        <h2>Agents</h2>
        <OnboardButton onOnboard={onboardAgent} className="dg-onboard-side" />
        <ul className="dg-members">
          <li>
            <span className="dg-dot" data-a={kellBusy[room] ? "processing" : "online"} aria-hidden="true" />
            <KellMark size={16} /><span className="dg-handle">Kell</span><span className="dg-badge">default</span>
            {kellBusy[room] && <span className="dg-state" data-a="processing">working</span>}
          </li>
          {agents.map((h) => {
            const a = streaming[h] !== undefined ? "processing" : "online";
            return (
              <li key={h}>
                <span className="dg-dot" data-a={a} aria-hidden="true" />
                <KellMark size={16} /><span className="dg-handle">{nameOf(h)}</span><span className="dg-badge">kell</span>
                {a === "processing" && <span className="dg-state" data-a={a}>working</span>}
                <button type="button" className="dg-remove" onClick={() => removeAgent(h)} aria-label={`Remove ${nameOf(h)} from the room`} title={`Remove ${nameOf(h)}`}><RemoveIcon /></button>
              </li>
            );
          })}
          {relayAgents.map((a) => (
            <li key={a.id}>
              <span className="dg-dot" data-a={a.online ? "online" : "idle"} aria-hidden="true" />
              <span className="dg-handle">{nameOf(a.handle)}</span><span className="dg-badge">your agent</span>
              <span className="dg-state" data-a={a.online ? "online" : "idle"} title={a.online ? "It checked its inbox in the last minute." : "Not connected yet. Paste the message into your agent, or ask it to check its kell inbox."}>{a.online ? "online" : "not connected"}</span>
              <button type="button" className="dg-remove" onClick={() => void removeRelayAgent(a)} aria-label={`Remove ${nameOf(a.handle)}`} title={`Remove ${nameOf(a.handle)}`}><RemoveIcon /></button>
            </li>
          ))}
        </ul>
        {relayError && <p className="dg-err" role="status">{relayError}</p>}

        {synced && (
          <>
            <h2>People in {roomName(room)}</h2>
            <ul className="dg-members">
              {members.filter((m) => m.kind === "person").map((m) => (
                <li key={m.sub ?? m.agent_id}>
                  <PersonAvatar sub={m.sub ?? undefined} mine={m.me} selfSub={subject} initials={initials(`@${m.name ?? "member"}`)} className="dg-avatar dg-avatar-sm" />
                  <span className="dg-handle">{m.me ? "You" : cap(m.name ?? fallbackName(m.sub))}</span>
                  <span className="dg-badge" data-kind="tab">{m.role}</span>
                </li>
              ))}
            </ul>
            <button type="button" className="btn-line dg-add" onClick={() => setManageOpen(true)}>Manage people</button>
          </>
        )}

        <button type="button" className="btn-line dg-add" onClick={() => { setAdding(true); setFormError(""); }}>+ Add agent</button>
        <AddAgentDialog
          open={adding} onClose={() => { setAdding(false); setFormError(""); }} full={full}
          name={addName} onName={setAddName} about={addAbout} onAbout={setAddAbout} error={formError} onSubmit={(e) => void submitAgent(e)}
          onOnboard={onboardAgent}
        />
        <ProfileWidget subject={subject} status={sync.status === "syncing" ? "Syncing" : sync.status === "offline" ? "Offline" : sync.status === "error" ? "Not syncing" : sync.live ? "Live" : "Synced"} agents={count} onSignOut={() => void signOut().then(() => { window.location.assign("/"); })} />
      </aside>

      <CanvasPanel open={canvasOpen} onClose={() => setCanvasOpen(false)} canvases={canvases} room={room} selectedId={canvasId} />
      <SearchPalette open={searchOpen} onClose={() => setSearchOpen(false)} rooms={roomList.rooms} currentRoom={room} load={loadForSearch} onPick={pickSearch} />
      <ManageRooms
        open={manageOpen} onClose={() => setManageOpen(false)} rooms={roomList.rooms} initial={room}
        roles={Object.fromEntries(Object.entries(info).map(([k, v]) => [k, v.role]))} rapi={rapi}
        roomId={(label) => engineRef.current?.roomId(label)} onDissolve={async (label) => { await deleteRoom(label); }} onCreate={createRoomNamed}
      />
      <section className="dg-main" aria-label={`Room ${roomName(room)}`}>
        <header className="dg-head">
          <HamburgerMenu onSearch={() => setSearchOpen(true)} canvasCount={canvases.filter((c) => c.room === room).length} onCanvas={() => setCanvasOpen(true)} onTemporary={newTemporary} panelOpen={isWide() ? !panelHidden : menuOpen} onTogglePanel={toggleSidebar} onManageRooms={() => setManageOpen(true)} paused={paused} onSettings={(section) => setSettings({ open: true, section })} onPause={() => broker.setPaused(!paused)}
            alerts={{ notes, prefs, room, onPrefs: setPrefs, onOpen: (r) => openRoomRef.current(r), onReadAll: () => setNotes((p) => p.map((n) => ({ ...n, read: true }))), onClear: () => setNotes([]) }}
            onSignOut={() => void signOut().then(() => { window.location.assign("/"); })} />
          <button type="button" className="dg-menu" onClick={openSidebar} aria-expanded={menuOpen} aria-controls="dg-side" aria-label="Open sidebar">
            <span aria-hidden="true">▤</span> <span>Open sidebar</span>
          </button>
          <h1>{shownName(room)}</h1>
          <ThemeToggle className="dg-theme" />
        </header>
        {tempRef.current.has(room) && <div className="dg-temp-note" role="status">Temporary chat: not saved, not synced, and gone when you close this page.</div>}
        {paused && <div className="dg-banner" role="status">Paused. Agents in this tab will not be woken until you resume.</div>}

        <MessageScroller.Provider autoScroll={false} defaultScrollPosition="end">
          <MessageScroller.Root className="dg-scroll">
            <MessageScroller.Viewport ref={viewportRef} data-lenis-prevent className="dg-viewport" aria-label={`Messages in ${room}`}>
              <MessageScroller.Content ref={contentRef} className="dg-content">
                {items.length === 0 && streamingHere.length === 0 && !kellHere && (
                  <p className="dg-empty">No messages in {roomName(room)} yet. {agents.length > 0 ? <>Say hello: <code>{agents[0]} hello</code>. </> : <>Invite people with the invite link, or add a model assistant to chat with. </>}In a room with just you, a message with no mention goes to <b>kell</b>; add your own agents with <b>+ Add agent</b>.</p>
                )}
                {items.map((i) => i.node)}
                {streamingHere.map(([h, v]) => (
                  <MessageScroller.Item key={`live-${h}`} className="dg-run" data-agent="true">
                    <Avatar who={assistantIdentityFor(h)} />
                    <div className="dg-col">
                      <div className="dg-meta"><b>{nameOf(h)}</b><span className="dg-badge">agent</span></div>
                      <div className="dg-body"><SmoothText markdown text={hideCanvasWhileStreaming(withoutDataImages(v.text))} onRevealed={v.final ? () => flushFinal(h) : undefined} /></div>
                    </div>
                  </MessageScroller.Item>
                ))}
                {kellHere && (
                  <MessageScroller.Item className="dg-run" data-agent="true">
                    <Avatar who={assistantIdentityFor("@kell")} />
                    <div className="dg-col">
                      <div className="dg-meta"><b>Kell</b><span className="dg-badge">agent</span></div>
                      <div className="dg-body" role="status" aria-label="kell is working"><Thinking /></div>
                    </div>
                  </MessageScroller.Item>
                )}
              </MessageScroller.Content>
            </MessageScroller.Viewport>
            <JumpToLatest onJump={() => scrollToEnd()} />
          </MessageScroller.Root>
        </MessageScroller.Provider>

        {sendingFiles && <div className="dg-sending" role="status" aria-live="polite"><span className="dg-pulse" aria-hidden="true" /> {sendingFiles}</div>}
        {inRoom(queueView, room).map((q) => (
          <div key={`${q.key}:${q.index}`} className="dg-queued small" role="status">
            <span className="dim">Waiting for {nameOf(q.who)}:</span> <span className="dg-queued-text">{q.text}</span>
            <button type="button" aria-label="Don't ask for this one" title="Don't ask for this one" onClick={() => { queues.current = removeAt(queues.current, q.key, q.index); setQueueView(queues.current); }}>×</button>
          </div>))}
        <div className="dg-typing" role="status" aria-live="polite">
          {(working.length > 0 || relayWorkingHandles.length > 0 || kellHere) && <>
            <span className="dg-pulse" aria-hidden="true" />
            {[
              ...working.map((h) => `${nameOf(h)} is working`),
              ...relayWorkingHandles.map((h) => `${nameOf(h)} is working`),
              ...(kellHere ? ["Kell is working"] : []),
            ].join(" · ")}
          </>}
          {streamingHere.length > 0 && (
            <button type="button" className="dg-stop" onClick={() => controllers.current.forEach((c) => c.abort())}>Stop</button>
          )}
        </div>
        <Composer draft={draft} handles={mentionable} agentHandles={[...agents, "@kell"]} onSend={(t, a, f) => void submit(t, a, f)} enterToSend={chatPrefs.enterToSend} />
      </section>
      <input ref={importFile} type="file" accept="application/json,.json" className="visually-hidden" aria-label="Choose a history file to import" onChange={(e) => void importHistory(e)} />
      <SettingsDialog
        open={settings.open} section={settings.section} room={room}
        onSection={(section) => setSettings((x) => ({ ...x, section }))} onClose={() => setSettings((x) => ({ ...x, open: false }))}
        chatPrefs={chatPrefs} onChatPrefs={setChatPrefs} notifyPrefs={prefs} onNotifyPrefs={setPrefs}
        data={{ onDownload: () => void download(), onImport: () => importFile.current?.click(), onDelete: () => void wipe(),
          folder: { supported: folderBackupSupported(), name: folder, onChoose: () => void chooseBackupFolder(), onRestore: () => void restoreFromFolder(), onStop: () => { void forgetFolder(folderStore, subject); setFolder(null); } } }}
        agents={{ modelText: backing?.text ?? "", modelReady: backing?.ready ?? false, count, paused, onPause: (v) => broker.setPaused(v), maxHops: MAX_DEPTH }}
        account={{ name: accountName, onSignOut: () => void signOut().then(() => { window.location.assign("/"); }) }}
      />
    </div>
    </CanvasContext.Provider>
    </FilesContext.Provider>
  );
}
