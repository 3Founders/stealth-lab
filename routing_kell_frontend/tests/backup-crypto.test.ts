// @vitest-environment node
import { describe, expect, it } from "vitest";
import { decryptBackup, encryptBackup } from "@/lib/chat/backup-crypto";

describe("encrypted backup", () => {
  const bundle = { version: 1, rooms: { "#general": { messages: [{ id: "m1", payload: { text: "the launch code is swordfish" } }], tasks: [] } } };

  it("opens with the passphrase and reads back exactly what was put in", async () => {
    const file = await encryptBackup(bundle, "correct horse battery");
    expect(await decryptBackup(JSON.parse(JSON.stringify(file)), "correct horse battery")).toEqual(bundle);
  });

  it("shows no readable text, uses a new salt and nonce each time, and refuses a wrong passphrase or a changed file", async () => {
    const a = await encryptBackup(bundle, "correct horse battery");
    const b = await encryptBackup(bundle, "correct horse battery");
    expect(JSON.stringify(a)).not.toContain("swordfish");
    expect(a.salt).not.toBe(b.salt);
    expect(a.cipher).not.toBe(b.cipher);
    await expect(decryptBackup(a, "wrong passphrase!!")).rejects.toThrow("Wrong passphrase");
    const bytes = Uint8Array.from(atob(a.cipher), (c) => c.charCodeAt(0)); bytes[3] ^= 1;
    await expect(decryptBackup({ ...a, cipher: btoa(String.fromCharCode(...bytes)) }, "correct horse battery")).rejects.toThrow("Wrong passphrase");
  });

  it("insists on a passphrase of some length and refuses files that are not backups", async () => {
    await expect(encryptBackup(bundle, "short")).rejects.toThrow("at least");
    await expect(decryptBackup({ hello: "world" }, "correct horse battery")).rejects.toThrow("not a kell encrypted backup");
  });
});
