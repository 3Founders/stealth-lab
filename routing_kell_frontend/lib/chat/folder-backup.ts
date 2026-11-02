/**
 * A copy of your chats in a folder on this computer (Chrome, Edge and other browsers with the File System Access API). A web page cannot
 * save to a computer-wide place by itself, so you choose a folder once; the page then keeps `kell-backup.json` (everything, for Restore) and
 * one readable `rooms/<room>.md` per room up to date there. The folder handle is kept in this browser's storage; the files are plain
 * (not encrypted) because they are yours, on your disk, to read. Browsers without the API get the Download button instead.
 */
import type { KvStore } from "@/lib/byok/vault";
import type { HistoryExport } from "./store";

interface FileH { createWritable(): Promise<{ write(d: string): Promise<void>; close(): Promise<void> }>; getFile(): Promise<{ text(): Promise<string> }> }
interface DirHandle {
  name: string;
  getFileHandle(name: string, o?: { create?: boolean }): Promise<FileH>;
  getDirectoryHandle(name: string, o?: { create?: boolean }): Promise<DirHandle>;
  queryPermission?(o: { mode: "readwrite" }): Promise<"granted" | "denied" | "prompt">;
  requestPermission?(o: { mode: "readwrite" }): Promise<"granted" | "denied" | "prompt">;
}

export const folderBackupSupported = (): boolean => typeof window !== "undefined" && "showDirectoryPicker" in window;
const key = (subject: string) => `folder:${subject}`;

export async function chooseFolder(store: KvStore, subject: string): Promise<string | null> {
  try {
    const h = await (window as unknown as { showDirectoryPicker(o: { mode: "readwrite" }): Promise<DirHandle> }).showDirectoryPicker({ mode: "readwrite" });
    await store.set(key(subject), h);
    return h.name;
  } catch { return null; } // cancelled
}

export async function folderName(store: KvStore, subject: string): Promise<string | null> {
  const h = (await store.get(key(subject))) as DirHandle | undefined;
  return h?.name ?? null;
}

export async function forgetFolder(store: KvStore, subject: string): Promise<void> { await store.delete(key(subject)); }

async function ready(h: DirHandle, ask: boolean): Promise<boolean> {
  if ((await h.queryPermission?.({ mode: "readwrite" })) === "granted") return true;
  return ask && (await h.requestPermission?.({ mode: "readwrite" })) === "granted";
}

const safeName = (room: string) => room.replace(/^#/, "").replace(/[^a-z0-9._-]+/gi, "-").slice(0, 80) || "room";

/** Readable text of one room. */
export function roomMarkdown(room: string, v: HistoryExport["rooms"][string]): string {
  const lines = v.messages.map((m) => `**${m.sender.displayName}** · ${new Date(m.timestamp).toISOString()}\n\n${m.payload.text}\n`);
  return `# ${room}\n\n${lines.join("\n")}`;
}

/** Writes the backup. `ask` is true only for a click (a browser may ask for permission again after a restart). Returns false when it could not. */
export async function writeBackup(store: KvStore, subject: string, bundle: HistoryExport, ask = false): Promise<boolean> {
  const h = (await store.get(key(subject))) as DirHandle | undefined;
  if (!h || !(await ready(h, ask))) return false;
  try {
    const put = async (dir: DirHandle, name: string, body: string) => { const w = await (await dir.getFileHandle(name, { create: true })).createWritable(); await w.write(body); await w.close(); };
    await put(h, "kell-backup.json", JSON.stringify(bundle, null, 2));
    const rooms = await h.getDirectoryHandle("rooms", { create: true });
    for (const [room, v] of Object.entries(bundle.rooms)) await put(rooms, `${safeName(room)}.md`, roomMarkdown(room, v));
    return true;
  } catch { return false; }
}

/** The backup in the chosen folder, ready for `importAll`, or null. */
export async function readBackup(store: KvStore, subject: string): Promise<unknown | null> {
  const h = (await store.get(key(subject))) as DirHandle | undefined;
  if (!h || !(await ready(h, true))) return null;
  try { return JSON.parse(await (await (await h.getFileHandle("kell-backup.json")).getFile()).text()); } catch { return null; }
}
