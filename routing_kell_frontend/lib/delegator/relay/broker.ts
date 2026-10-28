import { MAX_DEPTH } from "../types";
import type { AgentActivity, GroupMessage, RelayEvent, Room, TransportPacket } from "../types";

type Inbox = (packet: TransportPacket) => void | Promise<void>;
type Listener = (e: RelayEvent) => void;

/**
 * A store-and-forward relay that keeps nothing. A packet waits in `pending` only while its recipient has no inbox
 * attached, and is removed the moment it is handed over. There is no log, no history and no disk; whoever needs the
 * conversation keeps their own copy. In-memory and in-tab on purpose: this is a dev sandbox, not a network service.
 *
 * Loop guard: a message carries `depthCounter`, the agent-to-agent hops so far. At MAX_DEPTH the relay still shows the
 * message to people but will not wake any agent with it, and says so, until a human sends a new message (depth 0).
 */
export class Broker {
  private inboxes = new Map<string, Inbox>();
  private pending = new Map<string, TransportPacket[]>();
  private listeners = new Set<Listener>();
  private activity = new Map<string, AgentActivity>();
  private rooms = new Map<string, Room>();
  private agents = new Set<string>();
  paused = false;
  private seq = 0;

  newId(): string { return `m${Date.now().toString(36)}${(this.seq++).toString(36)}`; }

  on(l: Listener): () => void { this.listeners.add(l); return () => this.listeners.delete(l); }
  private emit(e: RelayEvent) { for (const l of [...this.listeners]) l(e); }

  /** A line from the room itself (not from anyone): why something did not happen. */
  notice(roomId: string, text: string) { this.emit({ kind: "notice", roomId, text }); }

  addRoom(room: Room) { this.rooms.set(room.id, { ...room, members: [...room.members] }); }
  room(id: string): Room | undefined { return this.rooms.get(id); }
  addMember(roomId: string, handle: string) {
    const r = this.rooms.get(roomId);
    if (!r || r.members.includes(handle)) return;
    r.members.push(handle);
    this.emit({ kind: "members", roomId, members: [...r.members] });
  }

  /** Takes a member out of the room. An agent is also forgotten: it is no longer woken and nothing waits for it. */
  removeMember(roomId: string, handle: string) {
    const r = this.rooms.get(roomId);
    if (!r || !r.members.includes(handle)) return;
    r.members = r.members.filter((h) => h !== handle);
    this.agents.delete(handle);
    this.inboxes.delete(handle);
    this.pending.delete(handle);
    this.activity.delete(handle);
    this.emit({ kind: "members", roomId, members: [...r.members] });
  }

  /** Marks a handle as an agent: only agents are held back by the depth guard and the pause. */
  registerAgent(handle: string) { this.agents.add(handle); if (!this.activity.has(handle)) this.activity.set(handle, "idle"); }
  isAgent(handle: string) { return this.agents.has(handle); }
  activityOf(handle: string): AgentActivity { return this.activity.get(handle) ?? "idle"; }
  setActivity(handle: string, a: AgentActivity) {
    if (this.activity.get(handle) === a) return;
    this.activity.set(handle, a);
    this.emit({ kind: "activity", handle, activity: a });
  }

  setPaused(p: boolean) {
    if (this.paused === p) return;
    this.paused = p;
    this.emit({ kind: "paused", paused: p });
  }

  /** Attach a recipient's inbox; anything waiting for it is handed over and purged. */
  attach(handle: string, inbox: Inbox): () => void {
    this.inboxes.set(handle, inbox);
    const waiting = this.pending.get(handle) ?? [];
    this.pending.delete(handle);
    for (const p of waiting) void this.hand(handle, p);
    return () => { if (this.inboxes.get(handle) === inbox) this.inboxes.delete(handle); };
  }

  pendingCount(): number { let n = 0; for (const q of this.pending.values()) n += q.length; return n; }

  private async hand(handle: string, packet: TransportPacket) {
    const inbox = this.inboxes.get(handle);
    if (!inbox) { this.pending.set(handle, [...(this.pending.get(handle) ?? []), packet]); return; }
    await inbox(packet);
  }

  /** Recipients of a message: "@all" means every member except the sender. */
  recipientsOf(m: GroupMessage): string[] {
    const members = this.rooms.get(m.roomId)?.members ?? [];
    const to = m.recipients.includes("@all") ? members : m.recipients;
    return [...new Set(to)].filter((h) => h !== m.sender.handle);
  }

  /** Sends a message. People always see it; agents are woken only if the room is running and the depth allows. */
  async send(m: GroupMessage): Promise<{ delivered: string[]; held: string[] }> {
    const delivered: string[] = [];
    const held: string[] = [];
    this.emit({ kind: "message", message: m });
    const senderIsAgent = this.isAgent(m.sender.handle);
    let heldByPause = false;
    for (const to of this.recipientsOf(m)) {
      if (this.isAgent(to)) {
        if (this.paused) { held.push(to); heldByPause = true; continue; }
        if (m.depthCounter >= MAX_DEPTH) { held.push(to); continue; }
      }
      delivered.push(to);
      if (senderIsAgent) this.setActivity(m.sender.handle, "delivering");
      await this.hand(to, { to, message: m });
    }
    if (senderIsAgent) this.setActivity(m.sender.handle, "idle");
    if (held.length) {
      this.emit({
        kind: "notice", roomId: m.roomId,
        text: heldByPause ? "Room is paused. Agents were not woken." : "Max agent delegation depth reached. Human approval required to continue.",
      });
    }
    return { delivered, held };
  }
}
