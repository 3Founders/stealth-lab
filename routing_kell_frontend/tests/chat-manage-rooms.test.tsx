// @vitest-environment jsdom
// Manage rooms: creating a room asks the owner of the room list, shows why when it cannot, and selects the new room.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import ManageRooms from "@/components/chat/ManageRooms";
import { newRoomName } from "@/lib/chat/rooms";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; });
(HTMLDialogElement.prototype as unknown as { showModal: () => void }).showModal = function (this: HTMLDialogElement) { this.setAttribute("open", ""); };

async function mount(onCreate: (n: string) => { id: string } | { error: string }) {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  const rapi = { members: vi.fn(() => Promise.resolve([])) } as never;
  await act(async () => { root!.render(<ManageRooms open onClose={() => {}} rooms={["#general"]} initial="#general" roles={{}} rapi={rapi} roomId={() => undefined} onDissolve={() => {}} onCreate={onCreate} />); });
}
const type = async (el: HTMLInputElement, v: string) => {
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => { set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); });
};

describe("Manage rooms: create a room", () => {
  it("calls onCreate with what was typed and clears the box", async () => {
    const onCreate = vi.fn(() => ({ id: "#design" }));
    await mount(onCreate);
    const input = host!.querySelector("#dg-manage-new") as HTMLInputElement;
    await type(input, "design");
    const btn = host!.querySelector(".dg-newroom button[type=submit]") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    await act(async () => { btn.click(); });
    expect(onCreate).toHaveBeenCalledWith("design");
    expect((host!.querySelector("#dg-manage-new") as HTMLInputElement).value).toBe("");
  });

  it("shows why a room cannot be made", async () => {
    await mount(() => ({ error: "#general already exists." }));
    await type(host!.querySelector("#dg-manage-new") as HTMLInputElement, "general");
    await act(async () => { (host!.querySelector(".dg-newroom button[type=submit]") as HTMLButtonElement).click(); });
    expect(host!.querySelector(".dg-newroom [role=alert]")?.textContent).toBe("#general already exists.");
  });

  it("the room-name rules: lower case, dashes for spaces, at most 12 rooms, no duplicates", () => {
    expect(newRoomName(["#general"], "Design Review")).toEqual({ name: "#design-review" });
    expect(newRoomName(["#general"], "general")).toEqual({ error: "#general already exists." });
    expect("error" in newRoomName(Array.from({ length: 50 }, (_, i) => `#r${i}`), "x")).toBe(true);
    expect(newRoomName(Array.from({ length: 12 }, (_, i) => `#r${i}`), "x")).toEqual({ name: "#x" });
    expect("error" in newRoomName([], "a.b")).toBe(true);       // dots are not allowed in a name you type here
  });
});
