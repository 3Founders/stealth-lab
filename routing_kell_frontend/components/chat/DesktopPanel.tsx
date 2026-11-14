"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import PanelShell from "@/components/chat/PanelShell";
import Markdown from "@/components/chat/Markdown";
import {
  isActive, LEVEL_LABEL, type DesktopApi, type DesktopAsk, type DesktopOverview, type DesktopSession, type GrantLevel, type Grants,
} from "@/lib/chat/desktop-api";

/**
 * "Desktop sessions": the kell desktop app's sessions, from the rooms (backend services/desktop_remote.py). Pick one of yours and talk
 * to it as if you were at that computer (a remote control); teammates' sessions they shared with you are listed too. What waits on you
 * (a teammate's ask to approve, a permission one of your desktop runs asks for) is at the top, and at the bottom you choose who may
 * call your desktop from a room and what their asks may do. In a room, "@kell ..." finds the right session by itself.
 */
const POLL_MS = 2000;

const ago = (iso: string | null) => {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};
const STATUS: Record<DesktopAsk["status"], string> = {
  needs_approval: "waiting for approval", queued: "sent", running: "working…", waiting_permission: "needs your permission",
  done: "done", failed: "failed", denied: "not approved", cancelled: "cancelled",
};

export default function DesktopPanel({ open, onClose, api, onPending }: {
  open: boolean; onClose: () => void; api: DesktopApi; onPending?: (n: number) => void;
}) {
  const [ov, setOv] = useState<DesktopOverview | null>(null);
  const [grants, setGrants] = useState<Grants | null>(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState<DesktopSession | null>(null);

  const load = useCallback(async () => {
    try {
      const [o, g] = await Promise.all([api.overview(), api.grants()]);
      setOv(o); setGrants(g); setError("");
      onPending?.(o.pending.length);
    } catch (e) { setError((e as Error).message); }
  }, [api, onPending]);

  useEffect(() => {
    if (!open) return;
    void load();
    const t = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(t);
  }, [open, load]);

  return (
    <PanelShell open={open} label="Desktop sessions">
      <div className="dg-canvas-head">
        <div className="dg-canvas-headrow">
          {picked
            ? <button type="button" className="dg-cv-chip" onClick={() => setPicked(null)}>← All sessions</button>
            : <b className="dg-dk-title">Desktop sessions</b>}
        </div>
        <button type="button" className="dg-cv-close" aria-label="Close" onClick={onClose}>
          <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
        </button>
      </div>
      <div className="dg-canvas-body dg-dk-body">
        {error && <p className="dg-dk-error" role="alert">{error}</p>}
        {picked
          ? <SessionView api={api} session={picked} mine={!ov || ov.mine.some((s) => s.session_id === picked.session_id && s.device_id === picked.device_id)} />
          : <Overview ov={ov} grants={grants} api={api} onPick={setPicked} reload={load} />}
      </div>
    </PanelShell>
  );
}

function Overview({ ov, grants, api, onPick, reload }: {
  ov: DesktopOverview | null; grants: Grants | null; api: DesktopApi; onPick: (s: DesktopSession) => void; reload: () => Promise<void>;
}) {
  if (!ov) return <p className="dg-dk-dim">Loading…</p>;
  const act = async (f: () => Promise<unknown>) => { try { await f(); } finally { await reload(); } };
  return (
    <>
      {ov.pending.length > 0 && (
        <section className="dg-dk-sec" aria-label="Waiting on you">
          <h3>Waiting on you</h3>
          {ov.pending.map((a) => (
            <div key={a.id} className="dg-dk-card" data-tone="warn">
              {a.status === "needs_approval" ? (
                <>
                  <p><b>@{a.asker_name}</b> asks your desktop: “{a.text.slice(0, 300)}”</p>
                  <div className="dg-dk-row">
                    <button type="button" className="btn-ink dg-mini-btn" onClick={() => void act(() => api.approve(a.id, true))}><span>Approve</span></button>
                    <button type="button" className="btn-line dg-mini-btn" onClick={() => void act(() => api.approve(a.id, false))}>Deny</button>
                  </div>
                </>
              ) : (
                <>
                  <p>Your desktop wants to <b>{a.waiting?.tool || "run a tool"}</b>{a.waiting?.detail ? <>: <code>{a.waiting.detail}</code></> : null}
                    {a.level !== "owner" && a.asker_name ? <> (for @{a.asker_name})</> : null}</p>
                  <div className="dg-dk-row">
                    <button type="button" className="btn-ink dg-mini-btn" onClick={() => void act(() => api.permission(a.id, "once"))}><span>Allow once</span></button>
                    <button type="button" className="btn-line dg-mini-btn" onClick={() => void act(() => api.permission(a.id, "always"))}>Always</button>
                    <button type="button" className="btn-line dg-mini-btn" onClick={() => void act(() => api.permission(a.id, "reject"))}>Deny</button>
                  </div>
                </>
              )}
            </div>
          ))}
        </section>
      )}

      <section className="dg-dk-sec" aria-label="My sessions">
        <h3>My sessions</h3>
        {ov.devices.length === 0 && (
          <p className="dg-dk-dim">No desktop yet. Install the <a href="/download">kell desktop app</a> and sign in to the rooms there; its sessions show up here.</p>
        )}
        {ov.devices.map((d) => (
          <div key={d.id} className="dg-dk-device">
            <div className="dg-dk-devhead"><span className="dg-dk-dot" data-on={d.online || undefined} aria-hidden="true" />{d.name || "Desktop"}
              <span className="dg-dk-dim">{d.online ? "online" : `offline · seen ${ago(d.last_seen)}`}</span></div>
            {ov.mine.filter((s) => s.device_id === d.id).map((s) => <SessionRow key={s.session_id} s={s} onPick={onPick} />)}
          </div>
        ))}
      </section>

      {ov.shared.length > 0 && (
        <section className="dg-dk-sec" aria-label="Shared with you">
          <h3>Shared with you</h3>
          {ov.shared.map((s) => <SessionRow key={`${s.device_id}/${s.session_id}`} s={s} onPick={onPick}
            who={grants?.received.find((g) => g.sub === s.owner)?.username} />)}
        </section>
      )}

      <Access grants={grants} api={api} reload={reload} />
      <p className="dg-dk-dim dg-dk-foot">In a room, just write <code>@kell …</code>: when it is about work in one of these sessions, kell hands it to that session and the answer appears in the room.</p>
    </>
  );
}

function SessionRow({ s, onPick, who }: { s: DesktopSession; onPick: (s: DesktopSession) => void; who?: string }) {
  return (
    <button type="button" className="dg-dk-sess" onClick={() => onPick(s)} title={s.summary || s.title}>
      <span className="nm">{s.title || "Untitled session"}</span>
      <span className="dg-dk-dim">{[who ? `@${who}` : "", s.project, ago(s.updated_at), s.online ? "" : "offline"].filter(Boolean).join(" · ")}</span>
    </button>
  );
}

function Access({ grants, api, reload }: { grants: Grants | null; api: DesktopApi; reload: () => Promise<void> }) {
  const [user, setUser] = useState("");
  const [level, setLevel] = useState<GrantLevel>("answer");
  const [note, setNote] = useState("");
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user.trim()) return;
    try { await api.grant(user.trim().replace(/^@/, ""), level); setUser(""); setNote(""); }
    catch (err) { setNote((err as Error).message); }
    await reload();
  };
  return (
    <section className="dg-dk-sec" aria-label="Who can call my desktop">
      <h3>Who can call my desktop</h3>
      <p className="dg-dk-dim">They can ask your sessions from a room you are both in. A teammate's ask runs in a copy of the session, never in yours.</p>
      {(grants?.given ?? []).map((g) => (
        <div key={g.sub} className="dg-dk-grant">
          <span>@{g.username}</span>
          <select aria-label={`What @${g.username} may do`} value={g.level} onChange={(e) => void api.grant(g.username, e.target.value as GrantLevel).then(reload)}>
            {(Object.keys(LEVEL_LABEL) as GrantLevel[]).map((l) => <option key={l} value={l}>{LEVEL_LABEL[l]}</option>)}
          </select>
          <button type="button" className="dg-cv-chip" onClick={() => void api.revoke(g.sub).then(reload)}>Remove</button>
        </div>
      ))}
      <form className="dg-dk-grant" onSubmit={(e) => void add(e)}>
        <input aria-label="Username" placeholder="username" value={user} onChange={(e) => setUser(e.target.value)} />
        <select aria-label="What they may do" value={level} onChange={(e) => setLevel(e.target.value as GrantLevel)}>
          {(Object.keys(LEVEL_LABEL) as GrantLevel[]).map((l) => <option key={l} value={l}>{LEVEL_LABEL[l]}</option>)}
        </select>
        <button type="submit" className="btn-ink dg-mini-btn" disabled={!user.trim()}><span>Allow</span></button>
      </form>
      {note && <p className="dg-dk-error" role="alert">{note}</p>}
    </section>
  );
}

