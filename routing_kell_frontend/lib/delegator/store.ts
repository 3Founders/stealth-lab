/**
 * Your room history, kept on this device (local-first, phase 1 of docs/rooms_design.md): every message and every relay task you
 * have seen is written to IndexedDB, so rooms and chats survive a reload, work offline, and belong to you. You can download all
 * of it, import it, or delete it.
 *
 * Each record is encrypted at rest with AES-GCM under a random per-browser DEVICE KEY that is kept as a NON-EXTRACTABLE CryptoKey
 * (same approach as lib/byok/vault.ts). The record's account, room and id are bound as additional authenticated data, so a record
 * cannot be moved to another room or account. Only the room name, a timestamp and the kind are stored in the clear: they are what
 * the index needs.
 *
 * Honest limits: this is per browser profile. It protects history at rest (disk, the devtools Application tab, backups), not
 * against script running on this site's own origin, and it does not follow you to another device: that is the sync phase. Clearing
 * site data deletes it. Transient room notices ("no agent named ...") are not kept. Server-side relay tasks expire after 7 days;
 * your local copy does not.
 */
import type { KvStore } from "@/lib/byok/vault";
import type { RelayTask } from "./relay-client";
import type { GroupMessage } from "./types";

export const HISTORY_DB = "kel-rooms";
export const KEEP_PER_ROOM = 2000;
const DEVICE_KEY = "device-key";

export type HistoryLine = { kind: "msg"; m: GroupMessage };

interface Sealed { v: 1; kind: "msg" | "task"; room: string; id: string; t: number; iv: string; cipher: string }

export interface HistoryExport {
  version: 1;
  exportedAt: string;
  rooms: Record<string, { messages: GroupMessage[]; tasks: RelayTask[] }>;
}

const b64 = (bytes: Uint8Array) => { let s = ""; for (const b of bytes) s += String.fromCharCode(b); return btoa(s); };
const unb64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const te = new TextEncoder();
const td = new TextDecoder();
const pad = (t: number) => String(Math.max(0, Math.floor(t))).padStart(15, "0");

export class HistoryStore {
  constructor(private store: KvStore, private subject: string) {
    if (!subject) throw new Error("History belongs to a signed-in account.");
  }

  private evKey = (room: string, t: number, id: string) => `ev:${this.subject}:${room}:${pad(t)}:${id}`;
  private evPrefix = (room?: string) => `ev:${this.subject}:${room ?? ""}`;
  private taskKey = (room: string, id: string) => `task:${this.subject}:${room}:${id}`;
  private taskPrefix = (room?: string) => `task:${this.subject}:${room ?? ""}`;

  private async deviceKey(): Promise<CryptoKey> {
    const existing = await this.store.get(DEVICE_KEY);
    if (existing) return existing as CryptoKey;
    const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    await this.store.set(DEVICE_KEY, key);
    return key;
  }

  private aad = (kind: string, room: string, id: string) => te.encode(`${this.subject}|${kind}|${room}|${id}`);

