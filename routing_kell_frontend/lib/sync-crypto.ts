/**
 * Client-side encryption for local project sync. Implements
 * docs/local_project_sync_security.md §D/§E and its "Implementation
 * Closure" §3 -- read both before changing this file.
 *
 * PRIMITIVE: AES-256-GCM, native browser SubtleCrypto (Web Crypto API).
 * Verified against MDN: Baseline/widely available since Jan 2020, 12-byte
 * IV, authenticated (confidentiality + integrity in one operation). A
 * fresh random IV is generated for every single encrypt/wrap call and is
 * PREPENDED to the returned bytes (iv || ciphertext) -- this file's own
 * wire convention, since the REST API stores one opaque blob, not a
 * separate iv field. Never reuse an IV for the same key.
 *
 * KEY HIERARCHY: one random 256-bit P-DEK per project (generated here,
 * extractable ONLY so it can be exported once for the local-bridge
 * handoff and once to be wrapped under the recovery KEK -- never written
 * anywhere as raw bytes beyond that). The recovery KEK is derived from a
 * user-chosen passphrase via Argon2id (openpgpjs/argon2id -- see
 * Implementation Closure §3 for why this library, why these parameters,
 * and the one open item: a dedicated security review of the library
 * itself, not yet done, tracked there, not blocking this code).
 *
 * NEVER derives a key from: username, email, user id, JWT, OAuth token,
 * project name, or filesystem path -- see the ADR's own explicit
 * non-derivation list.
 *
 * WASM LOADING: the package's default `loadWasm()` export expects a
 * bundler-provided `.wasm` -> loader transform (Rollup's plugin-wasm /
 * webpack's wasm-loader) that Next.js's webpack config does not supply
 * out of the box, and static `.wasm` ESM imports failed to bundle here
 * (webpack 5's async-WebAssembly experiment does not resolve this
 * package's WASI-shaped host imports). Instead this file uses the
 * package's own documented "custom Wasm loader" fallback API
 * (`argon2id/lib/setup`, see its README's "Custom Wasm loaders" section)
 * and fetches the two prebuilt binaries as ordinary static assets from
 * `/wasm/` (copied from `node_modules/argon2id/dist/` -- see that
 * directory if the package is ever upgraded, the binaries need re-copying).
 */
// NOT `import type { Argon2idParams } from "argon2id"` -- that package's
// index.js does unconditional top-level `import wasm from './dist/*.wasm'`,
// and pulling in ANY module specifier rooted at the package (even a
// type-only one) makes webpack try to parse index.js, which fails before
// tree-shaking ever runs (see this file's own WASM LOADING note above).
// This local type mirrors argon2id/lib/setup.d.ts's Argon2idParams exactly.
interface Argon2idParams {
  password: Uint8Array;
  salt: Uint8Array;
  parallelism: number;
  passes: number;
  memorySize: number;
  tagLength: number;
  ad?: Uint8Array;
  secret?: Uint8Array;
}

const AES_ALG = "AES-GCM";
const IV_BYTES = 12;
const P_DEK_BITS = 256;

// Implementation Closure §3's final V1 parameters: RFC 9106's
// memory-constrained recommendation's m/t, with p reduced from 4 to 1
// (single-threaded WASM gets no real benefit from p>1 without
// SharedArrayBuffer-backed threading, so p=4 would only multiply
// wall-clock time without adding real GPU/ASIC resistance). NOT yet
// benchmarked on real target hardware -- see that section for the
// required pre-ship measurement.
export const RECOVERY_KDF_PARAMS = { m: 65536, t: 3, p: 1 } as const;
const RECOVERY_TAG_LENGTH = 32; // 256-bit KEK
const RECOVERY_SALT_BYTES = 16;

async function instantiateArgon2Wasm(url: string, importObject: WebAssembly.Imports): Promise<WebAssembly.WebAssemblyInstantiatedSource> {
  const res = await fetch(url);
  const bytes = await res.arrayBuffer();
  return WebAssembly.instantiate(bytes, importObject);
}

let _argon2id: ((params: Argon2idParams) => Uint8Array) | null = null;
async function argon2id(): Promise<(params: Argon2idParams) => Uint8Array> {
  if (!_argon2id) {
    const { default: setupWasm } = await import("argon2id/lib/setup");
    _argon2id = await setupWasm(
      (importObject) => instantiateArgon2Wasm("/wasm/argon2id-simd.wasm", importObject),
      (importObject) => instantiateArgon2Wasm("/wasm/argon2id-no-simd.wasm", importObject),
    );
  }
  return _argon2id;
}

// ---------------------------------------------------------------- base64

