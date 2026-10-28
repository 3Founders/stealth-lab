// @vitest-environment node
// Room encryption: only the intended member can open a room key, events are bound to their room, epoch and sender, and a removed
// member can't read what comes after.
import { describe, expect, it } from "vitest";
import {
  RoomCryptoError, fingerprint, generateIdentity, harden, newRoomKey, openEvent, openIdentity, publicKeys, sealEvent, sealIdentity, unwrapRoomKey, wrapRoomKey, type EventMeta, type Identity,
} from "@/lib/rooms/crypto";
import { inviteFragment, inviteUrl, parseInviteFragment } from "@/lib/rooms/invite";

const kek = () => crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["wrapKey", "unwrapKey"]);
const meta = (over: Partial<EventMeta> = {}): EventMeta => ({ roomId: "room-1", epoch: 1, eventId: "ev-1", sender: "alice", ...over });
const fails = async (p: Promise<unknown>) => { await expect(p).rejects.toBeInstanceOf(RoomCryptoError); };

async function member() { const id = await harden(await generateIdentity()); return { id, pub: await publicKeys(id) }; }

describe("identity", () => {
  it("private keys are not extractable once hardened, and the public keys are shareable", async () => {
    const m = await member();
    await expect(crypto.subtle.exportKey("jwk", m.id.ecdh.privateKey)).rejects.toThrow();
    await expect(crypto.subtle.exportKey("jwk", m.id.ecdsa.privateKey)).rejects.toThrow();
    expect(m.pub.ecdh.crv).toBe("P-256");
    expect("d" in m.pub.ecdh).toBe(false); // no private part
    expect("d" in m.pub.ecdsa).toBe(false);
  });

  it("a passphrase-sealed identity opens on a new device with the right key and not the wrong one", async () => {
    const fresh = await generateIdentity();
    const pub = await publicKeys(fresh);
    const right = await kek();
    const sealed = await sealIdentity(fresh, right);
    expect(JSON.stringify(sealed)).not.toContain('"d"'); // opaque ciphertext, not a JWK
    const opened = await openIdentity(sealed, right, pub);
    const rk = await newRoomKey();
    const w = await wrapRoomKey(rk, pub.ecdh, "r", 1);
    expect(await unwrapRoomKey(w, opened.ecdh.privateKey, "r", 1)).toBeTruthy(); // the restored identity works
    await fails(openIdentity(sealed, await kek(), pub));
  });

  it("fingerprints are stable, comparable, and differ between people", async () => {
    const a = await member(), b = await member();
    expect(await fingerprint(a.pub)).toBe(await fingerprint(a.pub));
    expect(await fingerprint(a.pub)).not.toBe(await fingerprint(b.pub));
    expect(await fingerprint(a.pub)).toMatch(/^([0-9a-f]{5} ){5}[0-9a-f]{5}$/);
  });
});

describe("room key wrapping", () => {
  it("only the intended member can open it; it is bound to the room and epoch", async () => {
    const alice = await member(), bob = await member(), mallory = await member();
    const rk = await newRoomKey();
    const forBob = await wrapRoomKey(rk, bob.pub.ecdh, "room-1", 1);
    const opened = await unwrapRoomKey(forBob, bob.id.ecdh.privateKey, "room-1", 1);
    const ev = await sealEvent(rk, meta(), { text: "hi" }, alice.id.ecdsa.privateKey);
    expect(await openEvent(opened, meta(), ev, alice.pub.ecdsa)).toEqual({ text: "hi" });
    await fails(unwrapRoomKey(forBob, mallory.id.ecdh.privateKey, "room-1", 1)); // not for Mallory
    await fails(unwrapRoomKey(forBob, bob.id.ecdh.privateKey, "room-2", 1)); // another room
    await fails(unwrapRoomKey(forBob, bob.id.ecdh.privateKey, "room-1", 2)); // another epoch
    await fails(unwrapRoomKey({ ...forBob, ct: forBob.ct.slice(0, -4) + "AAAA" }, bob.id.ecdh.privateKey, "room-1", 1)); // altered
  });

  it("the wrapped key does not contain the key", async () => {
    const bob = await member();
    const rk = await newRoomKey();
    const raw = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.exportKey("raw", rk))));
    expect(JSON.stringify(await wrapRoomKey(rk, bob.pub.ecdh, "r", 1))).not.toContain(raw);
  });
});

