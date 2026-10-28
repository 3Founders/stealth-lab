"use client";
import { MessageScroller, useMessageScrollerScrollable } from "@shadcn/react/message-scroller";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import Composer from "@/components/delegator/Composer";
import { useSpringScroll } from "@/components/delegator/useSpringScroll";
import { recordUse, withinCap, type KeyValue } from "@/lib/byok/spend";
import { Vault, idbStore } from "@/lib/byok/vault";
import { HUMAN, MAX_AGENTS, ROOM_ID, newAgentHandle } from "@/lib/delegator/agents";
import { assistantIdentityFor, attachAssistant, type AssistantDeps } from "@/lib/delegator/adapters/assistant";
import { parseCommand, recipientsFor, toMcpPayload } from "@/lib/delegator/adapters/mcp";
import { describeBacking, makeResolver, type Backing } from "@/lib/delegator/adapters/resolve";
import { Broker } from "@/lib/delegator/relay/broker";
import type { AgentActivity, AgentIdentity, GroupMessage } from "@/lib/delegator/types";
import { MAX_DEPTH } from "@/lib/delegator/types";
import { groupMessages, initials } from "@/lib/delegator/ui";
import { getAccessToken } from "@/lib/session";

const LABEL: Record<AgentActivity, string> = { idle: "Idle", processing: "Working", delivering: "Sending" };

type Line = { kind: "msg"; m: GroupMessage } | { kind: "notice"; id: string; text: string };

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

/**
 * Your room: you and the agents you add. Every agent is a real, separate model call: the model you chose under Models (your own
 * key, or the opt-in keळ models), one instance per agent. There are no pretend people, rooms or agents here. `subject` is the
 * signed-in account. `brand` is the logo at the top of the sidebar and `back` a Back control under it.
 */
