/**
 * Keeps your rooms and chats the same on every device you sign in on (docs/rooms_design.md; server in backend/app/services/rooms.py).
 *
 * Local-first: this device already holds your whole history (lib/chat/store.ts). Sending writes to the local outbox first and
 * works offline; the engine then pushes the outbox, pulls what other devices (and other people) added since its cursor, and tells
 * the screen. The merge is a union by event id: the id of a message is its event id, so a retry, a message echoed back from the
 * server, or the same event arriving twice is one message. Nothing is edited in place, so there is nothing to conflict.
 *
 *  - ROOMS are matched by title: a room on this device with no server copy is created on the server and its history is queued for
 *    upload (adopted); a room on the server you haven't got here appears (a new device, or a room you were invited to); a room
 *    that is gone from the server (deleted by its owner, or you left) is dropped here.
 *  - The engine does not wake agents. A message that comes from another device is only shown: an agent in this tab answers messages
 *    typed on this device, so two open devices never both call a model for one message.
 *  - It polls (about every 2.5 s while a send or reply is in flight, otherwise every 5 s, every 30 s when the tab is hidden, with
 *    backoff while offline) and refreshes at once on `online` and on returning to the tab. Honest limit: no push notifications, and
 *    polling is not instant.
 *  - Open rooms are readable by keळ's server; that is what lets connectors join, and the room says so.
 */
import type { HistoryStore, OutboxItem } from "./store";
import { newEventId, toEvent, toMessage } from "./events";
import { RoomsApiError, type RoomsApi, type ServerRoom } from "./rooms-api";
import type { GroupMessage } from "./types";

export type SyncStatus = "idle" | "syncing" | "offline" | "error";
export interface SyncState { status: SyncStatus; pending: number; lastSyncAt: number | null; error: string | null }

export interface RoomsChange {
  added: Array<{ label: string; id: string; title: string; role: "owner" | "admin" | "member" }>;
  removed: string[];
  /** Per room label: what the list says about it right now. */
  info: Record<string, { unread: number; members: number; last: ServerRoom["last"] | null; role: "owner" | "admin" | "member" }>;
}

export interface SyncHooks {
  /** New messages from the server, already saved on this device and not seen before. */
  onMessages(label: string, messages: GroupMessage[]): void;
  onRooms(change: RoomsChange): void;
  onNotice(label: string | null, text: string): void;
  onState(state: SyncState): void;
}

export interface SyncDeps {
  api: Pick<RoomsApi, "rooms" | "createRoom" | "events" | "append" | "markRead">;
  history: HistoryStore;
  /** The room labels this device has right now. */
  labels(): string[];
  hooks: SyncHooks;
  /** True while something is in flight in the room on screen, to poll faster. */
  busy?(): boolean;
  hidden?(): boolean;
  sleep?(ms: number): Promise<void>;
}

const BATCH = 50;
const PAGE = 200;
const MAP_KEY = "roomMap";
const CURSOR_KEY = "cursors";
const LAMPORT_KEY = "lamport";

export class SyncEngine {
  private running: Promise<void> = Promise.resolve();
  private state: SyncState = { status: "idle", pending: 0, lastSyncAt: null, error: null };
  private stopped = true;
  private wake: (() => void) | null = null;
  private failures = 0;
  private map: Record<string, string> = {};
  private cursors: Record<string, number> = {};
  private lamport = 0;
  private loaded = false;

  constructor(private d: SyncDeps) {}

  // ---- what the screen asks

  /** Writes the message to the outbox (so it survives a reload and works offline) and nudges a sync. */
  async enqueue(label: string, m: GroupMessage): Promise<void> {
    await this.load();
    this.lamport += 1;
    await this.d.history.setMeta(LAMPORT_KEY, this.lamport);
    const event = toEvent(m, await this.d.history.deviceId(), this.lamport);
    await this.d.history.putOutbox({ id: m.id, room: label, event, at: Date.now() });
    this.set({ pending: (await this.d.history.outboxIds()).length });
    this.kick();
  }

  roomId(label: string): string | undefined { return this.map[label]; }