function toBase64(bytes: Uint8Array): string {
  // chunked to avoid blowing the call stack on String.fromCharCode(...bytes)
  // for larger payloads -- project snapshots are small today, but this
  // stays correct regardless.
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function fromBase64(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

// ------------------------------------------------------------ project key

/** A fresh, random P-DEK. Extractable so it can be exported once for the
 * local-bridge handoff and once for recovery-wrapping -- callers must
 * never write the exported raw bytes anywhere but those two paths. */
export async function generateProjectKey(): Promise<CryptoKey> {
  return crypto.subtle.generateKey({ name: AES_ALG, length: P_DEK_BITS }, true, ["encrypt", "decrypt"]);
}

export async function exportProjectKeyRaw(key: CryptoKey): Promise<string> {
  const raw = await crypto.subtle.exportKey("raw", key);
  return toBase64(new Uint8Array(raw));
}

export async function importProjectKeyRaw(base64: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", asBuffer(fromBase64(base64)), { name: AES_ALG }, true, ["encrypt", "decrypt", "wrapKey"]);
}

/** TS's lib.dom types are strict about Uint8Array<ArrayBuffer> vs the more
 * general Uint8Array<ArrayBufferLike> that `.slice()`/`.subarray()` can
 * return (which could in principle be backed by a SharedArrayBuffer) --
 * SubtleCrypto's BufferSource type wants the former. A fresh copy always
 * satisfies it; this has no behavioral effect, it only settles the type. */
function asBuffer(bytes: Uint8Array): Uint8Array<ArrayBuffer> {
  return new Uint8Array(bytes);
}

// -------------------------------------------------------------- encrypt

/** Encrypts arbitrary bytes under the P-DEK. Returns base64(iv || ciphertext). */
export async function encryptBytes(key: CryptoKey, plaintext: Uint8Array): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: AES_ALG, iv }, key, asBuffer(plaintext)));
  return toBase64(concat(iv, ciphertext));
}

export async function decryptBytes(key: CryptoKey, base64: string): Promise<Uint8Array> {
  const combined = fromBase64(base64);
  const iv = asBuffer(combined.slice(0, IV_BYTES));
  const ciphertext = asBuffer(combined.slice(IV_BYTES));
  const plaintext = await crypto.subtle.decrypt({ name: AES_ALG, iv }, key, ciphertext);
  return new Uint8Array(plaintext);
}

export async function encryptJson(key: CryptoKey, data: unknown): Promise<string> {
  return encryptBytes(key, new TextEncoder().encode(JSON.stringify(data)));
}

export async function decryptJson<T = unknown>(key: CryptoKey, base64: string): Promise<T> {
  const bytes = await decryptBytes(key, base64);
  return JSON.parse(new TextDecoder().decode(bytes)) as T;
}

// ------------------------------------------------------- recovery / KEK

export interface RecoveryKek {
  kek: CryptoKey;
  saltBase64: string;
  params: typeof RECOVERY_KDF_PARAMS;
}

/** Derives the recovery KEK from a user-chosen passphrase. Pass
 * `existingSaltBase64` when unwrapping an ALREADY-wrapped key (recovery /
 * new device); omit it to mint a fresh salt (first-time setup). The
 * passphrase itself is never returned, stored, or sent anywhere -- it
 * exists only for the duration of this call's stack. */
export async function deriveRecoveryKek(passphrase: string, existingSaltBase64?: string): Promise<RecoveryKek> {
  const hash = await argon2id();
  const salt = existingSaltBase64 ? fromBase64(existingSaltBase64) : crypto.getRandomValues(new Uint8Array(RECOVERY_SALT_BYTES));
  const keyBytes = hash({
    password: new TextEncoder().encode(passphrase),
    salt,
    parallelism: RECOVERY_KDF_PARAMS.p,
    passes: RECOVERY_KDF_PARAMS.t,
    memorySize: RECOVERY_KDF_PARAMS.m,
    tagLength: RECOVERY_TAG_LENGTH,
  });
  const kek = await crypto.subtle.importKey("raw", asBuffer(keyBytes), { name: AES_ALG }, false, ["wrapKey", "unwrapKey"]);
  return { kek, saltBase64: toBase64(salt), params: RECOVERY_KDF_PARAMS };
}

/** Wraps the P-DEK under the recovery KEK using SubtleCrypto's native
 * wrapKey -- the spec-correct operation for exactly this, rather than
 * exporting the key and encrypting its bytes as if they were ordinary
 * plaintext (see Implementation Closure §4's correction). Returns
 * base64(iv || wrapped-key-bytes). */
export async function wrapProjectKey(pDek: CryptoKey, kek: CryptoKey): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const wrapped = new Uint8Array(await crypto.subtle.wrapKey("raw", pDek, kek, { name: AES_ALG, iv }));
  return toBase64(concat(iv, wrapped));
}

export async function unwrapProjectKey(wrappedBase64: string, kek: CryptoKey): Promise<CryptoKey> {
  const combined = fromBase64(wrappedBase64);
  const iv = combined.slice(0, IV_BYTES);
  const wrapped = combined.slice(IV_BYTES);
  return crypto.subtle.unwrapKey(
    "raw", wrapped, kek, { name: AES_ALG, iv }, { name: AES_ALG }, true, ["encrypt", "decrypt"],
  );
}
