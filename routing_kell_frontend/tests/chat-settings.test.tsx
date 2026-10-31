// @vitest-environment jsdom
// The hamburger menu, the Settings window and the preferences behind them.
import React, { act, useState } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_CHAT_PREFS, loadChatPrefs, saveChatPrefs, sends, type ChatPrefs } from "@/lib/chat/prefs";
import { DEFAULT_PREFS } from "@/lib/chat/notify";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => <a href={href} {...rest}>{children}</a> }));

import HamburgerMenu from "@/components/chat/HamburgerMenu";
import SettingsDialog, { SECTIONS, type SectionId } from "@/components/chat/SettingsDialog";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
const mount = async (el: React.ReactElement) => { host = document.createElement("div"); document.body.appendChild(host); await act(async () => { root = createRoot(host!); root.render(el); }); };
beforeEach(() => {
  (HTMLDialogElement.prototype as unknown as { showModal: () => void }).showModal = function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  (HTMLDialogElement.prototype as unknown as { close: () => void }).close = function (this: HTMLDialogElement) { this.removeAttribute("open"); };
});
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

describe("send key", () => {
  const e = (o: Partial<{ key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; composing: boolean }> = {}) => ({ key: "Enter", shiftKey: false, ctrlKey: false, metaKey: false, ...o });
  it("Enter sends by default, Shift+Enter does not, and a composing input never sends", () => {
    expect(sends(e(), true)).toBe(true);
    expect(sends(e({ shiftKey: true }), true)).toBe(false);
    expect(sends(e({ composing: true }), true)).toBe(false);
    expect(sends(e({ key: "a" }), true)).toBe(false);
  });
  it("with 'Enter sends' off, only Ctrl or Cmd+Enter sends", () => {
    expect(sends(e(), false)).toBe(false);
    expect(sends(e({ ctrlKey: true }), false)).toBe(true);
    expect(sends(e({ metaKey: true }), false)).toBe(true);
  });
});

describe("saved preferences", () => {
  const mem = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }; };
  it("round-trip, and anything odd falls back to the defaults", () => {
    const s = mem();
    const p: ChatPrefs = { textSize: "large", compact: true, enterToSend: false, showTimes: false };
    saveChatPrefs("u1", p, s);
    expect(loadChatPrefs("u1", s)).toEqual(p);
    expect(loadChatPrefs("nobody", s)).toEqual(DEFAULT_CHAT_PREFS);
    s.setItem("kel.chat.prefs.bad", "{not json");
    expect(loadChatPrefs("bad", s)).toEqual(DEFAULT_CHAT_PREFS);
    s.setItem("kel.chat.prefs.odd", JSON.stringify({ textSize: "huge", compact: "yes" }));
    expect(loadChatPrefs("odd", s)).toEqual(DEFAULT_CHAT_PREFS);
  });
});

