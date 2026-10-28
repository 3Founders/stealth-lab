/**
 * NOT FOR USE. Retired by docs/rooms_design.md (rev 2): hand-written group key handling must not ship without review. Private rooms
 * will use MLS (RFC 9420) through ts-mls instead. This file stays, tested but unused, until it is deleted or replaced; do not import it
 * from product code.
 */
/**
 * End-to-end encryption for rooms (phase 2 of docs/rooms_design.md). WebCrypto only; no new dependency; nothing here touches a
 * network or a server.
 *
 *  - IDENTITY: each person has an ECDH P-256 pair (to receive room keys) and an ECDSA P-256 pair (to sign what they send). The
 *    public halves are published; the private halves stay on the device as non-extractable keys, and are backed up only as a
 *    passphrase-wrapped blob (the key-encryption key comes from lib/sync-crypto.ts `deriveRecoveryKek`, Argon2id).
 *  - ROOM KEY: one AES-GCM-256 key per room per epoch. It is wrapped separately for each member (ephemeral ECDH, HKDF, AES-GCM),
 *    so a server that holds the wrapped copies cannot open them.
 *  - EVENTS: each event is AES-GCM encrypted with AAD `roomId|epoch|eventId`, and the sender signs (aad, sender, nonce, ciphertext)
 *    so a member can tell who really sent it and the server cannot re-attribute or replay it into another room or epoch.
 *  - EPOCHS: removing a member (or revoking an invite link) means a new room key, wrapped only for those who remain. Events from
 *    an older epoch stay readable to those who held that key; this is not MLS and has no per-message forward secrecy.
 *
 * Honest limits: the server (or whoever serves the page) could publish a substituted public key for someone. `fingerprint()` exists
 * so two people can compare keys out of band. Keys live in the browser, so script running on this site's origin could use them.
 */
const ECDH = { name: "ECDH", namedCurve: "P-256" } as const;
const ECDSA = { name: "ECDSA", namedCurve: "P-256" } as const;
const AES = "AES-GCM";
const te = new TextEncoder();
const td = new TextDecoder();

