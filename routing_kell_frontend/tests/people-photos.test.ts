import { beforeEach, describe, expect, it } from "vitest";
import { forgetPeoplePhotos, personPhoto } from "@/lib/chat/people-photos";
import { fallbackName } from "@/lib/chat/events";

const deps = (res: () => Response | Promise<Response>, calls: string[] = [], now = () => 1000) => ({
  token: async () => "tok", origin: "https://mcp.example", now, makeUrl: () => "blob:photo",
  fetchImpl: (async (u: string) => { calls.push(u); return res(); }) as unknown as typeof fetch,
});
beforeEach(() => forgetPeoplePhotos());

describe("other people's photos", () => {
  it("fetches once per person however many messages ask, and returns a local url", async () => {
    const calls: string[] = [];
    const d = deps(() => new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { "content-type": "image/jpeg" } }), calls);
    const [a, b] = await Promise.all([personPhoto("u2", d), personPhoto("u2", d)]);
    expect(a).toBe("blob:photo"); expect(b).toBe("blob:photo");
    expect(await personPhoto("u2", d)).toBe("blob:photo");
    expect(calls).toEqual(["https://mcp.example/v1/people/u2/photo"]);
  });

  it("no photo means initials, and is not asked again for five minutes", async () => {
    const calls: string[] = [];
    let t = 1000;
    const d = deps(() => new Response(null, { status: 404 }), calls, () => t);
    expect(await personPhoto("u3", d)).toBeNull();
    t += 60_000; expect(await personPhoto("u3", d)).toBeNull();
    expect(calls.length).toBe(1);
    t += 5 * 60_000; await personPhoto("u3", d);
    expect(calls.length).toBe(2);
  });

  it("an unreachable server or a missing session is just no photo", async () => {
    expect(await personPhoto("u4", { ...deps(() => { throw new Error("down"); }) })).toBeNull();
    forgetPeoplePhotos();
    expect(await personPhoto("u5", { ...deps(() => new Response("x")), token: async () => null })).toBeNull();
  });
});

describe("a person with no name", () => {
  it("is 'Member' plus a few letters of their id, so two of them are not the same name", () => {
    expect(fallbackName("1a2b3c4d-0000")).toBe("Member 1a2b");
    expect(fallbackName("9f8e7d6c")).not.toBe(fallbackName("1a2b3c4d"));
    expect(fallbackName(null)).toBe("Member ?");
  });
});
