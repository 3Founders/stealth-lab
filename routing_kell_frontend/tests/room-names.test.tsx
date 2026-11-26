// @vitest-environment jsdom
// Rooms are named by people, so they are shown by what the person called them, not by the slug the database stores.
// `rooms.title` is a slug by CHECK constraint (^#[a-z0-9...]) and cannot hold spaces, which is why a Way's room used
// to read "Create-a-research-report-in-neurips-format-2" everywhere outside the header (owner, 2026-11-26: "why names
// are still requiring hyphens"). The human title already existed as `display_name`; it is now used wherever a room is
// named on screen.
import { describe, expect, it } from "vitest";
import { roomName } from "@/components/chat/bare";

describe("how a room is named on screen", () => {
  it("strips the leading # and nothing else for an ordinary room", () => {
    expect(roomName("#general")).toBe("General");
  });

  it("is only a fallback: a room with a display_name is shown by that name", () => {
    // The shape RoomView uses: `info[r]?.display_name || roomName(r)`.
    const shown = (display: string | null | undefined, slug: string) => display || roomName(slug);
    expect(shown("Create a Research Report in neurips format", "#create-a-research-report-in-neurips-format-2"))
      .toBe("Create a Research Report in neurips format");
    expect(shown(null, "#general")).toBe("General");
  });

  it("keeps spaces and capitals, which is the whole point of display_name", () => {
    const display = "Create an audited academic research talk with slides and speaker script";
    expect(display).toContain(" ");
    expect(display).not.toContain("-");
  });
});
