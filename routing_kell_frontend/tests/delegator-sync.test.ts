// @vitest-environment node
// Rooms that follow you: two devices on one account converge, offline sends wait and flush once, a retry never duplicates, and a
// new device (or a room you were invited to) gets the whole history. The server is an in-memory fake with the real semantics
// (idempotent ids, ordered seq, membership), mirroring backend/app/services/rooms.py.
import { describe, expect, it } from "vitest";
import type { KvStore } from "@/lib/byok/vault";
import { toEvent, toMessage } from "@/lib/delegator/events";
import { RoomsApiError, type AppendResult, type EventIn, type EventsPage, type ServerEvent, type ServerRoom } from "@/lib/delegator/rooms-api";
import { HistoryStore } from "@/lib/delegator/store";
import { SyncEngine, type RoomsChange, type SyncState } from "@/lib/delegator/sync";
import type { GroupMessage } from "@/lib/delegator/types";

const mapStore = (): KvStore => {
  const map = new Map<string, unknown>();
  return { get: async (k) => map.get(k), set: async (k, v) => void map.set(k, v), delete: async (k) => void map.delete(k), keys: async () => [...map.keys()] };
};

class FakeServer {
  rooms = new Map<string, { id: string; title: string; owner: string; members: Set<string>; events: ServerEvent[]; next: number; reads: Map<string, number> }>();
  offline = false;
  appends = 0;
  private n = 0;
  as(sub: string) {
    const guard = () => { if (this.offline) throw new RoomsApiError("Couldn't reach the keळ server."); };
    return {
      rooms: async (): Promise<ServerRoom[]> => {
        guard();
        return [...this.rooms.values()].filter((r) => r.members.has(sub)).map((r) => ({
          id: r.id, title: r.title, mode: "open" as const, role: r.owner === sub ? "owner" as const : "member" as const, created_at: "2026-10-28T00:00:00Z", last_seq: r.next,
          last_read_seq: r.reads.get(sub) ?? 0, unread: r.events.filter((e) => e.seq > (r.reads.get(sub) ?? 0) && e.sender.sub !== sub).length, members: r.members.size, last: null,
        }));
      },
      createRoom: async (title: string, id?: string): Promise<ServerRoom> => {
        guard();
        const have = [...this.rooms.values()].find((r) => r.owner === sub && r.title === title);
        const r = have ?? { id: id ?? `room-${++this.n}`, title, owner: sub, members: new Set([sub]), events: [], next: 0, reads: new Map() };
        this.rooms.set(r.id, r);
        return { id: r.id, title: r.title, mode: "open", role: "owner", created_at: "", last_seq: r.next };
      },
      events: async (id: string, after = 0, limit = 200): Promise<EventsPage> => {
        guard();
        const r = this.rooms.get(id);
        if (!r || !r.members.has(sub)) throw new RoomsApiError("no such room", 404);
        const rows = r.events.filter((e) => e.seq > after);
        const page = rows.slice(0, limit);
        return { events: page.map((e) => ({ ...e, sender: { ...e.sender, me: e.sender.sub === sub } })), next: page.at(-1)?.seq ?? after, more: rows.length > limit, last_seq: r.next };
      },
      append: async (id: string, events: EventIn[]): Promise<AppendResult> => {
        guard();
        this.appends += 1;
        const r = this.rooms.get(id);
        if (!r || !r.members.has(sub)) throw new RoomsApiError("no such room", 404);
        const out: AppendResult = { accepted: [], rejected: [] };
        for (const e of events) {
          const prior = r.events.find((x) => x.id === e.id);
          if (prior) { out.accepted.push({ id: e.id, seq: prior.seq, duplicate: true }); continue; }
          if (!e.body.trim()) { out.rejected.push({ id: e.id, reason: "the message is empty", retry: false }); continue; }
          r.next += 1;
          r.events.push({
            id: e.id, room_id: id, seq: r.next, kind: e.kind, body: e.body, target: null, reply_to: null, lamport: e.lamport ?? 0, mentions: [], depth: e.depth ?? 0, device_id: e.device_id ?? null,
            created_at: new Date(Date.UTC(2026, 9, 28, 12, 0, r.next)).toISOString(),
            sender: { kind: e.sender_kind, handle: e.sender_kind === "agent" ? e.sender_handle ?? "@agent" : "@person", sub, name: sub === "bob" ? "Bob" : null, me: true },
          });
          out.accepted.push({ id: e.id, seq: r.next });
        }
        return out;
      },
      markRead: async (id: string, seq: number) => { guard(); this.rooms.get(id)!.reads.set(sub, seq); return { last_read_seq: seq }; },
    };
  }
  invite(id: string, sub: string) { this.rooms.get(id)!.members.add(sub); }
}

const me = { handle: "@you", displayName: "You", type: "human" as const, status: "online" as const };
const msg = (id: string, room: string, text: string, t: number, over: Partial<GroupMessage> = {}): GroupMessage => ({
  id, roomId: room, sender: me, recipients: ["@all"], payload: { text }, timestamp: t, depthCounter: 0, ...over,
});

