/**
 * In-memory, per-tab cache of unwrapped P-DEKs. Deliberately NOT
 * localStorage/IndexedDB/sessionStorage -- lost on reload by design (see
 * docs/local_project_sync_security.md's browser-storage note: raw key
 * material lives only in memory / as a non-extractable CryptoKey for the
 * session). Re-deriving from the recovery passphrase (Argon2id, a few
 * real seconds) is the acceptable V1 cost of a reload; this cache only
 * avoids re-prompting/re-deriving on every navigation WITHIN one tab's
 * lifetime.
 */
const _cache = new Map<string, CryptoKey>();

export function getCachedProjectKey(projectId: string): CryptoKey | undefined {
  return _cache.get(projectId);
}

export function setCachedProjectKey(projectId: string, key: CryptoKey): void {
  _cache.set(projectId, key);
}

export function clearCachedProjectKey(projectId: string): void {
  _cache.delete(projectId);
}
