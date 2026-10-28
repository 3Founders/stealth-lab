// @vitest-environment jsdom
// The organization pages with a mocked client: what each shows, what each sends, and what a role hides.
import { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = {
  budget: vi.fn(), usageByUser: vi.fn(), members: vi.fn(), onboarding: vi.fn(), killSwitch: vi.fn(), clientConfig: vi.fn(),
  invites: vi.fn(), createInvite: vi.fn(), revokeInvite: vi.fn(), policy: vi.fn(), putPolicy: vi.fn(), recommendedPolicy: vi.fn(),
  providerKeys: vi.fn(), putProviderKey: vi.fn(), removeProviderKey: vi.fn(), calls: vi.fn(), denials: vi.fn(),
};
let role: "owner" | "member" = "owner";
vi.mock("@/components/org/OrgShell", () => ({
  useOrg: () => ({ api, org: { organization_id: "o1", name: "Acme", role, roles: [role] }, orgs: [], admin: role === "owner" }),
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("next/navigation", () => ({ notFound: () => { throw new Error("NEXT_NOT_FOUND"); }, usePathname: () => "/org" }));

import OrgHome from "@/app/org/page";
import MembersPage from "@/app/org/members/page";
import PolicyPage from "@/app/org/policy/page";
import KeysPage from "@/app/org/keys/page";
import ActivityPage from "@/app/org/activity/page";
import JoinPage from "@/app/join/page";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
async function mount(node: React.ReactNode) {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => { root!.render(node); });
  await flush(); await flush();
}
const btn = (t: string) => [...host!.querySelectorAll("button")].find((b) => b.textContent?.includes(t)) as HTMLButtonElement;
const click = (el: Element) => act(async () => { el.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
const fill = async (el: HTMLInputElement | HTMLTextAreaElement, v: string) => {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => { Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); });
};
const submit = (form: Element) => act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); this.dispatchEvent(new Event("close")); };
});
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; role = "owner"; Object.values(api).forEach((m) => m.mockReset()); vi.unstubAllEnvs(); });

const members = [{ user_id: "u1", subject: "sub-ada", display_name: "Ada", email: "ada@acme.co", is_active: true, roles: ["owner"], member_since: "2026-01-02T00:00:00Z" }];
const policy = (over = {}) => ({ organization_id: "o1", kill_switch: false, kill_switch_reason: null, allowed_providers: ["gc"], allowed_models: ["glm"], allowed_tools: ["call_model"], allowed_data_classes: ["USER_PRIVATE"], monthly_budget_usd: "100", per_user_daily_budget_usd: "5", version: 3, ...over });

describe("flag", () => {
  it("/join does not exist while the flag is off", () => {
    expect(() => JoinPage()).toThrow("NEXT_NOT_FOUND");
  });
});

