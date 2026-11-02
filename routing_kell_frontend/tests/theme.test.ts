import { describe, expect, it } from "vitest";
import { THEME_INIT, THEME_KEY, applyTheme, autoTheme, effectiveTheme, isTheme, readTheme, saveTheme } from "@/lib/theme";

const store = (v: string | null) => ({ getItem: (k: string) => (k === THEME_KEY ? v : null) });
const at = (h: number, m = 0) => new Date(2026, 10, 2, h, m);

describe("theme", () => {
  it("is automatic by the clock: dark from 19:00 to 06:59, light in between", () => {
    expect(autoTheme(at(6, 59))).toBe("dark");
    expect(autoTheme(at(7, 0))).toBe("light");
    expect(autoTheme(at(12))).toBe("light");
    expect(autoTheme(at(18, 59))).toBe("light");
    expect(autoTheme(at(19, 0))).toBe("dark");
    expect(autoTheme(at(0, 30))).toBe("dark");
  });
  it("an explicit choice beats the clock", () => {
    expect(effectiveTheme("light", at(23))).toBe("light");
    expect(effectiveTheme("dark", at(12))).toBe("dark");
    expect(effectiveTheme("auto", at(23))).toBe("dark");
  });
  it("reads a saved light or dark, and treats anything else, including the old 'system', as automatic", () => {
    expect(readTheme(store("dark"))).toBe("dark");
    expect(readTheme(store("light"))).toBe("light");
    expect(readTheme(store("system"))).toBe("auto");
    expect(readTheme(store("purple"))).toBe("auto");
    expect(readTheme(store(null))).toBe("auto");
    expect(readTheme({ getItem: () => { throw new Error("blocked"); } })).toBe("auto");
    expect(isTheme("auto")).toBe(true);
  });
  it("applies the theme in effect as an attribute", () => {
    const attrs = new Map<string, string>();
    const root = { setAttribute: (k: string, v: string) => attrs.set(k, v) } as unknown as HTMLElement;
    expect(applyTheme("dark", root, at(12))).toBe("dark"); expect(attrs.get("data-theme")).toBe("dark");
    expect(applyTheme("auto", root, at(12))).toBe("light"); expect(attrs.get("data-theme")).toBe("light");
    expect(applyTheme("auto", root, at(22))).toBe("dark"); expect(attrs.get("data-theme")).toBe("dark");
  });
  it("saves light and dark, forgets the choice for automatic, and a blocked store does not throw", () => {
    const seen: string[] = [];
    saveTheme("dark", { setItem: (k, v) => { seen.push(`set ${k}=${v}`); }, removeItem: (k) => { seen.push(`remove ${k}`); } });
    saveTheme("auto", { setItem: (k, v) => { seen.push(`set ${k}=${v}`); }, removeItem: (k) => { seen.push(`remove ${k}`); } });
    expect(seen).toEqual([`set ${THEME_KEY}=dark`, `remove ${THEME_KEY}`]);
    expect(() => saveTheme("dark", { setItem: () => { throw new Error("blocked"); }, removeItem: () => {} })).not.toThrow();
  });
  it("the head script follows the same rule: the saved choice, else the clock, with a ?theme= override", () => {
    expect(THEME_INIT).toContain(THEME_KEY);
    expect(THEME_INIT).toContain("getHours()");
    expect(THEME_INIT).toContain("h>=19||h<7");
    expect(THEME_INIT).toContain("get('theme')");
  });
});
