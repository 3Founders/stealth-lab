// Files sent in a room (docs/adding_files.md). The bytes go to the server (db/172_room_files.sql); the message carries only a small
// token that every device turns into a file card. Pictures that are attached the old way (data URLs) are unchanged.
// The idea of attaching any file type, shown as a card in the message, with the feature list that led here, is in
// docs/open_webui_gap_analysis_2026-11-02.md (ideas from Open WebUI, https://github.com/open-webui/open-webui; no code copied).
import { MCP_ORIGIN } from "@/lib/connector";

export const MAX_ROOM_FILE_BYTES = 25 * 1024 * 1024;
export const MAX_FILES_PER_MESSAGE = 6;

export interface RoomFile { id: string; name: string; size: number; mime: string }

/** [[file:<id>|<name>|<size>|<mime>]]: a human-readable fallback for anything that does not draw cards (connectors, agents, old clients). */
const TOKEN = /\[\[file:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\|([^|\]\n]{1,200})\|(\d{1,12})\|([^|\]\n]{1,100})\]\]/g;

export const fileToken = (f: RoomFile): string => `[[file:${f.id}|${f.name.replace(/[|\]\n]/g, "_")}|${f.size}|${f.mime.replace(/[|\]\n]/g, "_")}]]`;

/** The message text with the file tokens taken out, and the files they named. */
export function splitFiles(text: string): { text: string; files: RoomFile[] } {
  const files: RoomFile[] = [];
  const rest = text.replace(TOKEN, (_m, id: string, name: string, size: string, mime: string) => { files.push({ id, name, size: Number(size), mime }); return ""; });
  return { text: files.length ? rest.replace(/\n{3,}/g, "\n\n").trim() : text, files };
}

export function formatSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/** A short label for the card's icon. */
export function fileKind(f: { name: string; mime: string }): string {
  const ext = f.name.includes(".") ? f.name.split(".").pop()!.toLowerCase() : "";
  if (f.mime === "application/pdf" || ext === "pdf") return "PDF";
  if (f.mime.startsWith("image/")) return "IMG";
  if (f.mime.startsWith("audio/")) return "AUD";
  if (f.mime.startsWith("video/")) return "VID";
  if (["doc", "docx"].includes(ext)) return "DOC";
  if (["xls", "xlsx", "csv"].includes(ext)) return "XLS";
  if (["ppt", "pptx"].includes(ext)) return "PPT";
  if (["zip", "gz", "tar", "7z"].includes(ext)) return "ZIP";
  if (f.mime.startsWith("text/") || ["md", "json", "txt", "log"].includes(ext)) return "TXT";
  return ext ? ext.slice(0, 4).toUpperCase() : "FILE";
}

/** Why a file cannot be sent, or null. The server checks again; this saves the wait. */
export function whyNotFile(f: { name: string; size: number }, already: number): string | null {
  if (already >= MAX_FILES_PER_MESSAGE) return `At most ${MAX_FILES_PER_MESSAGE} files in one message.`;
  if (f.size === 0) return `${f.name} is empty.`;
  if (f.size > MAX_ROOM_FILE_BYTES) return `${f.name} is over ${MAX_ROOM_FILE_BYTES / (1024 * 1024)} MB.`;
  if (/\.(exe|dll|msi|scr|com|bat|cmd|vbs|ps1|jar|apk|app|dmg|pif|cpl|lnk|reg)$/i.test(f.name)) return "Programs and scripts can't be sent here.";
  return null;
}

export class FileError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "FileError"; }
}

export interface FilesDeps { token(): Promise<string | null>; origin?: string }

export function filesApi(deps: FilesDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  return {
    /** Sends one file; `onProgress` gets 0..1 while the bytes go up. A failure is an error, never a half-sent message. */
    upload: async (roomId: string, file: File, onProgress?: (p: number) => void, signal?: AbortSignal): Promise<RoomFile & { scanned: boolean; duplicate: boolean }> => {
      const token = await deps.token();
      if (!token) throw new FileError("Sign in again to send files.", 401);
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${base}/v1/rooms/${encodeURIComponent(roomId)}/files?name=${encodeURIComponent(file.name)}`);
        xhr.setRequestHeader("authorization", `Bearer ${token}`);
        xhr.setRequestHeader("content-type", "application/octet-stream");
        xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
        xhr.onerror = () => reject(new FileError("Couldn't reach the keळ server."));
        xhr.onabort = () => reject(new FileError("Stopped."));
        xhr.onload = () => {
          let body: Record<string, unknown> = {};
          try { body = JSON.parse(xhr.responseText) as Record<string, unknown>; } catch { /* no body */ }
          if (xhr.status >= 200 && xhr.status < 300 && typeof body.id === "string") {
            resolve({ id: body.id, name: String(body.name ?? file.name), size: Number(body.size ?? file.size), mime: String(body.mime ?? file.type ?? "application/octet-stream"), scanned: body.scanned === true, duplicate: body.duplicate === true });
          } else reject(new FileError(typeof body.error === "string" && body.error ? body.error : `The server answered ${xhr.status}.`, xhr.status));
        };
        signal?.addEventListener("abort", () => xhr.abort());
        xhr.send(file);
      });
    },
    /** The file's bytes as a Blob. A 410 means the server no longer has it (it keeps files for a limited time). */
    download: async (roomId: string, fileId: string): Promise<Blob> => {
      const token = await deps.token();
      if (!token) throw new FileError("Sign in again to open files.", 401);
      let res: Response;
      try {
        res = await fetch(`${base}/v1/rooms/${encodeURIComponent(roomId)}/files/${encodeURIComponent(fileId)}`, {
          headers: { authorization: `Bearer ${token}` }, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        });
      } catch { throw new FileError("Couldn't reach the keळ server."); }
      if (!res.ok) {
        let msg = "";
        try { msg = String(((await res.json()) as { error?: unknown }).error ?? ""); } catch { /* no body */ }
        throw new FileError(msg || (res.status === 410 ? "That file has expired. Ask the sender to send it again." : `The server answered ${res.status}.`), res.status);
      }
      return res.blob();
    },
  };
}
export type FilesApi = ReturnType<typeof filesApi>;