/** One session, like a chat with it: what was asked of it from here, the answers (live while it works), and a box to ask more. */
function SessionView({ api, session, mine }: { api: DesktopApi; session: DesktopSession; mine: boolean }) {
  const [asks, setAsks] = useState<DesktopAsk[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try { setAsks((await api.asks(session.session_id)).filter((a) => a.device_id === session.device_id).reverse()); }
    catch (e) { setError((e as Error).message); }
  }, [api, session]);
  useEffect(() => { void load(); }, [load]);
  const active = asks.some(isActive);
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(t);
  }, [active, load]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [asks.length, asks.at(-1)?.answer]);

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || sending) return;
    setSending(true); setError("");
    try { await api.ask(session, text.trim()); setText(""); await load(); }
    catch (err) { setError((err as Error).message); }
    finally { setSending(false); }
  };

  return (
    <div className="dg-dk-session">
      <div className="dg-dk-shead">
        <b>{session.title || "Untitled session"}</b>
        <span className="dg-dk-dim">{[session.project, session.device, session.online ? "online" : "offline"].filter(Boolean).join(" · ")}</span>
        {!mine && <span className="dg-dk-dim">A teammate's session: your asks run in a copy of it, with the access they gave you.</span>}
      </div>
      <div className="dg-dk-thread">
        {asks.length === 0 && <p className="dg-dk-dim">Nothing asked from here yet. What you write runs in this session on {mine ? "your" : "their"} desktop{mine ? ", as if you typed it there" : ""}.</p>}
        {asks.map((a) => (
          <div key={a.id} className="dg-dk-turn">
            <div className="dg-dk-q">{a.text}</div>
            <div className="dg-dk-a">
              {a.answer ? <Markdown text={a.answer} /> : null}
              <span className="dg-dk-dim" data-status={a.status}>{a.status === "failed" ? `failed: ${a.error}` : STATUS[a.status]}
                {isActive(a) && <button type="button" className="dg-cv-chip" onClick={() => void api.cancel(a.id).then(load)}>Stop</button>}</span>
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>
      {error && <p className="dg-dk-error" role="alert">{error}</p>}
      <form className="dg-dk-compose" onSubmit={(e) => void send(e)}>
        <textarea aria-label="Ask this session" placeholder={session.online ? "Ask this session…" : "This desktop is offline"} value={text}
          disabled={!session.online} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); (e.currentTarget.form as HTMLFormElement).requestSubmit(); } }} />
        <button type="submit" className="btn-ink dg-mini-btn" disabled={!session.online || !text.trim() || sending}><span>Send</span></button>
      </form>
    </div>
  );
}
