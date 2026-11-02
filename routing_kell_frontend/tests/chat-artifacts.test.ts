// HTML artifacts in a room message: the ```html block an agent posts to show a deck or a small site.
import { describe, expect, it } from "vitest";
import { MAX_ARTIFACT_CHARS, artifactTitle, splitArtifacts } from "@/lib/chat/artifacts";

const page = "<!doctype html><html><head><title>Q3 review</title></head><body><h1>Hi</h1></body></html>";

describe("HTML artifacts", () => {
  it("splits a message into its words and the one page it carries", () => {
    const segs = splitArtifacts(`Here is the deck:\n\`\`\`html\n${page}\n\`\`\`\nAnything else?`);
    expect(segs.map((s) => s.t)).toEqual(["text", "artifact", "text"]);
    const a = segs[1];
    if (a.t !== "artifact") throw new Error("expected an artifact");
    expect(a.artifact.html).toContain("<h1>Hi</h1>");
    expect(a.artifact.title).toBe("Q3 review");
    expect((segs[0] as { text: string }).text).toContain("Here is the deck");
    expect((segs[2] as { text: string }).text).toContain("Anything else?");
  });

  it("takes a title written after the fence, and falls back to a plain name", () => {
    const named = splitArtifacts("```html title=\"Launch site\"\n<p>x</p>\n```");
    expect(named[0].t === "artifact" && named[0].artifact.title).toBe("Launch site");
    const plain = splitArtifacts("```html\n<p>x</p>\n```");
    expect(plain[0].t === "artifact" && plain[0].artifact.title).toBe("HTML artifact");
  });

  it("leaves other languages, unterminated blocks and oversized pages as plain text", () => {
    for (const text of ["```python\nprint(1)\n```", "```html\n<p>never closed", "```html\n" + "x".repeat(MAX_ARTIFACT_CHARS + 1) + "\n```"]) {
      expect(splitArtifacts(text).every((s) => s.t === "text")).toBe(true);
    }
  });

  it("keeps ordinary messages untouched", () => {
    expect(splitArtifacts("hello there")).toEqual([{ t: "text", text: "hello there" }]);
    expect(splitArtifacts("")).toEqual([]);
  });

  it("trims a title and understands title= without quotes", () => {
    expect(artifactTitle("<p>x</p>", "  title=Deck  ")).toBe("Deck");
  });
});
