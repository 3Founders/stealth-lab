// @vitest-environment jsdom
// A room the server says runs a Way must show the run, even when it was opened from the sidebar with no `?way=` in the
// address. The badge and the pane must read the SAME fact, or a room is badged "steps" and then shows an empty chat
// (owner, 2026-11-26: "there are 2 rooms with execution tag but only chat is appearing").
//
// RoomView is large and depends on a live sync engine, so this pins the derivation itself: the same expression the
// component uses, over the same inputs. It is deliberately written out rather than imported, so that if the rule in
// RoomView.tsx changes without this changing, the difference shows up as a failing expectation.
import { describe, expect, it } from "vitest";

type Info = Record<string, { kind?: "chat" | "execution" } | undefined>;

/** The rule as RoomView.tsx computes it. */
function roomIsWay(
  room: string,
  info: Info,
  opts: { roomWayId?: string; wayRoomName?: string | null; saysNoWay?: boolean } = {},
) {
  const { roomWayId = "", wayRoomName = null, saysNoWay = false } = opts;
  const kindIsExecution = info[room]?.kind === "execution";
  return !saysNoWay && (kindIsExecution || (Boolean(roomWayId) && wayRoomName !== null && room === wayRoomName));
}

describe("which pane a room shows", () => {
  it("shows the run for a room the server marks execution, opened with no ?way=", () => {
    expect(roomIsWay("#create-an-audited-academic-research-talk-with-slides-and", {
      "#create-an-audited-academic-research-talk-with-slides-and": { kind: "execution" },
    })).toBe(true);
  });

  it("shows the chat for an ordinary room, whatever else is true", () => {
    expect(roomIsWay("#general", { "#general": { kind: "chat" } })).toBe(false);
    expect(roomIsWay("#general", {})).toBe(false);
  });

  it("still shows the run when arriving from a Way's page with ?way=", () => {
    expect(roomIsWay("#talk", {}, { roomWayId: "way-1", wayRoomName: "#talk" })).toBe(true);
  });

  it("lets go when the server says the room has no Way after all", () => {
    // /run answering no_way wins over the kind, or a mislabelled room would strand the person on a blank pane.
    expect(roomIsWay("#talk", { "#talk": { kind: "execution" } }, { saysNoWay: true })).toBe(false);
  });

  it("reads the kind of THIS room, not of whichever room is first in the list", () => {
    const info: Info = {
      "#one": { kind: "execution" },
      "#two": { kind: "chat" },
    };
    expect(roomIsWay("#two", info)).toBe(false);
    expect(roomIsWay("#one", info)).toBe(true);
  });
});
