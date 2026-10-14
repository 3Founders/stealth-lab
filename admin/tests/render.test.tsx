// @vitest-environment jsdom
/**
 * Mounts each page against the synthetic sample organisation and checks what a person would actually see. This is the
 * nearest thing to opening the app in a browser that runs in CI: it catches runtime crashes, broken data wiring and, most
 * importantly, the privacy rule that the Executive page never shows an individual.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OrgProvider } from "@/components/OrgContext";

vi.mock("next/link", async () => {
  const React = await import("react");
  return { default: ({ href, children, ...rest }: { href: string; children?: unknown }) => React.createElement("a", { href, ...rest }, children as never) };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ push: () => {}, replace: () => {} }), useSearchParams: () => new URLSearchParams() }));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLElement | null = null;
let errors: string[] = [];

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_DEMO_DATA", "1");
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  errors = [];
  vi.spyOn(console, "error").mockImplementation((...a: unknown[]) => { errors.push(a.map(String).join(" ")); });
  window.print = vi.fn();
});

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = null; host = null;
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

/** Mounts the page, lets its data load, and returns the visible text. */
async function show(load: () => Promise<{ default: React.ComponentType }>, until: RegExp): Promise<string> {
  const Page = (await load()).default;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root!.render(<OrgProvider><Page /></OrgProvider>); });
  for (let i = 0; i < 80; i++) {
    if (until.test(host.textContent ?? "")) break;
    await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
  }
  return host.textContent ?? "";
}

const crashes = () => errors.filter((e) => /TypeError|ReferenceError|Cannot read|is not a function|Each child in a list|undefined is not|Maximum update depth|Objects are not valid/i.test(e));

describe("Executive page (sample data)", () => {
  it("renders the whole page: verdict, KPIs, North Star, what changed, sections, honest gaps", async () => {
    const t = await show(() => import("@/app/page"), /Return on investment/);
    expect(t).toMatch(/On track|Needs attention|Action needed|No activity yet/);
    for (const label of ["AI spend", "People using it", "Requests", "Cost per completed request", "Typical wait"]) expect(t).toContain(label);
    expect(t).toContain("People active in the last 7 days");
    expect(t).toContain("How the numbers fit together");
    expect(t).toMatch(/Spending (rose|fell|stayed level) by|Spending is unchanged/);
    for (const section of ["Spending", "Adoption", "Reliability and speed", "Risk and control", "Return on investment"]) expect(t).toContain(section);
    expect(t).toContain("Prompts are never stored.");
    expect(t).toContain("Not measured yet");
    expect(t).toContain("Savings = cost on the default model");
    expect(t).toContain("Verified pass rate");
    expect(t).toContain("Not available yet");
    expect(t).toContain("Alert thresholds");
    expect(crashes()).toEqual([]);
  });

  it("never shows an individual: no names, no ids, no emails", async () => {
    const t = await show(() => import("@/app/page"), /Return on investment/);
    expect(t).not.toMatch(/Sample Person|demo-user|demo-uuid|example\.invalid/);
  });

  it("shows adoption depth as counts, and spend split by model and provider", async () => {
    const t = await show(() => import("@/app/page"), /Return on investment/);
    expect(t).toMatch(/Most days/);
    expect(t).toMatch(/Not at all/);
    expect(t).toContain("demo-provider-a / demo-flagship");
    expect(t).toContain("demo-provider-b");
  });

  it("budget: the bar, the personal cap and the 75%/90% markers are present", async () => {
    const t = await show(() => import("@/app/page"), /Return on investment/);
    expect(t).toMatch(/Markers at 75% and 90%/);
    expect(t).toMatch(/Personal spending cap: \$25\.00 per person per day/);
  });

  it("recent admin changes come from the newest-first audit, with no actor", async () => {
    const t = await show(() => import("@/app/page"), /Return on investment/);
    expect(t).toContain("Recent changes by admins");
    expect(t).toMatch(/org policy/);
    expect(t).not.toMatch(/demo-user/);
    expect(t).toMatch(/Verified/); // audit integrity
  });
});

describe("other pages (sample data)", () => {
  const cases: [string, () => Promise<{ default: React.ComponentType }>, RegExp, RegExp][] = [
    ["Policy", () => import("@/app/policy/page"), /Model catalog and policy rules/, /Kill switch/],
    ["Usage", () => import("@/app/usage/page"), /By day/, /Input cost/],
    ["Performance", () => import("@/app/performance/page"), /Whole range, merged/, /Router overhead/],
    ["People", () => import("@/app/people/page"), /Members/, /Sample Person 1/],
    ["Calls", () => import("@/app/calls/page"), /Metadata only/, /demo-flagship/],
    ["Denials", () => import("@/app/denials/page"), /By reason/, /allow-list/],
    ["Audit", () => import("@/app/audit/page"), /Hash chain/, /Intact/],
    ["Compliance", () => import("@/app/compliance/page"), /Legal hold/, /Erasure \(two owners\)/],
    ["Coming soon", () => import("@/app/coming/page"), /Value delivered/, /Savings against the default model/],
  ];
  for (const [name, load, ready, expected] of cases) {
    it(`${name} renders without crashing and shows its content`, async () => {
      const t = await show(load, ready);
      expect(t).toMatch(expected);
      expect(crashes()).toEqual([]);
    });
  }

  it("Operations (a platform page) degrades to honest states, not a crash, when the sample has no operator data", async () => {
    const t = await show(() => import("@/app/operations/page"), /Platform/);
    expect(t).toMatch(/Couldn.t load|Not part of the sample/);
    expect(crashes()).toEqual([]);
  });
});

describe("layout: the date-range controls sit on one horizontal row", () => {
  // A picker placed in a single 1-of-12 grid column is squeezed into a narrow vertical stack. Whatever element holds the
  // picker directly under the page's 12-column grid must therefore span all 12 columns.
  const pages: [string, () => Promise<{ default: React.ComponentType }>, RegExp][] = [
    ["Executive", () => import("@/app/page"), /Return on investment/],
    ["Usage", () => import("@/app/usage/page"), /By day/],
    ["Performance", () => import("@/app/performance/page"), /Whole range, merged/],
    ["People", () => import("@/app/people/page"), /Members/],
    ["Calls", () => import("@/app/calls/page"), /Metadata only/],
    ["Denials", () => import("@/app/denials/page"), /By reason|No refusals/],
    ["Audit", () => import("@/app/audit/page"), /Hash chain/],
  ];
  for (const [name, load, ready] of pages) {
    it(`${name}: the picker's row spans all 12 grid columns`, async () => {
      await show(load, ready);
      const presets = host!.querySelector('nav[aria-label="Range presets"]');
      expect(presets, "range picker is present").not.toBeNull();
      let el: HTMLElement | null = presets as HTMLElement;
      while (el && !(el.parentElement?.classList.contains("frame") && el.parentElement.classList.contains("grid"))) el = el.parentElement;
      expect(el, "picker is inside the page grid").not.toBeNull();
      expect(el!.style.gridColumn).toMatch(/span 12|\/ -1/);
    });
  }
});
