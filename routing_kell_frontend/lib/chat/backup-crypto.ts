/**
 * An encrypted copy of your chats that you keep yourself: a file protected by a passphrase only you know. The key is derived from the passphrase
 * with PBKDF2-SHA-256 (600,000 rounds, a random salt) and the text is sealed with AES-256-GCM (a random nonce; any change to the file makes it
 * fail to open). Nothing is sent anywhere. kell cannot open the file or reset the passphrase: if it is forgotten, the file cannot be read.
 *
 * This is the first step of docs/local_first_persistence_2026-11-03.md (design A): your data in a file you own, in an open format, that
 * survives clearing a browser or losing a device. Later steps: the same format into a folder on your computer, and a recovery key.
 */
export interface EncryptedBackup { format: "kell-backup"; v: 1; kdf: "PBKDF2-SHA256"; iterations: number; salt: string; iv: string; cipher: string; exportedAt: string }

export const MIN_PASSPHRASE = 10;
const ITERATIONS = 600_000;
const b64 = (b: Uint8Array) => { let s = ""; for (const x of b) s += String.fromCharCode(x); return btoa(s); };
const unb64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const te = new TextEncoder();
const td = new TextDecoder();

async function keyFrom(pass: string, salt: Uint8Array, iterations: number): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey("raw", te.encode(pass.normalize("NFKC")), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt: salt as BufferSource, iterations, hash: "SHA-256" }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

export async function encryptBackup(bundle: unknown, passphrase: string): Promise<EncryptedBackup> {
  if (passphrase.length < MIN_PASSPHRASE) throw new Error(`Use a passphrase of at least ${MIN_PASSPHRASE} characters.`);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await keyFrom(passphrase, salt, ITERATIONS);
  const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv as BufferSource }, key, te.encode(JSON.stringify(bundle)));
  return { format: "kell-backup", v: 1, kdf: "PBKDF2-SHA256", iterations: ITERATIONS, salt: b64(salt), iv: b64(iv), cipher: b64(new Uint8Array(cipher)), exportedAt: new Date().toISOString() };
}

/** The bundle inside, or an error saying the file or the passphrase is wrong (the two cannot be told apart, on purpose). */
export async function decryptBackup(file: unknown, passphrase: string): Promise<unknown> {
  const f = file as Partial<EncryptedBackup> | null;
  if (!f || f.format !== "kell-backup" || f.v !== 1 || typeof f.salt !== "string" || typeof f.iv !== "string" || typeof f.cipher !== "string" || typeof f.iterations !== "number") throw new Error("That is not a kell encrypted backup.");
  if (f.iterations < 100_000 || f.iterations > 5_000_000) throw new Error("That backup uses settings this version will not open.");
  try {
    const key = await keyFrom(passphrase, unb64(f.salt), f.iterations);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(f.iv) as BufferSource }, key, unb64(f.cipher) as BufferSource);
    return JSON.parse(td.decode(plain));
  } catch { throw new Error("Wrong passphrase, or the file was changed."); }
}