export const toB64 = (bytes: Uint8Array): string => { let s = ""; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000)); return btoa(s); };
export const fromB64 = (s: string): Uint8Array<ArrayBuffer> => Uint8Array.from(atob(s), (c) => c.charCodeAt(0)) as Uint8Array<ArrayBuffer>;
export const toB64Url = (bytes: Uint8Array) => toB64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
export const fromB64Url = (s: string) => fromB64(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
const buf = (u: Uint8Array): ArrayBuffer => u.buffer.slice(u.byteOffset, u.byteOffset + u.byteLength) as ArrayBuffer;

export class RoomCryptoError extends Error {
  constructor(message: string) { super(message); this.name = "RoomCryptoError"; }
}

// ---------------------------------------------------------------- identity

export interface Identity { ecdh: CryptoKeyPair; ecdsa: CryptoKeyPair }
export interface PublicKeys { ecdh: JsonWebKey; ecdsa: JsonWebKey }

/** New identity. The private keys are extractable only so they can be sealed once for the passphrase backup; call `harden` after. */
export async function generateIdentity(): Promise<Identity> {
  return {
    ecdh: (await crypto.subtle.generateKey(ECDH, true, ["deriveBits"])) as CryptoKeyPair,
    ecdsa: (await crypto.subtle.generateKey(ECDSA, true, ["sign", "verify"])) as CryptoKeyPair,
  };
}

export async function publicKeys(id: Identity): Promise<PublicKeys> {
  return { ecdh: await crypto.subtle.exportKey("jwk", id.ecdh.publicKey), ecdsa: await crypto.subtle.exportKey("jwk", id.ecdsa.publicKey) };
}

/** The same identity with non-extractable private keys: what to keep on the device. */
export async function harden(id: Identity): Promise<Identity> {
  const ecdhPriv = await crypto.subtle.importKey("jwk", await crypto.subtle.exportKey("jwk", id.ecdh.privateKey), ECDH, false, ["deriveBits"]);
  const ecdsaPriv = await crypto.subtle.importKey("jwk", await crypto.subtle.exportKey("jwk", id.ecdsa.privateKey), ECDSA, false, ["sign"]);
  return { ecdh: { publicKey: id.ecdh.publicKey, privateKey: ecdhPriv }, ecdsa: { publicKey: id.ecdsa.publicKey, privateKey: ecdsaPriv } };
}

export interface SealedIdentity { ecdh: string; ecdsa: string }

/** The private keys wrapped under a key-encryption key (from the passphrase), for the server to hold for new devices. */
export async function sealIdentity(id: Identity, kek: CryptoKey): Promise<SealedIdentity> {
  const wrap = async (k: CryptoKey) => {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const w = new Uint8Array(await crypto.subtle.wrapKey("jwk", k, kek, { name: AES, iv }));
    return toB64(new Uint8Array([...iv, ...w]));
  };
  return { ecdh: await wrap(id.ecdh.privateKey), ecdsa: await wrap(id.ecdsa.privateKey) };
}

/** Opens a sealed identity on a new device. A wrong passphrase (wrong `kek`) fails here, with no hint about why. */
export async function openIdentity(sealed: SealedIdentity, kek: CryptoKey, pub: PublicKeys): Promise<Identity> {
  const unwrap = async (s: string, alg: typeof ECDH | typeof ECDSA, usages: KeyUsage[]) => {
    const all = fromB64(s);
    return crypto.subtle.unwrapKey("jwk", buf(all.slice(12)), kek, { name: AES, iv: all.slice(0, 12) }, alg, false, usages);
  };
  try {
    return {
      ecdh: { publicKey: await crypto.subtle.importKey("jwk", pub.ecdh, ECDH, true, []), privateKey: await unwrap(sealed.ecdh, ECDH, ["deriveBits"]) },
      ecdsa: { publicKey: await crypto.subtle.importKey("jwk", pub.ecdsa, ECDSA, true, ["verify"]), privateKey: await unwrap(sealed.ecdsa, ECDSA, ["sign"]) },
    };
  } catch { throw new RoomCryptoError("That passphrase doesn't open this key."); }
}

/** A short, comparable fingerprint of someone's public keys, to check out of band that the server gave you the right ones. */
export async function fingerprint(pub: PublicKeys): Promise<string> {
  const canon = JSON.stringify([pub.ecdh.crv, pub.ecdh.x, pub.ecdh.y, pub.ecdsa.crv, pub.ecdsa.x, pub.ecdsa.y]);
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", te.encode(canon)));
  return [...h.slice(0, 15)].map((b) => b.toString(16).padStart(2, "0")).join("").replace(/(.{5})/g, "$1 ").trim();
}

// ---------------------------------------------------------------- room keys

/** A new room key. Extractable so it can be wrapped for each member; keep it in memory only. */
export const newRoomKey = (): Promise<CryptoKey> => crypto.subtle.generateKey({ name: AES, length: 256 }, true, ["encrypt", "decrypt"]);

export interface WrappedKey { epk: JsonWebKey; iv: string; ct: string }

const keyContext = (roomId: string, epoch: number) => `kel-room-key|${roomId}|${epoch}`;

async function wrapKeyFor(shared: CryptoKey, ctx: string): Promise<CryptoKey> {
  // HKDF turns the raw ECDH secret into an AES key bound to this room and epoch.
  const bits = await crypto.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt: new Uint8Array(32), info: te.encode(ctx) }, shared, 256);
  return crypto.subtle.importKey("raw", bits, { name: AES }, false, ["encrypt", "decrypt"]);
}

async function sharedSecret(priv: CryptoKey, pub: CryptoKey): Promise<CryptoKey> {
  const bits = await crypto.subtle.deriveBits({ name: "ECDH", public: pub }, priv, 256);
  return crypto.subtle.importKey("raw", bits, "HKDF", false, ["deriveBits"]);
}

