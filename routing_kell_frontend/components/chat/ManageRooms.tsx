"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { RoomMember, RoomsApi } from "@/lib/chat/rooms-api";

type Role = "owner" | "admin" | "member";

export interface ManageRoomsProps {
  open: boolean; onClose: () => void;
  rooms: string[]; initial: string;
  /** What you are in each synced room; a room that is not on your account yet has no entry. */
  roles: Record<string, Role | undefined>;
  rapi: RoomsApi;
  /** The server id for a room label, or undefined while the room only exists on this device. */
  roomId: (label: string) => string | undefined;
  /** Dissolve (owner) or leave (anyone else) a room; the parent asks the person to confirm. */
  onDissolve: (label: string) => Promise<void> | void;
  /** Makes a room and switches to it, or says why it cannot. */
  onCreate: (name: string) => { id: string } | { error: string };
}

/**
 * "Manage rooms", opened from the menu: pick a room on the left, then see and change who is in it. The owner promotes people to
 * admin or back to member, removes people and dissolves the room; an admin invites people and removes plain members; everyone else
 * can see who is here and leave. The server decides all of it (services/rooms.py), so a button hidden here is a courtesy, not the
 * protection. Same native <dialog> behaviour as Settings: focus stays inside, Escape closes, the page behind is inert.
 */