export default function RoomView({ brand, back, subject }: { brand?: React.ReactNode; back?: React.ReactNode; subject: string }) {
  const broker = useMemo(() => { const b = new Broker(); b.addRoom({ id: ROOM_ID, title: "Your room", members: [HUMAN.handle] }); return b; }, []);
  const vault = useMemo(() => new Vault(idbStore(), subject), [subject]);
  const kv = useMemo(localKv, []);

  const [agents, setAgents] = useState<string[]>([]);
  const [lines, setLines] = useState<Line[]>([]);
  const [activity, setActivity] = useState<Record<string, AgentActivity>>({});
  const [streaming, setStreaming] = useState<Record<string, string>>({});
  const [paused, setPaused] = useState(false);
  const [backing, setBacking] = useState<Backing | undefined>(undefined);
  const controllers = useRef(new Map<string, AbortController>());
  const detachers = useRef(new Map<string, () => void>());
  // The history an agent reads is kept here, updated the moment a line arrives: React state lags a render behind, and an agent that
  // starts first would send the model a request with no message from the person in it.
  const linesRef = useRef<Line[]>(lines);
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const { scrollToEnd, follow } = useSpringScroll(viewportRef, contentRef, ROOM_ID);

  const deps = useMemo<AssistantDeps>(() => ({
    resolve: makeResolver({ vault, token: getAccessToken, kv, subject }),
    allowed: () => withinCap(kv, subject),
    used: () => void recordUse(kv, subject),
    history: () => linesRef.current.flatMap((l) => (l.kind === "msg" ? [l.m] : [])),
    onStart: (_r, handle, c) => { controllers.current.set(handle, c); setStreaming((p) => ({ ...p, [handle]: "" })); },
    onDelta: (_r, handle, text) => setStreaming((p) => ({ ...p, [handle]: text })),
    onEnd: (_r, handle) => { controllers.current.delete(handle); setStreaming((p) => { const n = { ...p }; delete n[handle]; return n; }); },
  }), [vault, kv, subject]);

  const push = (l: Line) => {
    const p = linesRef.current;
    if (l.kind === "msg" && p.some((x) => x.kind === "msg" && x.m.id === l.m.id)) return;
    linesRef.current = [...p, l];
    setLines(linesRef.current);
  };

  const addAgent = (handle: string) => {
    broker.addMember(ROOM_ID, handle);
    detachers.current.set(handle, attachAssistant(broker, deps, handle));
    setAgents((p) => [...p, handle]);
  };

  useEffect(() => {
    const off = broker.on((e) => {
      if (e.kind === "message") push({ kind: "msg", m: e.message });
      else if (e.kind === "notice") push({ kind: "notice", id: broker.newId(), text: e.text });
      else if (e.kind === "activity") setActivity((p) => ({ ...p, [e.handle]: e.activity }));
      else if (e.kind === "paused") setPaused(e.paused);
    });
    return () => { off(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [broker]);

  // The room starts with one agent. (A ref guards the development double-run of effects.)
  const started = useRef(false);
  useEffect(() => {
    if (!started.current) { started.current = true; addAgent("@assistant-1"); }
    const all = detachers.current;
    return () => { all.forEach((d) => d()); all.clear(); started.current = false; setAgents([]); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [broker, deps]);

  useEffect(() => {
    let live = true;
    describeBacking({ vault, kv, subject }).then((b) => { if (live) setBacking(b); }).catch(() => { if (live) setBacking({ ready: false, text: "Add a key to chat here" }); });
    return () => { live = false; };
  }, [vault, kv, subject]);

  const roomMembers = [HUMAN.handle, ...agents];
  const working = agents.filter((h) => (activity[h] ?? "idle") !== "idle" || streaming[h] !== undefined);

  // Notices split the timeline; messages between them are grouped by sender.
  const blocks = useMemo(() => {
    const out: Array<{ kind: "run"; run: ReturnType<typeof groupMessages>[number] } | { kind: "notice"; id: string; text: string }> = [];
    let buf: GroupMessage[] = [];
    const flush = () => { for (const run of groupMessages(buf)) out.push({ kind: "run", run }); buf = []; };
    for (const l of lines) { if (l.kind === "msg") buf.push(l.m); else { flush(); out.push(l); } }
    flush();
    return out;
  }, [lines]);

  const liveText = JSON.stringify(streaming);
  useEffect(() => { scrollToEnd({ instant: true }); }, [scrollToEnd]);
  useEffect(() => { follow(); }, [blocks, liveText, follow]);

  const note = (t: string) => push({ kind: "notice", id: broker.newId(), text: t });

  const create = (name?: string) => {
    const r = newAgentHandle(agents, name);
    if ("error" in r) return note(r.error);
    addAgent(r.handle);
    note(`${r.handle} joined the room.`);
  };

  const submit = async (line: string) => {
    const c = parseCommand(line);
    if (c) {
      if (c.cmd === "error") return note(c.message);
      if (c.cmd === "stop") {
        broker.setPaused(true);
        controllers.current.forEach((ctl) => ctl.abort());
        return note("/stop: paused. No agent will be woken until you resume.");
      }
      return create(c.name);
    }
    const m: GroupMessage = {
      id: broker.newId(), roomId: ROOM_ID, sender: HUMAN, recipients: recipientsFor(line),
      payload: toMcpPayload(line), timestamp: Date.now(), depthCounter: 0,
    };
    await broker.send(m);
  };

  const streamingHandles = Object.keys(streaming);

  return (
    <div className="dg-shell" data-paused={paused}>
      <aside className="dg-side" aria-label="Your room and its agents">
        {brand && <div className="dg-brand">{brand}</div>}
        {back && <div className="dg-backrow">{back}</div>}
        <h2>Room</h2>
        <nav>
          <button type="button" className="dg-room" aria-current="page"><span aria-hidden="true">#</span>{ROOM_ID.slice(1)}</button>
        </nav>
        <h2>In this room</h2>
        <ul className="dg-members">
          <li>
            <span className="dg-dot" data-a="human" aria-hidden="true" />
            <span className="dg-handle">{HUMAN.handle}</span>
          </li>
          {agents.map((h) => {
            const a = streaming[h] !== undefined ? "processing" : (activity[h] ?? "idle");
            return (
              <li key={h}>
                <span className="dg-dot" data-a={a} aria-hidden="true" />
                <span className="dg-handle">{h}</span>
                <span className="dg-badge">agent</span>
                {backing && !backing.ready
                  ? <Link className="dg-state dg-addkey" href="/account/models">{backing.text}</Link>
                  : <span className="dg-state" data-a={a}>{a === "idle" ? (backing?.text ?? "") : LABEL[a]}</span>}
              </li>
            );
          })}
        </ul>
        <button type="button" className="btn-line dg-add" onClick={() => create()} disabled={agents.length >= MAX_AGENTS}>+ Add agent</button>
        <p className="dg-note">
          Each agent is a separate instance of the model you chose under <Link href="/account/models" style={{ textDecoration: "underline" }}>Models</Link>.
          Your messages go to that provider{backing?.text.startsWith("keळ") ? ": public material only" : " with your own key"}.
        </p>
      </aside>

      <section className="dg-main" aria-label="Your room">
        <header className="dg-head">
          <h1>{ROOM_ID}</h1>
          <span className="dg-limit">Agents hand off at most {MAX_DEPTH} times before a person must step in.</span>
          <button type="button" className="dg-breaker" aria-pressed={paused} onClick={() => broker.setPaused(!paused)}>
            <span aria-hidden="true">{paused ? "▶" : "⏸"}</span>{paused ? "Resume agents" : "Pause agents"}
          </button>
        </header>
        {paused && <div className="dg-banner" role="status">Paused. Agents will not be woken until you resume.</div>}

        <MessageScroller.Provider autoScroll={false} defaultScrollPosition="end">
          <MessageScroller.Root className="dg-scroll">
            <MessageScroller.Viewport ref={viewportRef} data-lenis-prevent className="dg-viewport" aria-label="Messages in your room">
              <MessageScroller.Content ref={contentRef} className="dg-content">
                {blocks.length === 0 && streamingHandles.length === 0 && (
                  <p className="dg-empty">No messages yet. Say hello: <code>@assistant-1 hello</code>. Add more agents with the button on the left or <code>/add-agent</code>.</p>
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
                {streamingHandles.map((h) => (
                  <MessageScroller.Item key={`live-${h}`} className="dg-run" data-agent="true">
                    <Avatar who={assistantIdentityFor(h)} />
                    <div className="dg-col">
                      <div className="dg-meta"><b>{h}</b><span className="dg-badge">agent</span></div>
                      <div className="dg-body"><p>{streaming[h] || "…"}</p></div>
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
          {streamingHandles.length > 0 && (
            <button type="button" className="dg-stop" onClick={() => controllers.current.forEach((c) => c.abort())}>Stop</button>
          )}
        </div>
        <Composer handles={roomMembers.filter((h) => h !== HUMAN.handle)} onSend={(t) => void submit(t)} />
      </section>
    </div>
  );
}
