// @vitest-environment node
// The browser vault: keys are encrypted at rest, bound to the account, never stored in the clear. (A Map stands in for
// IndexedDB; real WebCrypto does the encryption.)
import { describe, expect, it } from "vitest";
import { Vault, type KvStore } from "@/lib/byok/vault";

const SECRET = "sk-ant-api03-SUPERSECRETVALUE-9f8e7d6c5b4a";

function memStore(): KvStore & { raw: Map<string, unknown> } {
  const raw = new Map<string, unknown>();
  return {
    raw,
    get: async (k) => raw.get(k),
    set: async (k, v) => void raw.set(k, v),
    delete: async (k) => void raw.delete(k),
    keys: async () => [...raw.keys()],
  };
}
const input = { provider: "anthropic", baseUrl: "https://api.anthropic.com", label: "work", apiKey: SECRET };

describe("Vault", () => {
  it("round-trips a key, and stores only ciphertext and non-secret metadata", async () => {
    const store = memStore();
    const v = new Vault(store, "user-1");
    const meta = await v.add(input);
    expect(meta.last4).toBe("c5b4a".slice(-4));
    expect(await v.reveal(meta.id)).toBe(SECRET);
    // what is at rest: nothing readable. Serialise everything that was stored (but the CryptoKey object) and look for the key.
    const atRest = JSON.stringify([...store.raw.entries()].filter(([k]) => k.startsWith("entry:")));
    expect(atRest).not.toContain(SECRET);
    expect(atRest).not.toContain("SUPERSECRET");
  });

  it("the device key is a non-extractable CryptoKey: scripts can use it but cannot read its bytes", async () => {
    const store = memStore();
    await new Vault(store, "user-1").add(input);
    const dk = store.raw.get("device-key") as CryptoKey;
    expect(dk.extractable).toBe(false);
    await expect(crypto.subtle.exportKey("raw", dk)).rejects.toThrow();
  });

  it("list() returns metadata only, never the key", async () => {
    const v = new Vault(memStore(), "user-1");
    await v.add(input);
    const listed = JSON.stringify(await v.list());
    expect(listed).not.toContain(SECRET);
    expect(listed).toContain('"last4"');
  });

  it("a ciphertext moved to another account or another entry does not decrypt", async () => {
    const store = memStore();
    const a = new Vault(store, "user-1");
    const meta = await a.add(input);
    const stored = store.raw.get(`entry:user-1:${meta.id}`);
    store.raw.set(`entry:user-2:${meta.id}`, stored); // same ciphertext, other subject
    expect(await new Vault(store, "user-2").reveal(meta.id)).toBeNull();
    const other = await a.add({ ...input, apiKey: "sk-other-key-1234567890" });
    store.raw.set(`entry:user-1:${other.id}`, { ...(stored as object), id: other.id }); // same ciphertext, other entry
    expect(await a.reveal(other.id)).toBeNull();
  });

  it("accounts are separate: one cannot list or read another's keys", async () => {
    const store = memStore();
    const a = new Vault(store, "user-1");
    const b = new Vault(store, "user-2");
    const meta = await a.add(input);
    expect(await b.list()).toEqual([]);
    expect(await b.reveal(meta.id)).toBeNull();
  });

  it("remove() and clear() delete the key", async () => {
    const store = memStore();
    const v = new Vault(store, "user-1");
    const one = await v.add(input);
    await v.add({ ...input, apiKey: "sk-second-key-abcdef123456" });
    await v.remove(one.id);
    expect(await v.reveal(one.id)).toBeNull();
    expect(await v.list()).toHaveLength(1);
    await v.clear();
    expect(await v.list()).toEqual([]);
    expect([...store.raw.keys()].filter((k) => k.startsWith("entry:"))).toEqual([]);
  });

  it("a lost device key makes old entries unreadable rather than wrong", async () => {
    const store = memStore();
    const v = new Vault(store, "user-1");
    const meta = await v.add(input);
    store.raw.delete("device-key");
    expect(await new Vault(store, "user-1").reveal(meta.id)).toBeNull();
  });

  it("refuses an empty key and an account-less vault", async () => {
    await expect(new Vault(memStore(), "u").add({ ...input, apiKey: "   " })).rejects.toThrow();
    expect(() => new Vault(memStore(), "")).toThrow();
  });
});