export default function ManageRooms(p: ManageRoomsProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [room, setRoom] = useState(p.initial);
  const [members, setMembers] = useState<RoomMember[] | null>(null);
  const [error, setError] = useState("");
  const [invite, setInvite] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState("");
  const [newName, setNewName] = useState("");
  const [newError, setNewError] = useState("");

  const sid = p.roomId(room);
  const role = p.roles[room] ?? "owner"; // a room only on this device is yours alone
  const isOwner = role === "owner";
  const canManage = role === "owner" || role === "admin";

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (p.open && !d.open) { setRoom(p.rooms.includes(p.initial) ? p.initial : p.rooms[0] ?? ""); d.showModal(); }
    if (!p.open && d.open) d.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.open]);

  const load = useCallback(() => {
    setInvite(null); setCopied(false); setError("");
    if (!sid) { setMembers([]); return () => {}; }
    let live = true;
    setMembers(null);
    p.rapi.members(sid).then((m) => { if (live) setMembers(m); }).catch((e) => { if (live) { setMembers([]); setError(e instanceof Error ? e.message : "Couldn't load the people in this room."); } });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, p.rapi]);
  useEffect(() => (p.open ? load() : undefined), [p.open, load]);

  function close() {
    const d = dialog.current;
    if (!d || !d.open) { p.onClose(); return; }
    const calm = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    d.setAttribute("data-closing", "");
    window.setTimeout(() => { d.removeAttribute("data-closing"); p.onClose(); }, calm ? 0 : 240);
  }

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : "That didn't work.");
  const people = (members ?? []).filter((m) => m.kind === "person");

  async function setRole(m: RoomMember, next: "admin" | "member") {
    if (!sid || !m.sub) return;
    setError(""); setBusy(m.sub);
    try { await p.rapi.setMemberRole(sid, m.sub, next); setMembers((prev) => (prev ?? []).map((x) => (x.sub === m.sub ? { ...x, role: next } : x))); }
    catch (e) { fail(e); } finally { setBusy(""); }
  }
  async function remove(m: RoomMember) {
    if (!sid || !m.sub) return;
    if (!window.confirm(`Remove ${m.name ?? "this person"} from ${room}?`)) return;
    setError(""); setBusy(m.sub);
    try { await p.rapi.removeMember(sid, m.sub); setMembers((prev) => (prev ?? []).filter((x) => x.sub !== m.sub)); }
    catch (e) { fail(e); } finally { setBusy(""); }
  }
  async function makeInvite() {
    if (!sid) return;
    setError("");
    try {
      const inv = await p.rapi.createInvite(sid, 7, 20);
      setInvite(`${window.location.origin}/room/join?code=${encodeURIComponent(inv.code)}`); setCopied(false);
    } catch (e) { fail(e); }
  }
  async function copy() { try { await navigator.clipboard.writeText(invite ?? ""); setCopied(true); } catch { setCopied(false); } }
  function create(e: React.FormEvent) {
    e.preventDefault();
    const r = p.onCreate(newName);
    if ("error" in r) { setNewError(r.error); return; }
    setNewName(""); setNewError(""); setRoom(r.id);
  }
  async function dissolve() { setBusy("room"); try { await p.onDissolve(room); } finally { setBusy(""); } }

  // A person can be changed or removed by someone above them: the owner by anyone's rank but their own, an admin only for plain members.
  const canRemove = (m: RoomMember) => !m.me && m.role !== "owner" && (isOwner || (role === "admin" && m.role === "member"));

  return (
    <dialog ref={dialog} className="dg-settings dg-manage" aria-labelledby="dg-manage-title" onCancel={(e) => { e.preventDefault(); close(); }} onClick={(e) => { if (e.target === dialog.current) close(); }}>
      <div className="dg-set-frame">
        <div className="dg-set-head">
          <h2 id="dg-manage-title">Manage rooms</h2>
          <button type="button" className="dg-set-x" onClick={close} aria-label="Close">Done</button>
        </div>
        <div className="dg-set-body">
          <nav className="dg-set-nav" aria-label="Rooms">
            <form className="dg-newroom" onSubmit={create}>
              <label htmlFor="dg-manage-new">New room</label>
              <input id="dg-manage-new" value={newName} onChange={(e) => { setNewName(e.target.value); setNewError(""); }} placeholder="design-review" maxLength={30} autoComplete="off" />
              {newError && <p className="dg-err" role="alert">{newError}</p>}
              <button type="submit" className="btn-line dg-mini" disabled={!newName.trim()}>+ Create room</button>
            </form>
            {p.rooms.map((r) => (
              <button key={r} type="button" aria-selected={r === room} onClick={() => setRoom(r)}>
                #{r.slice(1)}{p.roles[r] && p.roles[r] !== "member" && <span className="dg-badge" data-kind="tab" style={{ marginLeft: 8 }}>{p.roles[r]}</span>}
              </button>
            ))}
          </nav>
          <div className="dg-set-panel">
            {!room ? <p className="dg-set-lead">You have no rooms yet.</p> : (
              <section key={room}>
                <h3>{room}</h3>
                <p className="dg-set-lead">
                  {!sid ? "This room only exists on this device so far, so it has no other people. It syncs to your account when you send a message."
                    : isOwner ? "You own this room. You can make people admins, remove them, and dissolve the room."
                    : role === "admin" ? "You are an admin here. You can invite people and remove plain members."
                    : "You are a member of this room. You can see who is here, or leave."}
                </p>

                {sid && (
                  <fieldset className="dg-set-group">
                    <legend>People</legend>
                    {members === null ? <p className="dg-note" style={{ padding: 0 }}>Loading…</p> : (
                      <ul className="dg-manage-people">
                        {people.map((m) => (
                          <li key={m.sub ?? m.agent_id}>
                            <span className="dg-handle">{m.me ? "You" : m.name ?? "A member"}</span>
                            {isOwner && !m.me && m.role !== "owner" ? (
                              <select aria-label={`Role of ${m.name ?? "this person"}`} value={m.role} disabled={busy === m.sub} onChange={(e) => void setRole(m, e.target.value as "admin" | "member")}>
                                <option value="member">Member</option>
                                <option value="admin">Admin</option>
                              </select>
                            ) : <span className="dg-badge" data-kind="tab">{m.role}</span>}
                            {canRemove(m) && <button type="button" className="btn-line dg-mini-btn" disabled={busy === m.sub} onClick={() => void remove(m)}>Remove</button>}
                          </li>
                        ))}
                      </ul>
                    )}
                  </fieldset>
                )}

                {sid && canManage && (
                  <fieldset className="dg-set-group">
                    <legend>Invite</legend>
                    {!invite ? <div><button type="button" className="btn-line dg-mini-btn" onClick={() => void makeInvite()}>Create invite link</button></div> : (
                      <div className="dg-form" role="status" style={{ margin: 0 }}>
                        <label htmlFor="dg-manage-invite">Invite link</label>
                        <input id="dg-manage-invite" readOnly value={invite} onFocus={(e) => e.currentTarget.select()} />
                        <p className="dg-note" style={{ padding: 0 }}>Anyone with a keळ account who opens this link can join {room}. It works 20 times and expires in 7 days. Share it only with people you want here.</p>
                        <div className="dg-form-row"><button type="button" className="btn-ink dg-mini-btn" onClick={() => void copy()}><span>{copied ? "Copied" : "Copy link"}</span></button><button type="button" className="btn-line dg-mini-btn" onClick={() => setInvite(null)}>Done</button></div>
                      </div>
                    )}
                  </fieldset>
                )}

                {error && <p className="dg-err" role="alert" style={{ margin: 0 }}>{error}</p>}

                <fieldset className="dg-set-group dg-danger">
                  <legend>{isOwner || !sid ? "Dissolve" : "Leave"}</legend>
                  <p className="dg-note" style={{ padding: 0 }}>
                    {!sid ? "Removes this room and its history from this device."
                      : isOwner ? "Deletes the room for everyone: its history, people and invite links. This can't be undone."
                      : "Takes the room off your account. The others keep it."}
                  </p>
                  <div><button type="button" className="btn-line dg-mini-btn dg-danger-btn" disabled={busy === "room" || p.rooms.length < 2} onClick={() => void dissolve()}>{isOwner || !sid ? "Dissolve room" : "Leave room"}</button></div>
                  {p.rooms.length < 2 && <p className="dg-note" style={{ padding: 0 }}>You need at least one other room before you can remove this one.</p>}
                </fieldset>
              </section>
            )}
          </div>
        </div>
      </div>
    </dialog>
  );
}
