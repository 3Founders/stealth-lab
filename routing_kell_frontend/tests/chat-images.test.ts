import { describe, expect, it } from "vitest";
import { encodeMessage } from "@/lib/byok/chat";
import { toChatTurns } from "@/lib/chat/adapters/assistant";
import { toEvent } from "@/lib/chat/events";
import { fitWithin, splitImages, textForSync, visionHint, whyNotImage, withoutDataImages, MAX_IMAGES_PER_REQUEST } from "@/lib/chat/images";
import type { AgentIdentity, GroupMessage, ImageAttachment } from "@/lib/chat/types";

const PNG = "data:image/png;base64,iVBORw0KGgo=";
const att = (n: string): ImageAttachment => ({ kind: "image", mime: "image/jpeg", data: `D${n}`, name: n, width: 10, height: 10 });
const human = { handle: "@you", displayName: "You", type: "human" as const, status: "online" as const };
const msg = (id: string, text: string, attachments?: ImageAttachment[], sender: AgentIdentity = human): GroupMessage =>
  ({ id, roomId: "r", sender, recipients: ["@all"], payload: { text, ...(attachments ? { attachments } : {}) }, timestamp: 1, depthCounter: 0 });

describe("which files can be attached", () => {
  it("takes png, jpeg, webp and gif up to 40 MB, four at a time", () => {
    expect(whyNotImage({ type: "image/png", size: 1000 }, 0)).toBeNull();
    expect(whyNotImage({ type: "image/svg+xml", size: 1000, name: "a.svg" }, 0)).toMatch(/not a PNG/);
    expect(whyNotImage({ type: "application/pdf", size: 10 }, 0)).toMatch(/not a PNG/);
    expect(whyNotImage({ type: "image/png", size: 41 * 1024 * 1024 }, 0)).toMatch(/over 40 MB/);
    expect(whyNotImage({ type: "image/png", size: 10 }, 4)).toMatch(/At most 4/);
  });
  it("scales a big picture down to the same shape, and never enlarges one", () => {
    expect(fitWithin(3136, 1568)).toEqual({ width: 1568, height: 784 });
    expect(fitWithin(800, 600)).toEqual({ width: 800, height: 600 });
    expect(fitWithin(1000, 4000, 1000)).toEqual({ width: 250, height: 1000 });
  });
});

describe("pictures in a model reply", () => {
  it("draws a data: picture, offers an https one, and leaves everything else as text", () => {
    const segs = splitImages(`look ![a cat](${PNG}) and ![web](https://example.com/x.png) ![bad](http://example.com/x.png) ![js](javascript:alert(1)) ![svg](data:image/svg+xml;base64,AAAA)`);
    const imgs = segs.filter((s) => s.t === "image");
    expect(imgs).toHaveLength(2);
    expect(imgs[0]).toMatchObject({ src: "data", alt: "a cat" });
    expect(imgs[1]).toMatchObject({ src: "remote", url: "https://example.com/x.png" });
    const text = segs.filter((s) => s.t === "text").map((s) => (s as { s: string }).s).join("");
    expect(text).toContain("javascript:alert(1)");
    expect(text).toContain("http://example.com/x.png");
  });
  it("refuses an address with a password in it", () => {
    expect(splitImages("![x](https://user:pw@example.com/a.png)").some((s) => s.t === "image")).toBe(false);
  });
  it("replaces data: pictures with a note, but not web ones", () => {
    expect(withoutDataImages(`a ![x](${PNG}) b ![y](https://example.com/y.png)`)).toBe("a [image] b ![y](https://example.com/y.png)");
  });
});

describe("what a room server stores", () => {
  it("never gets a picture, only a note", () => {
    expect(textForSync("hello", [att("1"), att("2")])).toBe("hello\n\n[2 pictures attached on the sender's device]");
    expect(textForSync("", [att("1")])).toBe("[1 picture attached on the sender's device]");
    expect(textForSync(`made ![x](${PNG})`)).toBe("made [image]");
    const ev = toEvent(msg("1", "see", [att("1")]), "dev", 1);
    expect(ev.body).not.toContain("D1");
    expect(ev.body).toContain("1 picture attached");
  });
});

describe("sending pictures to a model", () => {
  const m = { role: "user" as const, content: [{ type: "text" as const, text: "what is this?" }, { type: "image" as const, mime: "image/jpeg", data: "QUJD" }] };
  it("uses image_url parts for OpenAI-style providers", () => {
    expect(encodeMessage(m, false)).toEqual({ role: "user", content: [{ type: "text", text: "what is this?" }, { type: "image_url", image_url: { url: "data:image/jpeg;base64,QUJD" } }] });
  });
  it("uses base64 image blocks for Anthropic", () => {
    expect(encodeMessage(m, true).content).toEqual([{ type: "text", text: "what is this?" }, { type: "image", source: { type: "base64", media_type: "image/jpeg", data: "QUJD" } }]);
  });
  it("leaves a text-only message exactly as it was", () => {
    expect(encodeMessage({ role: "user", content: "hi" }, false)).toEqual({ role: "user", content: "hi" });
  });
  it("puts a picture into the turn that carries it, and plain text stays a string", () => {
    const turns = toChatTurns([msg("1", "hello"), msg("2", "what is this", [att("A")])]);
    expect(turns).toHaveLength(1);
    const c = turns[0].content;
    expect(Array.isArray(c)).toBe(true);
    expect((c as Array<{ type: string }>).filter((p) => p.type === "image")).toHaveLength(1);
    expect(toChatTurns([msg("1", "hello")])[0].content).toBe("@you: hello");
  });
  it("sends only the newest few pictures and never a picture the model made", () => {
    const many = [1, 2, 3, 4, 5].map((i) => msg(`m${i}`, `p${i}`, [att(String(i))]));
    const turns = toChatTurns(many);
    const parts = turns[0].content as Array<{ type: string; text?: string }>;
    expect(parts.filter((p) => p.type === "image")).toHaveLength(MAX_IMAGES_PER_REQUEST);
    const bot = { handle: "@assistant", displayName: "a", type: "local_agent" as const, status: "online" as const };
    const t2 = toChatTurns([msg("1", "draw"), msg("2", `here ![x](${PNG})`, undefined, bot)], "@assistant");
    expect(JSON.stringify(t2)).not.toContain("iVBOR");
    expect(JSON.stringify(t2)).toContain("[image]");
  });
});

describe("telling people when a model cannot read pictures", () => {
  it("adds a hint to an error that looks like that, and only to that", () => {
    expect(visionHint("The provider answered 400: This model does not support image input")).toMatch(/may not accept pictures/);
    expect(visionHint("The provider answered 401: invalid key")).toBeNull();
  });
});