function device(server: FakeServer, sub: string, labels: string[]) {
  const history = new HistoryStore(mapStore(), sub);
  const got: Array<[string, string[]]> = [];
  const notices: string[] = [];
  const changes: RoomsChange[] = [];
  const states: SyncState[] = [];
  const labelsNow = [...labels];
  const engine = new SyncEngine({
    api: server.as(sub), history, labels: () => labelsNow,
    hooks: {
      onMessages: (l, ms) => got.push([l, ms.map((m) => m.payload.text)]),
      onRooms: (c) => { changes.push(c); for (const a of c.added) labelsNow.push(a.label); for (const r of c.removed) labelsNow.splice(labelsNow.indexOf(r), 1); },
      onNotice: (_l, t) => notices.push(t), onState: (s) => states.push(s),
    },
  });
  /** What the screen does when you type: keep it on the device, then queue it. */
  const send = async (label: string, m: GroupMessage) => { await history.appendMessage(m); await engine.enqueue(label, m); };
  return { engine, history, got, notices, changes, states, labels: labelsNow, send };
}

describe("two devices on one account", () => {
  it("a message sent on the laptop appears on the phone, once, and the laptop doesn't get its own back", async () => {
    const s = new FakeServer();
    const laptop = device(s, "alice", ["#general"]);
    const phone = device(s, "alice", []);
    await laptop.send("#general", msg("11111111-1111-4111-8111-111111111111", "#general", "from the laptop", 1000));
    await laptop.engine.syncNow();
    expect(s.rooms.size).toBe(1);
    await phone.engine.syncNow();
    expect(phone.changes[0].added.map((a) => a.label)).toEqual(["#general"]);      // the room appears on the new device
    expect(phone.got).toEqual([["#general", ["from the laptop"]]]);
    expect((await phone.history.load("#general")).messages.map((m) => m.payload.text)).toEqual(["from the laptop"]);
    await laptop.engine.syncNow();
    expect(laptop.got).toEqual([]);                                               // its own message is not shown twice
    await phone.engine.syncNow(); await laptop.engine.syncNow();
    expect(phone.got).toHaveLength(1);                                            // nothing repeats on later passes
    // and back the other way
    await phone.send("#general", msg("22222222-2222-4222-8222-222222222222", "#general", "from the phone", 2000));
    await phone.engine.syncNow(); await laptop.engine.syncNow();
    expect(laptop.got).toEqual([["#general", ["from the phone"]]]);
    expect((await laptop.history.load("#general")).messages.map((m) => m.payload.text)).toEqual(["from the laptop", "from the phone"]);
  });

  it("a brand-new device pulls the whole history, more than one page of it", async () => {
    const s = new FakeServer();
    const a = device(s, "alice", ["#general"]);
    for (let i = 1; i <= 450; i++) await a.send("#general", msg(`00000000-0000-4000-8000-${String(i).padStart(12, "0")}`, "#general", `m${i}`, i));
    await a.engine.syncNow();
    const b = device(s, "alice", []);
    await b.engine.syncNow();
    const all = (await b.history.load("#general")).messages;
    expect(all).toHaveLength(450);
    expect(all[0].payload.text).toBe("m1");
    expect(all.at(-1)?.payload.text).toBe("m450");
  });

  it("existing local history is adopted: the room is created on the server and its messages are uploaded, once", async () => {
    const s = new FakeServer();
    const a = device(s, "alice", ["#general", "#design"]);
    await a.history.appendMessage(msg("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "#general", "old one", 1));
    await a.history.appendMessage(msg("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "#design", "old two", 2));
    await a.engine.syncNow();
    expect([...s.rooms.values()].map((r) => r.title).sort()).toEqual(["#design", "#general"]);
    expect([...s.rooms.values()].flatMap((r) => r.events.map((e) => e.body)).sort()).toEqual(["old one", "old two"]);
    await a.engine.syncNow(); await a.engine.syncNow();
    expect([...s.rooms.values()].reduce((n, r) => n + r.events.length, 0)).toBe(2);   // not uploaded again
    const phone = device(s, "alice", []);
    await phone.engine.syncNow();
    expect(phone.labels.sort()).toEqual(["#design", "#general"]);
  });
});

describe("offline", () => {
  it("sends made offline wait in the outbox, survive a restart, and go out once when the connection is back", async () => {
    const s = new FakeServer();
    const store = mapStore();
    const first = new HistoryStore(store, "alice");
    const mk = (history: HistoryStore) => new SyncEngine({ api: s.as("alice"), history, labels: () => ["#general"], hooks: { onMessages() {}, onRooms() {}, onNotice() {}, onState() {} } });
    const e1 = mk(first);
    await e1.syncNow();                                            // the room exists on the server
    s.offline = true;
    for (const [i, t] of ["one", "two", "three"].entries()) { const m = msg(`33333333-3333-4333-8333-33333333333${i}`, "#general", t, i); await first.appendMessage(m); await e1.enqueue("#general", m); }
    await e1.syncNow();
    expect(e1.current()).toMatchObject({ status: "offline", pending: 3 });
    expect(s.rooms.values().next().value!.events).toHaveLength(0);

    const restarted = mk(new HistoryStore(store, "alice"));        // the page was reloaded while offline: the queue is still there
    s.offline = false;
    await restarted.syncNow();
    expect(restarted.current()).toMatchObject({ status: "idle", pending: 0 });
    expect(s.rooms.values().next().value!.events.map((e) => e.body)).toEqual(["one", "two", "three"]);
    await restarted.syncNow();
    expect(s.rooms.values().next().value!.events).toHaveLength(3);
  });

  it("a retry after a lost reply is the same message", async () => {
    const s = new FakeServer();
    const a = device(s, "alice", ["#general"]);
    const m = msg("44444444-4444-4444-8444-444444444444", "#general", "once", 1);
    await a.send("#general", m);
    await a.engine.syncNow();
    await a.history.putOutbox({ id: m.id, room: "#general", event: toEvent(m, "dev", 9), at: 1 });   // the client never saw the answer and resends
    await a.engine.syncNow();
    expect([...s.rooms.values()][0].events).toHaveLength(1);
    expect((await a.history.load("#general")).messages).toHaveLength(1);
  });

  it("a message the server refuses for good is dropped with a notice, and the rest still go", async () => {
    const s = new FakeServer();
    const a = device(s, "alice", ["#general"]);
    await a.send("#general", msg("55555555-5555-4555-8555-555555555551", "#general", "   ", 1));
    await a.send("#general", msg("55555555-5555-4555-8555-555555555552", "#general", "fine", 2));
    await a.engine.syncNow();
    expect(a.notices.join(" ")).toContain("wasn't accepted");
    expect([...s.rooms.values()][0].events.map((e) => e.body)).toEqual(["fine"]);
    expect(a.engine.current().pending).toBe(0);
  });
});

describe("people and rooms", () => {
  it("someone else's message shows under their name, an invited room appears, and a room that is gone is dropped", async () => {
    const s = new FakeServer();
    const alice = device(s, "alice", ["#general"]);
    await alice.engine.syncNow();
    const id = [...s.rooms.keys()][0];
    s.invite(id, "bob");
    const bob = device(s, "bob", ["#general"]);                     // Bob already has his own #general
    await alice.send("#general", msg("66666666-6666-4666-8666-666666666666", "#general", "hi bob", 1));
    await alice.engine.syncNow();
    await bob.engine.syncNow();
    expect(bob.changes.flatMap((c) => c.added).map((a) => a.label)).toEqual(["#general-2"]);        // no clash with his own room
    expect(bob.got).toEqual([["#general-2", ["hi bob"]]]);
    const shown = (await bob.history.load("#general-2")).messages[0];
    expect(shown.sender.type).toBe("human");
    expect(shown.sender.handle).not.toBe("@you");                                                  // not shown as Bob himself
    s.rooms.delete(id);                                                                             // the owner deletes the room
    await alice.engine.syncNow(); await bob.engine.syncNow();
    expect(alice.changes.at(-1)!.removed).toEqual(["#general"]);
    expect(bob.changes.at(-1)!.removed).toEqual(["#general-2"]);
  });

  it("an agent's message keeps its handle and depth, and people are shown by name", () => {
    const e = (over: Partial<ServerEvent>): ServerEvent => ({
      id: "77777777-7777-4777-8777-777777777777", room_id: "r", seq: 1, kind: "msg", body: "x", target: null, reply_to: null, lamport: 1, mentions: ["bot"], depth: 2, device_id: null,
      created_at: "2026-10-28T10:00:00Z", sender: { kind: "agent", handle: "@reviewer", sub: "alice", name: null, me: true }, ...over,
    });
    const agent = toMessage(e({}), "#r")!;
    expect(agent.sender).toMatchObject({ handle: "@reviewer", type: "local_agent" });
    expect(agent.depthCounter).toBe(2);
    expect(agent.recipients).toEqual(["@bot"]);
    expect(toMessage(e({ sender: { kind: "person", handle: "@person", sub: "bob", name: "Bob Smith", me: false } }), "#r")!.sender.handle).toBe("@bob-smith");
    expect(toMessage(e({ kind: "react" }), "#r")).toBeNull();                                      // kinds the room doesn't show yet are skipped
    const out = toEvent(msg("88888888-8888-4888-8888-888888888888", "#r", "hello", 1, { depthCounter: 9, sender: { ...me, handle: "@reviewer", type: "local_agent" } }), "dev", 3);
    expect(out).toMatchObject({ kind: "msg", sender_kind: "agent", sender_handle: "@reviewer", depth: 4, lamport: 3, device_id: "dev" });
    expect(toEvent(msg("99999999-9999-4999-8999-999999999999", "#r", "hi", 1), "dev", 1)).not.toHaveProperty("sender_handle");
  });

  it("marking a room read tells the server how far this device has read", async () => {
    const s = new FakeServer();
    const a = device(s, "alice", ["#general"]);
    await a.send("#general", msg("abababab-abab-4bab-8bab-abababababab", "#general", "one", 1));
    await a.engine.syncNow();
    await a.engine.markRead("#general");
    expect([...s.rooms.values()][0].reads.get("alice")).toBe(1);
  });
});
