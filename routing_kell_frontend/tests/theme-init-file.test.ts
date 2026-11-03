// @vitest-environment node
// The theme script sets data-theme on <html> before the page paints. In production the inline copy once lost a stretch of its own text
// (`localStorage.getItem('kell:themevar e=...`), which is a syntax error: no data-theme was ever set, so every dark-mode rule that needs the
// attribute (button hovers among them) never applied in the default mode. The page now loads the script from /theme-init.js, and this test
// keeps that file identical to THEME_INIT and proves both parse and run.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { THEME_INIT } from "@/lib/theme";

const file = fs.readFileSync(path.join(process.cwd(), "public", "theme-init.js"), "utf8").trim();

const run = (script: string, hour: number, saved: string | null, search = "") => {
  const attrs: Record<string, string> = {};
  const fakeDate = class extends Date { constructor() { super(2026, 10, 3, hour, 0, 0); } };
  new Function("document", "location", "localStorage", "URLSearchParams", "Date", script)(
    { documentElement: { setAttribute: (k: string, v: string) => { attrs[k] = v; } } },
    { search },
    { getItem: () => saved },
    URLSearchParams,
    fakeDate,
  );
  return attrs["data-theme"];
};

describe("theme-init.js", () => {
  it("is the same script as THEME_INIT, and it parses", () => {
    expect(file).toBe(THEME_INIT);
    expect(() => new Function(file)).not.toThrow();
  });
  it("sets dark at night and light by day when nothing is saved, and obeys a saved choice or ?theme=", () => {
    expect(run(file, 22, null)).toBe("dark");
    expect(run(file, 3, null)).toBe("dark");
    expect(run(file, 12, null)).toBe("light");
    expect(run(file, 22, "light")).toBe("light");
    expect(run(file, 12, "dark")).toBe("dark");
    expect(run(file, 12, null, "?theme=dark")).toBe("dark");
  });
});