describe("hamburger menu", () => {
  it("opens, lists the options, opens a Settings section, and closes on Escape", async () => {
    const picked: string[] = [];
    await mount(<HamburgerMenu panelOpen onManageRooms={() => picked.push("manage")} onTogglePanel={() => picked.push("panel")} paused={false} onSettings={(s) => picked.push(s)} onPause={() => picked.push("pause")} onSignOut={() => picked.push("out")} />);
    expect(host!.querySelector('[role="menu"]')).toBeNull();
    await act(async () => { (host!.querySelector(".dg-burger") as HTMLButtonElement).click(); });
    const items = [...host!.querySelectorAll('[role="menuitem"]')].map((i) => i.textContent);
    expect(items).toEqual(["Manage rooms", "Close room list", "Preferences", "Notifications", "Your data", "Agents and models", "Keyboard shortcuts", "Pause agents", "Help and docs", "Contact", "About and privacy", "Account", "Sign out"]);
    await act(async () => { ([...host!.querySelectorAll('[role="menuitem"]')].find((i) => i.textContent === "Your data") as HTMLElement).click(); });
    expect(picked).toEqual(["data"]);
    expect(host!.querySelector('[role="menu"]')).toBeNull();
    await act(async () => { (host!.querySelector(".dg-burger") as HTMLButtonElement).click(); });
    await act(async () => { ([...host!.querySelectorAll('[role="menuitem"]')].find((i) => i.textContent === "Manage rooms") as HTMLElement).click(); });
    expect(picked).toEqual(["data", "manage"]);
    await act(async () => { (host!.querySelector(".dg-burger") as HTMLButtonElement).click(); });
    await act(async () => { host!.querySelector('[role="menu"]')!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    expect(host!.querySelector('[role="menu"]')).toBeNull();
  });

  it("offers Resume when agents are paused", async () => {
    await mount(<HamburgerMenu panelOpen onManageRooms={() => {}} onTogglePanel={() => {}} paused onSettings={() => {}} onPause={() => {}} onSignOut={() => {}} />);
    await act(async () => { (host!.querySelector(".dg-burger") as HTMLButtonElement).click(); });
    expect(host!.textContent).toContain("Resume agents");
  });
});

function Harness({ start = "preferences" as SectionId, onData = () => {} }: { start?: SectionId; onData?: () => void }) {
  const [section, setSection] = useState<SectionId>(start);
  const [chat, setChat] = useState<ChatPrefs>(DEFAULT_CHAT_PREFS);
  return (
    <SettingsDialog open section={section} room="#team" onSection={setSection} onClose={() => {}} chatPrefs={chat} onChatPrefs={setChat}
      notifyPrefs={DEFAULT_PREFS} onNotifyPrefs={() => {}} data={{ onDownload: onData, onImport: () => {}, onDelete: () => {} }}
      agents={{ modelText: "OpenAI · gpt", modelReady: true, count: 2, paused: false, onPause: () => {}, maxHops: 4 }} account={{ name: "Ada", onSignOut: () => {} }} />
  );
}

describe("Settings window", () => {
  it("has a section for every menu option, opens on the chosen one, and switches with a click", async () => {
    await mount(<Harness start="data" />);
    expect(host!.querySelector("dialog")!.hasAttribute("open")).toBe(true);
    expect(SECTIONS.map((s) => s.id)).toEqual(["preferences", "notifications", "data", "agents", "account", "shortcuts", "about"]);
    expect(host!.querySelector('[role="tabpanel"]')!.textContent).toContain("saved in this browser, encrypted");
    await act(async () => { (host!.querySelector("#dg-tab-shortcuts") as HTMLButtonElement).click(); });
    expect(host!.querySelector('[role="tabpanel"]')!.textContent).toContain("Open the list of people and agents to mention");
    await act(async () => { (host!.querySelector("#dg-tab-about") as HTMLButtonElement).click(); });
    expect(host!.querySelector('[role="tabpanel"]')!.textContent).toMatch(/not end-to-end encrypted/);
    expect(host!.querySelector('[role="tabpanel"]')!.textContent).toMatch(/does not have a SOC 2 report/);
  });

  it("arrow keys move between sections", async () => {
    await mount(<Harness />);
    const first = host!.querySelector("#dg-tab-preferences") as HTMLButtonElement;
    await act(async () => { first.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })); });
    expect(host!.querySelector("#dg-tab-notifications")!.getAttribute("aria-selected")).toBe("true");
  });

  it("a preference changes and Your data actions run", async () => {
    let downloaded = 0;
    await mount(<Harness onData={() => { downloaded++; }} />);
    const compact = [...host!.querySelectorAll<HTMLInputElement>(".dg-set-row input")][0];
    expect(compact.checked).toBe(false);
    await act(async () => { compact.click(); });
    expect(host!.querySelectorAll<HTMLInputElement>(".dg-set-row input")[0].checked).toBe(true);
    await act(async () => { (host!.querySelector("#dg-tab-data") as HTMLButtonElement).click(); });
    await act(async () => { ([...host!.querySelectorAll("button")].find((b) => b.textContent === "Download") as HTMLButtonElement).click(); });
    expect(downloaded).toBe(1);
  });
});