describe("events", () => {
  it("round-trips, and the stored form holds no plaintext", async () => {
    const alice = await member();
    const rk = await newRoomKey();
    const sealed = await sealEvent(rk, meta(), { kind: "msg", text: "launch code swordfish" }, alice.id.ecdsa.privateKey);
    expect(JSON.stringify(sealed)).not.toContain("swordfish");
    expect(await openEvent(rk, meta(), sealed, alice.pub.ecdsa)).toEqual({ kind: "msg", text: "launch code swordfish" });
  });

  it("is bound to its room, epoch, id and sender: moving or re-attributing it fails", async () => {
    const alice = await member(), bob = await member();
    const rk = await newRoomKey();
    const sealed = await sealEvent(rk, meta(), { text: "x" }, alice.id.ecdsa.privateKey);
    await fails(openEvent(rk, meta({ roomId: "room-2" }), sealed, alice.pub.ecdsa));
    await fails(openEvent(rk, meta({ epoch: 2 }), sealed, alice.pub.ecdsa));
    await fails(openEvent(rk, meta({ eventId: "ev-2" }), sealed, alice.pub.ecdsa));
    await fails(openEvent(rk, meta({ sender: "bob" }), sealed, alice.pub.ecdsa)); // the server can't relabel the sender
    await fails(openEvent(rk, meta(), sealed, bob.pub.ecdsa)); // signed by someone else
  });

  it("a tampered ciphertext or nonce is refused", async () => {
    const alice = await member();
    const rk = await newRoomKey();
    const s = await sealEvent(rk, meta(), { text: "x" }, alice.id.ecdsa.privateKey);
    await fails(openEvent(rk, meta(), { ...s, ciphertext: s.ciphertext.slice(0, -4) + "AAAA" }, alice.pub.ecdsa));
    await fails(openEvent(rk, meta(), { ...s, nonce: s.nonce.slice(0, -2) + "AA" }, alice.pub.ecdsa));
    await fails(openEvent(rk, meta(), { ...s, sig: "AAAA" }, alice.pub.ecdsa));
  });

  it("two events with the same text have different ciphertext (fresh nonce each time)", async () => {
    const alice = await member();
    const rk = await newRoomKey();
    const a = await sealEvent(rk, meta(), { text: "same" }, alice.id.ecdsa.privateKey);
    const b = await sealEvent(rk, meta(), { text: "same" }, alice.id.ecdsa.privateKey);
    expect(a.ciphertext).not.toBe(b.ciphertext);
  });
});

describe("removing a member (a new epoch)", () => {
  it("the removed member can read the past, not what comes after", async () => {
    const alice = await member(), bob = await member(), carol = await member();
    const k1 = await newRoomKey();
    const wBob1 = await wrapRoomKey(k1, bob.pub.ecdh, "room-1", 1);
    const before = await sealEvent(k1, meta({ epoch: 1 }), { text: "before" }, alice.id.ecdsa.privateKey);

    const k2 = await newRoomKey(); // Bob is removed: a new key, wrapped only for those who remain
    const wCarol2 = await wrapRoomKey(k2, carol.pub.ecdh, "room-1", 2);
    const after = await sealEvent(k2, meta({ epoch: 2, eventId: "ev-2" }), { text: "after" }, alice.id.ecdsa.privateKey);

    const bobK1 = await unwrapRoomKey(wBob1, bob.id.ecdh.privateKey, "room-1", 1);
    expect(await openEvent(bobK1, meta({ epoch: 1 }), before, alice.pub.ecdsa)).toEqual({ text: "before" });
    await fails(openEvent(bobK1, meta({ epoch: 2, eventId: "ev-2" }), after, alice.pub.ecdsa)); // the old key can't open the new epoch
    await fails(unwrapRoomKey(wCarol2, bob.id.ecdh.privateKey, "room-1", 2)); // and Bob can't unwrap Carol's copy

    const carolK2 = await unwrapRoomKey(wCarol2, carol.id.ecdh.privateKey, "room-1", 2);
    expect(await openEvent(carolK2, meta({ epoch: 2, eventId: "ev-2" }), after, alice.pub.ecdsa)).toEqual({ text: "after" });
  });
});

describe("invite links", () => {
  it("carry the room key in the fragment, and the key opens the room", async () => {
    const alice = await member();
    const rk = await newRoomKey();
    const frag = await inviteFragment(rk, 3);
    const url = inviteUrl("https://www.kell.org.in/", "inv-1", frag);
    expect(url).toBe(`https://www.kell.org.in/room/join?id=inv-1#${frag}`);
    expect(new URL(url).search).not.toContain(frag); // the key is after the #, so it is never sent to a server
    const got = await parseInviteFragment(new URL(url).hash);
    expect(got?.epoch).toBe(3);
    const ev = await sealEvent(rk, meta({ epoch: 3 }), { text: "welcome" }, alice.id.ecdsa.privateKey);
    expect(await openEvent(got!.key, meta({ epoch: 3 }), ev, alice.pub.ecdsa)).toEqual({ text: "welcome" });
  });

  it("garbage is not an invite", async () => {
    for (const bad of ["", "#", "#k1.1.short", "#k2.1." + "A".repeat(43), "#k1.x." + "A".repeat(43), "hello"]) expect(await parseInviteFragment(bad)).toBeNull();
  });
});

describe("types", () => {
  it("Identity is usable as exported", async () => {
    const id: Identity = await generateIdentity();
    expect(id.ecdh.publicKey.type).toBe("public");
  });
});
