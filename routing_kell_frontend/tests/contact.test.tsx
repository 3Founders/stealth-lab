// @vitest-environment jsdom
// The Contact page and its request: configured destinations show only when set, the form posts what it should (and the
// honeypot untouched), and every failure comes back as a sentence the visitor can act on.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseBookingUrl, parseContactEmail, submitContact, type ContactInput } from "@/lib/contact";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const INPUT: ContactInput = { name: "Ada", email: "ada@example.com", company: "", topic: "pilot", message: "hello", website: "", turnstileToken: "tok" };
const respond = (status: number, body?: unknown) => vi.fn(async () => new Response(body === undefined ? null : JSON.stringify(body), { status }));

describe("contact destinations", () => {
  it("a booking address must be https (http only on this machine) and carry no credentials", () => {
    expect(parseBookingUrl("https://cal.com/founders/intro")).toBe("https://cal.com/founders/intro");
    expect(parseBookingUrl("http://localhost:3000/x")).toBe("http://localhost:3000/x");
    expect(parseBookingUrl("http://cal.com/x")).toBeNull();
    expect(parseBookingUrl("https://u:p@cal.com/x")).toBeNull();
    expect(parseBookingUrl("javascript:alert(1)")).toBeNull();
    expect(parseBookingUrl(undefined)).toBeNull();
  });
  it("a contact email must look like one", () => {
    expect(parseContactEmail("hello@example.com")).toBe("hello@example.com");
    expect(parseContactEmail("a b@example.com")).toBeNull();
    expect(parseContactEmail("x@y")).toBeNull();
    expect(parseContactEmail("")).toBeNull();
  });
});

describe("submitContact", () => {
  it("posts the fields, the page path and the honeypot to /v1/contact", async () => {
    const f = respond(202, { ok: true });
    expect(await submitContact(INPUT, f as unknown as typeof fetch, "https://api.example")).toEqual({ ok: true });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example/v1/contact");
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({ name: "Ada", email: "ada@example.com", company: null, topic: "pilot", message: "hello", turnstile_token: "tok", website: "" });
  });
  it("shows the server's sentence for a refusal, and a generic one when it gave none", async () => {
    const refused = await submitContact(INPUT, respond(429, { detail: "Too many messages. Please try again later." }) as unknown as typeof fetch, "https://api.example");
    expect(refused).toEqual({ ok: false, message: "Too many messages. Please try again later." });
    const generic = await submitContact(INPUT, respond(500) as unknown as typeof fetch, "https://api.example");
    expect(generic.ok).toBe(false);
    expect((generic as { message: string }).message).toMatch(/try again/);
  });
  it("a network failure and a missing server address are explained, never thrown", async () => {
    const down = vi.fn(async () => { throw new TypeError("fetch failed"); });
    expect((await submitContact(INPUT, down as unknown as typeof fetch, "https://api.example")).ok).toBe(false);
    const none = await submitContact(INPUT, vi.fn() as unknown as typeof fetch, "");
    expect(none).toMatchObject({ ok: false });
    expect((none as { message: string }).message).toMatch(/email/);
  });
});

vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <span>{children}</span> }));
vi.mock("@/components/Turnstile", () => ({ default: () => null, TURNSTILE_SITE_KEY: "" }));

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; vi.unstubAllEnvs(); vi.resetModules(); });

async function mountPage() {
  const { default: ContactPage } = await import("@/app/contact/page");
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(<ContactPage />); });
}

describe("Contact page", () => {
  it("has a labelled form, a honeypot hidden from people and assistive tech, and a disabled booking slot when none is configured", async () => {
    await mountPage();
    expect(host!.textContent).toContain("Talk to a founder");
    for (const el of host!.querySelectorAll<HTMLInputElement>("input, select, textarea")) {
      expect(el.labels?.length, `${el.name} has no label`).toBeGreaterThan(0);
    }
    const trap = host!.querySelector<HTMLInputElement>('input[name="website"]')!;
    expect(trap.closest("[aria-hidden='true']")).not.toBeNull();
    expect(trap.tabIndex).toBe(-1);
    // No booking link configured: the slot is there, honestly disabled, and links nowhere.
    const slot = [...host!.querySelectorAll("button")].find((b) => b.textContent === "Book a call")!;
    expect(slot.disabled).toBe(true);
    expect(host!.querySelector('a[href*="calendly"], a[target="_blank"]')).toBeNull();
  });
  it("offers a booking button and an email address when the deployment sets them", async () => {
    vi.stubEnv("NEXT_PUBLIC_BOOKING_URL", "https://cal.com/founders/intro");
    vi.stubEnv("NEXT_PUBLIC_CONTACT_EMAIL", "hello@example.com");
    await mountPage();
    const book = host!.querySelector<HTMLAnchorElement>("a.btn-ink")!;
    expect(book.href).toBe("https://cal.com/founders/intro");
    expect(book.rel).toContain("noopener");
    expect(host!.querySelector('a[href="mailto:hello@example.com"]')).not.toBeNull();
  });
});