  /** Stop tracking a room that was removed on purpose, so a new room with the same name starts clean. */
  async forget(label: string): Promise<void> {
    await this.load();
    const id = this.map[label];
    delete this.map[label];
    if (id) delete this.cursors[id];
    await this.save();
  }

  async markRead(label: string): Promise<void> {
    await this.load();
    const id = this.map[label];
    const seq = this.cursors[id ?? ""];
    if (id && seq) { try { await this.d.api.markRead(id, seq); } catch { /* the next sync tries again */ } }
  }

  current(): SyncState { return this.state; }

  // ---- lifecycle

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    void this.loop();
  }

  stop(): void { this.stopped = true; this.wake?.(); }

  /** Sync soon (after a send, on coming back online, on returning to the tab). */
  kick(): void { this.wake?.(); }

  private async loop(): Promise<void> {
    while (!this.stopped) {
      await this.syncNow();
      if (this.stopped) break;
      const ms = this.failures ? Math.min(60_000, 2000 * 2 ** Math.min(this.failures, 5)) : this.d.hidden?.() ? 30_000 : this.d.busy?.() ? 2500 : 5000;
      await new Promise<void>((resolve) => {
        const t = setTimeout(resolve, ms);
        this.wake = () => { clearTimeout(t); resolve(); };
      });
      this.wake = null;
    }
  }

  // ---- one pass

  /** One full pass: reconcile rooms, push the outbox, pull what is new. Passes never overlap. */
  syncNow(): Promise<void> {
    this.running = this.running.then(() => this.pass()).catch(() => undefined);
    return this.running;
  }

  private set(p: Partial<SyncState>) {
    this.state = { ...this.state, ...p };
    this.d.hooks.onState(this.state);
  }

  private async load() {
    if (this.loaded) return;
    this.map = (await this.d.history.getMeta<Record<string, string>>(MAP_KEY)) ?? {};
    this.cursors = (await this.d.history.getMeta<Record<string, number>>(CURSOR_KEY)) ?? {};
    this.lamport = (await this.d.history.getMeta<number>(LAMPORT_KEY)) ?? 0;
    this.loaded = true;
  }

  private async save() {
    await this.d.history.setMeta(MAP_KEY, this.map);
    await this.d.history.setMeta(CURSOR_KEY, this.cursors);
  }

  private async pass(): Promise<void> {
    await this.load();
    this.set({ status: "syncing", pending: (await this.d.history.outboxIds()).length });
    try {
      let rooms = await this.d.api.rooms();
      await this.reconcile(rooms);
      if ((await this.flush()) > 0) rooms = await this.d.api.rooms(); // see our own sends in the room list, so the cursor moves past them now
      await this.pull(rooms);
      this.failures = 0;
      this.set({ status: "idle", pending: (await this.d.history.outboxIds()).length, lastSyncAt: Date.now(), error: null });
    } catch (err) {
      this.failures += 1;
      const e = err as RoomsApiError;
      const offline = e instanceof RoomsApiError && e.offline;
      this.set({
        status: offline ? "offline" : "error", pending: (await this.d.history.outboxIds().catch(() => [])).length,
        error: offline ? null : e instanceof Error ? e.message : "Sync failed.",
      });
    }
  }

  /** Match this device's room labels with the server's rooms. */
  private async reconcile(rooms: ServerRoom[]): Promise<void> {
    const byId = new Map(rooms.map((r) => [r.id, r]));
    const labels = this.d.labels();
    const change: RoomsChange = { added: [], removed: [], info: {} };

    for (const label of labels) {            // a room we had synced is gone from the server: deleted, or we left
      const id = this.map[label];
      if (id && !byId.has(id)) { delete this.map[label]; delete this.cursors[id]; change.removed.push(label); }
    }
    const queued = new Set(await this.d.history.outboxIds());
    const used = new Set(Object.values(this.map));
    const gone = new Set(change.removed);
    const have = labels.filter((l) => !gone.has(l));

    for (const label of have) {              // a room only this device has: find or create its server copy, queue its history
      if (this.map[label]) continue;
      const mine = rooms.find((r) => r.title === label && r.role === "owner" && !used.has(r.id));
      const room = mine ?? (await this.d.api.createRoom(label));
      this.map[label] = room.id;
      used.add(room.id);
      for (const m of (await this.d.history.load(label)).messages) {
        if (queued.has(m.id)) continue;
        this.lamport += 1;
        await this.d.history.putOutbox({ id: m.id, room: label, event: toEvent(m, await this.d.history.deviceId(), this.lamport), at: m.timestamp });
      }
      await this.d.history.setMeta(LAMPORT_KEY, this.lamport);
    }

    const taken = new Set([...have]);
    for (const r of rooms) {                 // a room on the server this device doesn't have: a new device, or an invitation
      if (used.has(r.id)) continue;
      let label = r.title, n = 2;
      while (taken.has(label)) label = `${r.title}-${n++}`;
      taken.add(label);
      this.map[label] = r.id;
      used.add(r.id);
      change.added.push({ label, id: r.id, title: r.title, role: r.role });
    }

    const labelOf = new Map(Object.entries(this.map).map(([l, id]) => [id, l]));
    for (const r of rooms) {
      const label = labelOf.get(r.id);
      if (label) change.info[label] = { unread: r.unread ?? 0, members: r.members ?? 1, last: r.last ?? null, role: r.role };
    }
    await this.save();
    if (change.added.length || change.removed.length || Object.keys(change.info).length) this.d.hooks.onRooms(change);
  }

  /** Push the outbox. A message the server accepts (or already had) leaves the queue; one it refuses for good leaves with a notice;
   *  one it asks us to retry stays. */
  private async flush(): Promise<number> {
    let sent = 0;
    const items = await this.d.history.outbox();
    const byLabel = new Map<string, OutboxItem[]>();
    for (const it of items) { if (this.map[it.room]) byLabel.set(it.room, [...(byLabel.get(it.room) ?? []), it]); }
    for (const [label, list] of byLabel) {
      for (let i = 0; i < list.length; i += BATCH) {
        const batch = list.slice(i, i + BATCH);
        try {
          const res = await this.d.api.append(this.map[label], batch.map((b) => b.event));
          for (const a of res.accepted) { await this.d.history.delOutbox(a.id); sent += 1; }
          for (const r of res.rejected) {
            if (r.retry) continue;
            if (r.id) await this.d.history.delOutbox(r.id);
            this.d.hooks.onNotice(label, `A message wasn't accepted: ${r.reason}.`);
          }
          if (res.rejected.some((r) => r.retry)) throw new RoomsApiError("Slowing down: too many messages at once.", 429);
        } catch (err) {
          if (err instanceof RoomsApiError && err.status === 404) {   // the room is gone: its queued messages can't go anywhere
            for (const b of list) await this.d.history.delOutbox(b.id);
            this.d.hooks.onNotice(label, "This room no longer exists on your account, so your unsent messages were dropped.");
            break;
          }
          throw err;
        }
      }
    }
    return sent;
  }

  /** Fetch what is new in each room (its last_seq is ahead of our cursor), save it, then show it. */
  private async pull(rooms: ServerRoom[]): Promise<void> {
    const labelOf = new Map(Object.entries(this.map).map(([l, id]) => [id, l]));
    for (const r of rooms) {
      const label = labelOf.get(r.id);
      if (!label) continue;
      let cursor = this.cursors[r.id] ?? 0;
      while (cursor < r.last_seq) {
        const page = await this.d.api.events(r.id, cursor, PAGE);
        const fresh: GroupMessage[] = [];
        for (const e of page.events) {
          this.lamport = Math.max(this.lamport, e.lamport);
          const m = toMessage(e, label);
          if (!m || (await this.d.history.hasMessage(label, m.id))) continue;
          await this.d.history.appendMessage(m);
          fresh.push(m);
        }
        // once everything is read, jump to the server's last seq: it can be ahead of the last event (ids skipped by a retry), and
        // everything at or below it is already included, so this stops asking for nothing
        cursor = page.more ? page.next : Math.max(page.next, page.last_seq);
        this.cursors[r.id] = cursor;
        await this.d.history.setMeta(LAMPORT_KEY, this.lamport);
        await this.save();
        if (fresh.length) this.d.hooks.onMessages(label, fresh);
        if (!page.more) break;
      }
    }
  }
}

export { newEventId };