describe("Org home", () => {
  it("shows the month's spend against the cap and each person, by name", async () => {
    api.budget.mockResolvedValue({ policy_version: 3, kill_switch: false, month_start: "2026-10-01T00:00:00Z", day_start: "2026-10-28T00:00:00Z", monthly: { budget_usd: "100", used_usd: "40", held_usd: "0", remaining_usd: "60" }, per_user_daily_budget_usd: "5", users_today: [] });
    api.usageByUser.mockResolvedValue([{ organization_id: "o1", actor_subject: "sub-ada", day: "2026-10-02", calls: 3, failed: 0, tokens_input_fresh: 100, tokens_cache_read: 0, tokens_cache_write: 0, tokens_output: 50, cost_usd: "40", upper_bound_calls: 0 }]);
    api.members.mockResolvedValue(members);
    api.onboarding.mockResolvedValue({ next_step: "running", kill_switch: false });
    await mount(<OrgHome />);
    expect(host!.textContent).toContain("$40.00");
    expect(host!.textContent).toContain("of $100.00");
    expect(host!.textContent).toContain("$60.00 left");
    expect(host!.querySelector('[role="progressbar"]')!.getAttribute("aria-valuenow")).toBe("40");
    expect(host!.querySelector("table")!.textContent).toContain("Ada");
  });
  it("kill switch: needs a reason and a confirm, then sends it", async () => {
    api.budget.mockResolvedValue({ policy_version: 3, kill_switch: false, month_start: "2026-10-01T00:00:00Z", day_start: "x", monthly: { budget_usd: "100", used_usd: "0", held_usd: "0", remaining_usd: "100" }, per_user_daily_budget_usd: "5", users_today: [] });
    api.usageByUser.mockResolvedValue([]); api.members.mockResolvedValue(members); api.onboarding.mockResolvedValue({ next_step: "running", kill_switch: false });
    api.killSwitch.mockResolvedValue(policy({ kill_switch: true }));
    await mount(<OrgHome />);
    await click(host!.querySelector(".org-danger .btn-line")!);
    expect(host!.querySelector("dialog")!.hasAttribute("open")).toBe(true);
    await submit(host!.querySelector("dialog form")!);
    expect(api.killSwitch).not.toHaveBeenCalled();
    expect(host!.textContent).toContain("Say why");
    await fill(host!.querySelector("#ks-reason") as HTMLInputElement, "incident");
    await submit(host!.querySelector("dialog form")!);
    expect(api.killSwitch).toHaveBeenCalledWith("o1", true, "incident");
    expect(host!.textContent).toContain("This organization is stopped");
  });
  it("a member sees only their connection details, and no admin call is made", async () => {
    role = "member";
    api.clientConfig.mockResolvedValue({ organization_id: "o1", name: "Acme", gateway: "https://gw/v1", models: ["glm-5.3-flash"], profiles: {} });
    await mount(<OrgHome />);
    expect(host!.textContent).toContain("https://gw/v1");
    expect(host!.textContent).not.toContain("Stop everything");
    expect(api.budget).not.toHaveBeenCalled();
    expect(api.usageByUser).not.toHaveBeenCalled();
  });
  it("no policy yet is explained, not shown as a failure", async () => {
    api.budget.mockRejectedValue(Object.assign(new Error("no policy"), { status: 409 }));
    api.usageByUser.mockResolvedValue([]); api.members.mockResolvedValue(members); api.onboarding.mockResolvedValue({ next_step: "set_policy", kill_switch: false });
    await mount(<OrgHome />);
    expect(host!.textContent).toContain("No policy is set yet");
    expect(host!.querySelector('a[href="/org/policy"]')).not.toBeNull();
  });
});

describe("Members and invites", () => {
  it("shows a new invite code once, and revokes", async () => {
    api.members.mockResolvedValue(members);
    api.invites.mockResolvedValue([{ id: "i1", label: "Team", created_by: "u1", created_at: "2026-10-01T00:00:00Z", expires_at: "2026-10-08T00:00:00Z", max_uses: 5, uses: 1, revoked_at: null, active: true }]);
    api.createInvite.mockResolvedValue({ id: "i2", code: "SECRET-CODE-123", expires_at: "2026-11-01T00:00:00Z", max_uses: 50, label: null });
    api.revokeInvite.mockResolvedValue(undefined);
    await mount(<MembersPage />);
    expect(host!.textContent).toContain("Ada");
    await submit(host!.querySelectorAll("form")[0]);
    expect(api.createInvite).toHaveBeenCalledWith("o1", { label: undefined, days: 7, max_uses: 50 });
    expect(host!.textContent).toContain("SECRET-CODE-123");
    expect(host!.textContent).toContain("shown once");
    await click(btn("Revoke"));
    expect(api.revokeInvite).toHaveBeenCalledWith("o1", "i1");
  });
});

describe("Policy", () => {
  it("public data only sets exactly the public classes, and the save carries the version", async () => {
    api.policy.mockResolvedValue(policy());
    api.putPolicy.mockResolvedValue(policy({ version: 4, allowed_data_classes: ["PUBLIC_SOURCE", "PUBLIC_DERIVED", "GLOBAL_PROCEDURE"] }));
    await mount(<PolicyPage />);
    const pub = host!.querySelector('.profile-visibility input[type="checkbox"]') as HTMLInputElement;
    expect(pub.checked).toBe(false);
    expect(btn("Save policy").disabled).toBe(true);
    await click(pub);
    expect(btn("Save policy").disabled).toBe(false);
    await submit(host!.querySelector("form")!);
    expect(api.putPolicy).toHaveBeenCalledWith("o1", { allowed_data_classes: ["PUBLIC_SOURCE", "PUBLIC_DERIVED", "GLOBAL_PROCEDURE"] }, 3);
  });
  it("with no policy it offers the setup from the connected keys", async () => {
    api.policy.mockRejectedValue(Object.assign(new Error("no policy"), { status: 409 }));
    api.recommendedPolicy.mockResolvedValue(policy());
    await mount(<PolicyPage />);
    expect(host!.textContent).toContain("Set up your policy");
    await fill(host!.querySelector("#rec-m") as HTMLInputElement, "500");
    await fill(host!.querySelector("#rec-d") as HTMLInputElement, "20");
    await submit(host!.querySelector("form")!);
    expect(api.recommendedPolicy).toHaveBeenCalledWith("o1", "500", "20");
  });
});

