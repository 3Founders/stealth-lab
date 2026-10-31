"use client";
import { useEffect, useRef, useState } from "react";
import { canChangeRole, canDissolve, canRemove, type Role } from "@/lib/chat/rooms";

export interface ManageRoomsProps {
  open: boolean; onClose: () => void;
  rooms: Array<{ id: string; members: string[] }>; initial: string;
  /** The handle you are sending as, and everyone's role per room (people only; agents have none). */
  me: string; roles: Record<string, Record<string, Role>>; isAgent: (handle: string) => boolean;
  onSetRole: (room: string, handle: string, role: "admin" | "member") => void;
  onRemove: (room: string, handle: string) => void;
  onDissolve: (room: string) => void;
  /** Makes a room and switches to it, or says why it cannot. */
  onCreate: (name: string) => { id: string } | { error: string };
}

/** "Manage rooms" from the menu: pick a room, then see who is in it, change roles, remove people and agents, and dissolve or leave. */
export default function ManageRooms(p: ManageRoomsProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [room, setRoom] = useState(p.initial);
  const [newName, setNewName] = useState("");
  const [newError, setNewError] = useState("");

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (p.open && !d.open) { setRoom(p.rooms.some((r) => r.id === p.initial) ? p.initial : p.rooms[0]?.id ?? ""); d.showModal(); }
    if (!p.open && d.open) d.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.open]);

  const current = p.rooms.find((r) => r.id === room);
  const mine = current ? p.roles[room]?.[p.me] : undefined;
  const people = (current?.members ?? []).filter((h) => !p.isAgent(h));
  const agents = (current?.members ?? []).filter((h) => p.isAgent(h));

  function create(e: React.FormEvent) {
    e.preventDefault();
    const r = p.onCreate(newName);
    if ("error" in r) { setNewError(r.error); return; }
    setNewName(""); setNewError(""); setRoom(r.id);
  }

  return (
    <dialog ref={dialog} className="dg-manage" aria-labelledby="dg-manage-title" onCancel={(e) => { e.preventDefault(); p.onClose(); }} onClick={(e) => { if (e.target === dialog.current) p.onClose(); }}>
      <div className="dg-manage-frame">
        <div className="dg-manage-head">
          <h2 id="dg-manage-title">Manage rooms</h2>
          <button type="button" className="dg-manage-x" onClick={p.onClose} aria-label="Close">Done</button>
        </div>
        <div className="dg-manage-body">
          <nav className="dg-manage-nav" aria-label="Rooms">
            <form className="dg-newroom" onSubmit={create}>
              <label htmlFor="dg-manage-new">New room</label>
              <input id="dg-manage-new" value={newName} onChange={(e) => { setNewName(e.target.value); setNewError(""); }} placeholder="design-review" maxLength={30} autoComplete="off" />
              {newError && <p className="dg-err" role="alert">{newError}</p>}
              <button type="submit" className="dg-mini" disabled={!newName.trim()}>+ Create room</button>
            </form>
            {p.rooms.map((r) => <button key={r.id} type="button" aria-selected={r.id === room} onClick={() => setRoom(r.id)}>{r.id}</button>)}
          </nav>
          <div className="dg-manage-panel">
            {!current ? <p>You have no rooms yet.</p> : (
              <section key={room}>
                <h3>{room}</h3>
                <p className="dg-manage-lead">
                  {!mine ? `${p.me} is not in this room.`
                    : mine === "owner" ? `${p.me} owns this room: can make people admins, remove them, and dissolve the room.`
                    : mine === "admin" ? `${p.me} is an admin here: can remove plain members.`
                    : `${p.me} is a member here: can see who is in the room, or leave.`}
                </p>
                <h4>People</h4>
                <ul className="dg-manage-people">
                  {people.map((h) => {
                    const role = p.roles[room]?.[h];
                    const self = h === p.me;
                    return (
                      <li key={h}>
                        <span className="dg-handle">{self ? `${h} (you)` : h}</span>
                        {canChangeRole(mine, role) ? (
                          <select aria-label={`Role of ${h}`} value={role} onChange={(e) => p.onSetRole(room, h, e.target.value as "admin" | "member")}>
                            <option value="member">Member</option>
                            <option value="admin">Admin</option>
                          </select>
                        ) : <span className="dg-badge">{role ?? "member"}</span>}
                        {canRemove(mine, role, self) && <button type="button" className="dg-mini" onClick={() => p.onRemove(room, h)}>{self ? "Leave" : "Remove"}</button>}
                      </li>
                    );
                  })}
                </ul>
                {agents.length > 0 && (
                  <>
                    <h4>Agents</h4>
                    <ul className="dg-manage-people">
                      {agents.map((h) => (
                        <li key={h}>
                          <span className="dg-handle">{h}</span><span className="dg-badge">agent</span>
                          {(mine === "owner" || mine === "admin") && <button type="button" className="dg-mini" onClick={() => p.onRemove(room, h)}>Remove</button>}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                <h4>Dissolve</h4>
                <p className="dg-manage-lead">Removes the room for everyone in this tab. Nothing was ever saved, so there is no history to lose.</p>
                <button type="button" className="dg-mini dg-danger" disabled={!canDissolve(mine) || p.rooms.length < 2} onClick={() => p.onDissolve(room)}>Dissolve room</button>
                {!canDissolve(mine) && <p className="dg-manage-lead">Only the owner can dissolve a room.</p>}
                {canDissolve(mine) && p.rooms.length < 2 && <p className="dg-manage-lead">You need at least one other room first.</p>}
              </section>
            )}
          </div>
        </div>
      </div>
    </dialog>
  );
}