  private async seal(kind: "msg" | "task", room: string, id: string, t: number, value: unknown): Promise<Sealed> {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: this.aad(kind, room, id) }, await this.deviceKey(), te.encode(JSON.stringify(value)));
    return { v: 1, kind, room, id, t, iv: b64(iv), cipher: b64(new Uint8Array(cipher)) };
  }

  private async open<T>(s: Sealed): Promise<T | null> {
    try {
      const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(s.iv), additionalData: this.aad(s.kind, s.room, s.id) }, await this.deviceKey(), unb64(s.cipher));
      return JSON.parse(td.decode(plain)) as T;
    } catch { return null; } // tampered, moved, or the device key was cleared: skipped, never shown
  }

  /** Keeps a message. Writing the same id again is a no-op. */
  async appendMessage(m: GroupMessage): Promise<void> {
    const key = this.evKey(m.roomId, m.timestamp, m.id);
    if ((await this.store.get(key)) !== undefined) return;
    await this.store.set(key, await this.seal("msg", m.roomId, m.id, m.timestamp, m));
  }

  /** Keeps the latest state of a relay task (replaces the earlier copy). */
  async saveTask(t: RelayTask): Promise<void> {
    await this.store.set(this.taskKey(t.room, t.id), await this.seal("task", t.room, t.id, Date.parse(t.created_at) || 0, t));
  }

  /** The room's saved messages (oldest first) and tasks. Records that fail to decrypt are skipped. */
  async load(room: string): Promise<{ messages: GroupMessage[]; tasks: RelayTask[] }> {
    const messages: GroupMessage[] = [];
    const tasks: RelayTask[] = [];
    const evP = `${this.evPrefix(room)}:`;
    const tkP = `${this.taskPrefix(room)}:`;
    for (const k of (await this.store.keys()).sort()) {
      if (k.startsWith(evP)) {
        const s = (await this.store.get(k)) as Sealed | undefined;
        const v = s && s.room === room ? await this.open<GroupMessage>(s) : null;
        if (v) messages.push(v);
      } else if (k.startsWith(tkP)) {
        const s = (await this.store.get(k)) as Sealed | undefined;
        const v = s && s.room === room ? await this.open<RelayTask>(s) : null;
        if (v) tasks.push(v);
      }
    }
    return { messages: messages.sort((a, b) => a.timestamp - b.timestamp), tasks: tasks.sort((a, b) => a.created_at.localeCompare(b.created_at)) };
  }

  /** Rooms that have anything saved. */
  async rooms(): Promise<string[]> {
    const out = new Set<string>();
    const strip = (k: string, p: string) => k.slice(p.length).split(":")[0];
    for (const k of await this.store.keys()) {
      if (k.startsWith(`ev:${this.subject}:`)) out.add(strip(k, `ev:${this.subject}:`));
      else if (k.startsWith(`task:${this.subject}:`)) out.add(strip(k, `task:${this.subject}:`));
    }
    return [...out].sort();
  }

  /** Drops the oldest messages beyond `keep`, so a long-lived room doesn't grow without bound. */
  async trim(room: string, keep = KEEP_PER_ROOM): Promise<number> {
    const p = `${this.evPrefix(room)}:`;
    const mine = (await this.store.keys()).filter((k) => k.startsWith(p)).sort();
    const drop = mine.slice(0, Math.max(0, mine.length - keep));
    for (const k of drop) await this.store.delete(k);
    return drop.length;
  }

  async clearRoom(room: string): Promise<void> {
    for (const k of await this.store.keys()) if (k.startsWith(`${this.evPrefix(room)}:`) || k.startsWith(`${this.taskPrefix(room)}:`)) await this.store.delete(k);
  }

  /** Everything this account has saved on this device. Not the device key. */
  async clearAll(): Promise<void> {
    for (const k of await this.store.keys()) if (k.startsWith(`ev:${this.subject}:`) || k.startsWith(`task:${this.subject}:`)) await this.store.delete(k);
  }

  /** A readable copy of everything, for you to keep. */
  async exportAll(): Promise<HistoryExport> {
    const rooms: HistoryExport["rooms"] = {};
    for (const r of await this.rooms()) { const { messages, tasks } = await this.load(r); rooms[r] = { messages, tasks }; }
    return { version: 1, exportedAt: new Date().toISOString(), rooms };
  }

  /** Adds an export's contents (never removes anything). Returns how many messages and tasks were read. */
  async importAll(bundle: unknown): Promise<{ messages: number; tasks: number }> {
    const b = bundle as Partial<HistoryExport> | null;
    if (!b || b.version !== 1 || typeof b.rooms !== "object" || b.rooms === null) throw new Error("That file isn't a keळ history export.");
    let messages = 0, tasks = 0;
    for (const [room, v] of Object.entries(b.rooms)) {
      for (const m of v?.messages ?? []) {
        if (m && typeof m.id === "string" && typeof m.timestamp === "number" && m.roomId === room) { await this.appendMessage(m); messages++; }
      }
      for (const t of v?.tasks ?? []) {
        if (t && typeof t.id === "string" && t.room === room) { await this.saveTask(t); tasks++; }
      }
    }
    return { messages, tasks };
  }
}
