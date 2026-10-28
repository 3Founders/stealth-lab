/**
 * The browser vault for a person's own model keys (BYOK, "mode 1": the key never reaches kell servers).
 *
 * How a key is kept: it is encrypted with AES-GCM under a random 256-bit DEVICE KEY that is generated here and stored in
 * IndexedDB as a NON-EXTRACTABLE CryptoKey (the page's scripts can use it to encrypt and decrypt but cannot read its bytes
 * out). Only the ciphertext, a random IV and non-secret metadata (provider, label, last 4 characters, date) are stored. The
 * ciphertext is bound (as additional authenticated data) to the signed-in subject and the entry id, so it cannot be moved to
 * another account or entry. Nothing here ever uses localStorage or sessionStorage for a key.
 *
 * Honest limits: this protects keys at rest in the browser profile (disk, devtools "Application" tab, backups). It does NOT
 * protect against script running on this site's own origin (an XSS bug could ask the vault to decrypt), and it does not follow
 * the person to another browser: keys are per browser profile, and clearing site data deletes them. A passphrase-wrapped mode
 * would add a prompt on every visit and is not built.
 */

export interface KeyMeta {
  id: string;
  provider: string; // a provider id from providers.ts
  baseUrl: string; // the host the key is sent to (and nowhere else)
  label: string;
  last4: string;
  model: string; // the model the person chose when they tested the key; empty until then
  createdAt: string;
}

interface Stored extends KeyMeta { iv: string; cipher: string }

/** The slice of IndexedDB the vault needs. The real one is `idbStore()`; unit tests pass a Map-backed double. */
export interface KvStore {
  get(key: string): Promise<unknown>;
  set(key: string, value: unknown): Promise<void>;
  delete(key: string): Promise<void>;
  keys(): Promise<string[]>;
}

const DEVICE_KEY = "device-key";
const entryKey = (subject: string, id: string) => `entry:${subject}:${id}`;
const prefix = (subject: string) => `entry:${subject}:`;

const b64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes));
const unb64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const enc = new TextEncoder();

export function idbStore(dbName = "kel-byok"): KvStore {
  const open = () => new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(dbName, 1);
    req.onupgradeneeded = () => req.result.createObjectStore("kv");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  const run = async <T,>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
    const db = await open();
    try {
      return await new Promise<T>((resolve, reject) => {
        const req = fn(db.transaction("kv", mode).objectStore("kv"));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    } finally { db.close(); }
  };
  return {
    get: (k) => run("readonly", (s) => s.get(k)),
    set: async (k, v) => { await run("readwrite", (s) => s.put(v, k)); },
    delete: async (k) => { await run("readwrite", (s) => s.delete(k)); },
    keys: async () => (await run("readonly", (s) => s.getAllKeys())).map(String),
  };
}

export class Vault {
  constructor(private store: KvStore, private subject: string) {
    if (!subject) throw new Error("A vault belongs to a signed-in account.");
  }

  private async deviceKey(): Promise<CryptoKey> {
    const existing = await this.store.get(DEVICE_KEY);
    if (existing) return existing as CryptoKey;
    const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]); // non-extractable
    await this.store.set(DEVICE_KEY, key);
    return key;
  }

  async add(input: { provider: string; baseUrl: string; label: string; apiKey: string; model?: string }): Promise<KeyMeta> {
    const apiKey = input.apiKey.trim();
    if (!apiKey) throw new Error("Enter a key.");
    const id = crypto.randomUUID();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const cipher = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv, additionalData: enc.encode(`${this.subject}|${id}`) }, await this.deviceKey(), enc.encode(apiKey));
    const meta: KeyMeta = {
      id, provider: input.provider, baseUrl: input.baseUrl, label: input.label.trim().slice(0, 60),
      last4: apiKey.slice(-4), model: input.model ?? "", createdAt: new Date().toISOString(),
    };
    await this.store.set(entryKey(this.subject, id), { ...meta, iv: b64(iv), cipher: b64(new Uint8Array(cipher)) } satisfies Stored);
    return meta;
  }

  /** Non-secret metadata only. */
  async list(): Promise<KeyMeta[]> {
    const out: KeyMeta[] = [];
    for (const k of await this.store.keys()) {
      if (!k.startsWith(prefix(this.subject))) continue;
      const s = (await this.store.get(k)) as Stored | undefined;
      if (s) out.push({ id: s.id, provider: s.provider, baseUrl: s.baseUrl, label: s.label, last4: s.last4, model: s.model, createdAt: s.createdAt });
    }
    return out.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  }

  async setModel(id: string, model: string): Promise<void> {
    const s = (await this.store.get(entryKey(this.subject, id))) as Stored | undefined;
    if (s) await this.store.set(entryKey(this.subject, id), { ...s, model });
  }

  /** The plain key, for one request. Callers must not log it, store it or put it in a message. */
  async reveal(id: string): Promise<string | null> {
    const s = (await this.store.get(entryKey(this.subject, id))) as Stored | undefined;
    if (!s) return null;
    try {
      const plain = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: unb64(s.iv), additionalData: enc.encode(`${this.subject}|${id}`) }, await this.deviceKey(), unb64(s.cipher));
      return new TextDecoder().decode(plain);
    } catch {
      return null; // tampered, moved to another account, or the device key was cleared
    }
  }

  async remove(id: string): Promise<void> {
    await this.store.delete(entryKey(this.subject, id));
  }

  /** Delete every key this account has in this browser. */
  async clear(): Promise<void> {
    for (const k of await this.store.keys()) if (k.startsWith(prefix(this.subject))) await this.store.delete(k);
  }
}
