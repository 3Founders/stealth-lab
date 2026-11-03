import { describe, expect, it } from "vitest";
import { guessTitle, isUglyTitle, labelFor } from "@/lib/chat/canvas-names";

describe("canvas names (the same rules as the server)", () => {
  it("an id is not a name, a real title is", () => {
    for (const t of ["0e2a3067-4ddd-4fef-831b-0bdb68343104", "0e2a30674ddd4fef831b0bdb68343104", "3", "12-4", "1.html", "x9k2m4p7q1r5"]) expect(isUglyTitle(t), t).toBe(true);
    for (const t of ["Q4 plan review", "Crumb Bakery", "History of the Internet Slides", "Four-Day Work Week Memo"]) expect(isUglyTitle(t), t).toBe(false);
  });
  it("a name is read from the text, and an id is never shown", () => {
    const uuid = "0e2a3067-4ddd-4fef-831b-0bdb68343104";
    expect(labelFor(uuid, "html", "<!doctype html><title>Crumb Bakery</title>")).toBe("Crumb Bakery");
    expect(labelFor("3", "document", "# Four Day Week\n\ntext")).toBe("Four Day Week");
    expect(labelFor(uuid, "html", "<section><h2>Why now</h2></section>")).toBe("Why now");
    expect(labelFor("Launch plan", "document", "# Other")).toBe("Launch plan");
    expect(labelFor("3", "code", "")).toBe("Code");
    expect(guessTitle("", "html")).toBe("Web page");
  });
});