/** The room key, sealed so only the holder of `recipientEcdh`'s private half can open it. */
export async function wrapRoomKey(roomKey: CryptoKey, recipientEcdh: JsonWebKey, roomId: string, epoch: number): Promise<WrappedKey> {
  const ctx = keyContext(roomId, epoch);
  const eph = (await crypto.subtle.generateKey(ECDH, true, ["deriveBits"])) as CryptoKeyPair;
  const pub = await crypto.subtle.importKey("jwk", recipientEcdh, ECDH, false, []);
  const k = await wrapKeyFor(await sharedSecret(eph.privateKey, pub), ctx);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const raw = await crypto.subtle.exportKey("raw", roomKey);
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: AES, iv, additionalData: te.encode(ctx) }, k, raw));
  return { epk: await crypto.subtle.exportKey("jwk", eph.publicKey), iv: toB64(iv), ct: toB64(ct) };
}

export async function unwrapRoomKey(w: WrappedKey, myEcdhPrivate: CryptoKey, roomId: string, epoch: number): Promise<CryptoKey> {
  const ctx = keyContext(roomId, epoch);
  try {
    const epk = await crypto.subtle.importKey("jwk", w.epk, ECDH, false, []);
    const k = await wrapKeyFor(await sharedSecret(myEcdhPrivate, epk), ctx);
    const raw = await crypto.subtle.decrypt({ name: AES, iv: fromB64(w.iv), additionalData: te.encode(ctx) }, k, fromB64(w.ct));
    return crypto.subtle.importKey("raw", raw, { name: AES }, true, ["encrypt", "decrypt"]);
  } catch { throw new RoomCryptoError("This room key isn't for you, or it was altered."); }
}

// ---------------------------------------------------------------- events

export interface EventMeta { roomId: string; epoch: number; eventId: string; sender: string }
export interface SealedEvent { nonce: string; ciphertext: string; sig: string }

const aadOf = (m: EventMeta) => `${m.roomId}|${m.epoch}|${m.eventId}`;
const signedBytes = (m: EventMeta, nonce: string, ciphertext: string) => te.encode(`${aadOf(m)}|${m.sender}|${nonce}|${ciphertext}`);

/** Encrypts and signs one event. `envelope` is any JSON value (see the event envelope in docs/rooms_design.md). */
export async function sealEvent(roomKey: CryptoKey, meta: EventMeta, envelope: unknown, signKey: CryptoKey): Promise<SealedEvent> {
  const nonce = toB64(crypto.getRandomValues(new Uint8Array(12)));
  const ciphertext = toB64(new Uint8Array(await crypto.subtle.encrypt({ name: AES, iv: fromB64(nonce), additionalData: te.encode(aadOf(meta)) }, roomKey, te.encode(JSON.stringify(envelope)))));
  const sig = toB64(new Uint8Array(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, signKey, signedBytes(meta, nonce, ciphertext))));
  return { nonce, ciphertext, sig };
}

/** Verifies the sender's signature first, then decrypts. Throws if either fails: nothing unverified is ever returned. */
export async function openEvent<T = unknown>(roomKey: CryptoKey, meta: EventMeta, e: SealedEvent, senderEcdsaPublic: JsonWebKey): Promise<T> {
  let ok = false;
  try {
    const pub = await crypto.subtle.importKey("jwk", senderEcdsaPublic, ECDSA, false, ["verify"]);
    ok = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, pub, fromB64(e.sig), signedBytes(meta, e.nonce, e.ciphertext));
  } catch { ok = false; }
  if (!ok) throw new RoomCryptoError("This message's signature doesn't match its sender.");
  try {
    const plain = await crypto.subtle.decrypt({ name: AES, iv: fromB64(e.nonce), additionalData: te.encode(aadOf(meta)) }, roomKey, fromB64(e.ciphertext));
    return JSON.parse(td.decode(plain)) as T;
  } catch { throw new RoomCryptoError("This message can't be opened with this room key."); }
}
