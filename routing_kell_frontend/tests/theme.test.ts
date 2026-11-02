import { describe, expect, it } from "vitest";
import { THEME_INIT, THEME_KEY, applyTheme, isTheme, readTheme, saveTheme } from "@/lib/theme";

const store = (v: string | null) => ({ getItem: (k: string) => (k === THEME_KEY ? v : null) });

describe("theme", () => {
  it("reads a saved choice and falls back to the device setting for anything else", () => {
    expect(readTheme(store("dark"))).toBe("dark");
    expect(readTheme(store("light"))).toBe("light");
    expect(readTheme(store("purple"))).toBe("system");
    expect(readTheme(store(null))).toBe("system");
    expect(readTheme({ getItem: () => { throw new Error("blocked"); } })).toBe("system");
    expect(isTheme("system")).toBe(true);
  });
  it("applies by attribute, and system removes it", () => {
    const attrs = new Map<string, string>();
    const root = { setAttribute: (k: string, v: string) => attrs.set(k, v), removeAttribute: (k: string) => attrs.delete(k) } as unknown as HTMLElement;
    applyTheme("dark", root); expect(attrs.get("data-theme")).toBe("dark");
    applyTheme("light", root); expect(attrs.get("data-theme")).toBe("light");
    applyTheme("system", root); expect(attrs.has("data-theme")).toBe(false);
  });
  it("saves, and a blocked store does not throw", () => {
    const seen: string[] = [];
    saveTheme("dark", { setItem: (k, v) => { seen.push(`${k}=${v}`); } });
    expect(seen).toEqual([`${THEME_KEY}=dark`]);
    expect(() => saveTheme("dark", { setItem: () => { throw new Error("blocked"); } })).not.toThrow();
  });
  it("the head script uses the same key and only the two explicit choices", () => {
    expect(THEME_INIT).toContain(THEME_KEY);
    expect(THEME_INIT).toContain("'light'");
    expect(THEME_INIT).toContain("'dark'");
  });
});