describe("Provider keys", () => {
  it("lists keys masked, and adds one without showing the key again", async () => {
    api.providerKeys.mockResolvedValue([{ connection_id: "main", provider: "gc", base_url: "https://x", units: [{ model: "glm", input_per_mtok: 1, output_per_mtok: 2 }], allowed_data_classes: ["USER_PRIVATE"], key_hint: "4321", enabled: true, updated_by: "u1", updated_at: null }]);
    api.putProviderKey.mockResolvedValue({});
    await mount(<KeysPage />);
    expect(host!.textContent).toContain("••••4321");
    await click(btn("Add a key"));
    await fill(host!.querySelector("#k-cid") as HTMLInputElement, "second");
    await fill(host!.querySelector("#k-provider") as HTMLInputElement, "gc");
    await fill(host!.querySelector("#k-url") as HTMLInputElement, "https://api.gc.example");
    await fill(host!.querySelector("#k-key") as HTMLInputElement, "sk-SECRET-VALUE");
    await fill(host!.querySelector('input[aria-label="Model 1"]') as HTMLInputElement, "glm");
    await fill(host!.querySelector('input[aria-label^="Input cost"]') as HTMLInputElement, "0.5");
    await fill(host!.querySelector('input[aria-label^="Output cost"]') as HTMLInputElement, "1.5");
    await submit(host!.querySelectorAll("form")[0]);
    expect(api.putProviderKey).toHaveBeenCalledWith("o1", "second", expect.objectContaining({ provider: "gc", base_url: "https://api.gc.example", api_key: "sk-SECRET-VALUE", units: [{ model: "glm", input_per_mtok: 0.5, output_per_mtok: 1.5 }] }));
    expect(host!.textContent).not.toContain("sk-SECRET-VALUE");
  });
  it("replace without typing a key sends no api_key; remove asks first", async () => {
    api.providerKeys.mockResolvedValue([{ connection_id: "main", provider: "gc", base_url: "https://x", units: [{ model: "glm", input_per_mtok: 1, output_per_mtok: 2 }], allowed_data_classes: ["USER_PRIVATE"], key_hint: "4321", enabled: true, updated_by: "u1", updated_at: null }]);
    api.putProviderKey.mockResolvedValue({}); api.removeProviderKey.mockResolvedValue(undefined);
    await mount(<KeysPage />);
    await click(btn("Replace"));
    await submit(host!.querySelectorAll("form")[0]);
    expect("api_key" in (api.putProviderKey.mock.calls[0][2] as object)).toBe(false);
    await click(btn("Remove"));
    expect(api.removeProviderKey).not.toHaveBeenCalled();
    expect(host!.querySelector("dialog")!.hasAttribute("open")).toBe(true);
    await click([...host!.querySelectorAll("dialog button")].find((b) => b.textContent?.includes("Remove key"))!);
    expect(api.removeProviderKey).toHaveBeenCalledWith("o1", "main");
  });
});

describe("Activity", () => {
  it("filters calls by status and loads the next page", async () => {
    api.members.mockResolvedValue(members);
    const row = (id: string) => ({ id, created_at: "2026-10-28T10:00:00Z", user_id: "sub-ada", provider: "gc", model: "glm", scaffold: null, tool: "call_model", tier: null, status: "settled", data_class: "PUBLIC_SOURCE", tokens_input_fresh: 10, tokens_cache_read: 0, tokens_cache_write: 0, tokens_output: 5, cost_usd: "0.01", cost_source: "provider", provider_ms: 120, gate_ms: 3, error_type: null, instance_key: null, policy_decision: "allowed" });
    api.calls.mockResolvedValueOnce({ calls: [row("c1")], next_after: "cursor" }).mockResolvedValue({ calls: [row("c2")], next_after: null });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await mount(<ActivityPage />);
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(host!.textContent).toContain("Ada");
    await click(btn("Load more"));
    expect(api.calls.mock.calls[1][3]).toMatchObject({ after: "cursor" });
    expect(host!.querySelectorAll("tbody tr").length).toBe(2);
    const sel = [...host!.querySelectorAll("select")].find((s) => [...s.options].some((o) => o.value === "failed"))!;
    await act(async () => { sel.value = "failed"; sel.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(api.calls.mock.calls.at(-1)![3]).toMatchObject({ status: "failed" });
    vi.useRealTimers();
  });
});
