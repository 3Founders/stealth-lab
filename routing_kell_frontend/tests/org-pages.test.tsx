// @vitest-environment jsdom
// The organization pages with a mocked client: what each shows, what each sends, and what a role hides.
import { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = {
  budget: vi.fn(), usageByUser: vi.fn(), members: vi.fn(), onboarding: vi.fn(), killSwitch: vi.fn(), clientConfig: vi.fn(),
  invites: vi.fn(), createInvite: vi.fn(), revokeInvite: vi.fn(), policy: vi.fn(), putPolicy: vi.fn(), recommendedPolicy: vi.fn(),
  providerKeys: vi.fn(), putProviderKey: vi.fn(), removeProviderKey: vi.fn(), calls: vi.fn(), denials: vi.fn(),
  agents: vi.fn(), approvals: vi.fn(), setMemberRole: vi.fn(), removeMember: vi.fn(), createAgent: vi.fn(), revokeAgent: vi.fn(),
  roles: vi.fn(), audit: vi.fn(), updateAgent: vi.fn(), approveApproval: vi.fn(), denyApproval: vi.fn(),
};
let role: "owner" | "admin" | "member" = "owner";
vi.mock("@/components/org/OrgShell", () => ({
  useOrg: () => ({ api, org: { organization_id: "o1", name: "Acme", role, roles: [role] }, orgs: [], admin: role === "owner" || role === "admin" }),
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("next/navigation", () => ({ notFound: () => { throw new Error("NEXT_NOT_FOUND"); }, usePathname: () => "/org" }));

import OrgHome from "@/app/org/page";
import MembersPage from "@/app/org/members/page";
import AgentsPage from "@/app/org/agents/page";
import ApprovalsPage from "@/app/org/approvals/page";
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
beforeEach(() => {
  api.roles.mockResolvedValue(roleCatalog);
  api.audit.mockResolvedValue({ events: [], chain_intact: true });
  api.agents.mockResolvedValue([]);
});
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; role = "owner"; Object.values(api).forEach((m) => m.mockReset()); vi.unstubAllEnvs(); });

const members = [{ user_id: "u1", subject: "sub-ada", display_name: "Ada", email: "ada@acme.co", is_active: true, roles: ["owner"], member_since: "2026-01-02T00:00:00Z" }];
const roleCatalog = {
  organization_id: "o1",
  roles: [
    { id: "owner", label: "Owner", summary: "Everything, including billing.", capabilities: ["view_org", "manage_members", "manage_admins", "billing"], members: 1 },
    { id: "admin", label: "Admin", summary: "Runs the organization day to day.", capabilities: ["view_org", "manage_members"], members: 0 },
    { id: "member", label: "Member", summary: "Uses the organization's models.", capabilities: ["view_org"], members: 0 },
    { id: "viewer", label: "Viewer", summary: "Read-only access.", capabilities: ["view_org"], members: 0 },
  ],
  capabilities: [
    { id: "view_org", label: "See the organization", roles: ["owner", "admin", "member", "viewer"] },
    { id: "manage_members", label: "Invite and remove people", roles: ["owner", "admin"] },
    { id: "manage_admins", label: "Change another administrator", roles: ["owner"] },
    { id: "billing", label: "Billing", roles: ["owner"] },
  ],
  members: 1,
};
const policy = (over = {}) => ({ organization_id: "o1", kill_switch: false, kill_switch_reason: null, allowed_providers: ["gc"], allowed_models: ["glm"], allowed_tools: ["call_model"], allowed_data_classes: ["USER_PRIVATE"], monthly_budget_usd: "100", per_user_daily_budget_usd: "5", version: 3, ...over });

describe("flag", () => {
  it("/join does not exist while the flag is off", () => {
    expect(() => JoinPage()).toThrow("NEXT_NOT_FOUND");
  });
});

describe("Org home", () => {
  beforeEach(() => { api.agents.mockResolvedValue([]); });
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
    api.invites.mockResolvedValue([{ id: "i1", label: "Team", role: "member", email: null, created_by: "u1", created_at: "2026-10-01T00:00:00Z", expires_at: "2026-10-08T00:00:00Z", max_uses: 5, uses: 1, revoked_at: null, email_sent_at: null, active: true }]);
    api.createInvite.mockResolvedValue({ id: "i2", code: "SECRET-CODE-123", expires_at: "2026-11-01T00:00:00Z", max_uses: 50, label: null, role: "member", email: null, email_sent: false, email_sent_at: null });
    api.revokeInvite.mockResolvedValue(undefined);
    await mount(<MembersPage />);
    expect(host!.textContent).toContain("Ada");
    expect(host!.textContent).toContain("Roles and permissions");
    expect(host!.textContent).toContain("Invite and remove people");
    await submit(host!.querySelectorAll("form")[0]);
    expect(api.createInvite).toHaveBeenCalledWith("o1", { label: undefined, email: undefined, days: 7, max_uses: 50, role: "member" });
    expect(host!.textContent).toContain("SECRET-CODE-123");
    expect(host!.textContent).toContain("shown once");
    await click(btn("Revoke"));
    expect(api.revokeInvite).toHaveBeenCalledWith("o1", "i1");
  });

  it("an admin cannot change an owner's role or offer the owner role", async () => {
    api.members.mockResolvedValue(members);
    api.invites.mockResolvedValue([]);
    role = "admin";
    await mount(<MembersPage />);
    const select = host!.querySelector("select[aria-label^='Role for']") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect([...select.options].map((o) => o.value)).not.toContain("owner");
  });

  it("an owner changes a member's role", async () => {
    api.members.mockResolvedValue(members);
    api.invites.mockResolvedValue([]);
    api.setMemberRole.mockResolvedValue({ user_id: "u1", subject: "sub-ada", role: "member" });
    await mount(<MembersPage />);
    const select = host!.querySelector("select[aria-label^='Role for']") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
    await act(async () => { select.value = "member"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(api.setMemberRole).toHaveBeenCalledWith("o1", "u1", "member");
  });

  it("sends an invite to an address and says when delivery did not happen", async () => {
    api.members.mockResolvedValue(members);
    api.invites.mockResolvedValue([]);
    api.createInvite.mockResolvedValue({
      id: "i3", code: "SECRET-CODE-456", expires_at: "2026-11-01T00:00:00Z", max_uses: 1,
      label: null, role: "member", email: "new@acme.co", email_sent: false, email_sent_at: null,
    });
    await mount(<MembersPage />);
    await fill(host!.querySelector("#inv-email") as HTMLInputElement, "new@acme.co");
    await submit(host!.querySelectorAll("form")[0]);
    expect(api.createInvite).toHaveBeenCalledWith("o1", expect.objectContaining({ email: "new@acme.co" }));
    expect(host!.textContent).toContain("was not sent");
  });
});

describe("Agents", () => {
  const agent = {
    id: "a1", name: "reviewer", kind: "agent", owner_sub: "sub-ada", owner_name: "Ada", owner_email: "ada@acme.co",
    created_at: "2026-10-01T00:00:00Z", last_seen: "2026-10-28T10:00:00Z", revoked_at: null, status: "online" as const,
    online: true, scopes: ["read"], data_ceiling: "PUBLIC_SOURCE", budget_usd_month: "20", expires_at: null,
  };

  it("lists the owner, status and recorded containment", async () => {
    api.agents.mockResolvedValue([agent, { ...agent, id: "a2", name: "old", status: "expired" as const, online: false }]);
    await mount(<AgentsPage />);
    expect(host!.textContent).toContain("Ada");
    expect(host!.textContent).toContain("Online");
    expect(host!.textContent).toContain("Expired");
    expect(host!.textContent).toContain("$20.00");
  });

  it("adds an agent with scopes, a ceiling, a budget and an expiry", async () => {
    api.createAgent.mockResolvedValue(agent);
    await mount(<AgentsPage />);
    await fill(host!.querySelector("#ag-name") as HTMLInputElement, "reviewer");
    await fill(host!.querySelector("#ag-scopes") as HTMLInputElement, "read, call_model");
    await fill(host!.querySelector("#ag-budget") as HTMLInputElement, "20");
    await fill(host!.querySelector("#ag-expires") as HTMLInputElement, "30");
    await submit(host!.querySelector("form")!);
    expect(api.createAgent).toHaveBeenCalledWith("o1", {
      name: "reviewer", kind: "agent", data_ceiling: "PUBLIC_SOURCE",
      scopes: ["read", "call_model"], budget_usd_month: "20", expires_days: 30,
    });
  });

  it("edits the containment without revoking, and revokes only after a confirm", async () => {
    api.agents.mockResolvedValue([agent]);
    api.updateAgent.mockResolvedValue({ ...agent, budget_usd_month: "40", scopes: ["read", "call_model"] });
    api.revokeAgent.mockResolvedValue(undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await mount(<AgentsPage />);
    await click(btn("Edit"));
    expect(host!.querySelector("dialog")!.hasAttribute("open")).toBe(true);
    await fill(host!.querySelector("#ed-scopes") as HTMLInputElement, "read, call_model");
    await fill(host!.querySelector("#ed-budget") as HTMLInputElement, "40");
    await submit(host!.querySelector("dialog form")!);
    expect(api.updateAgent).toHaveBeenCalledWith("o1", "a1", {
      scopes: ["read", "call_model"], data_ceiling: "PUBLIC_SOURCE", budget_usd_month: "40",
    });
    await click(btn("Revoke"));
    expect(api.revokeAgent).toHaveBeenCalledWith("o1", "a1");
    confirm.mockRestore();
  });

  it("a member sees no agent table and makes no admin request", async () => {
    role = "member";
    await mount(<AgentsPage />);
    expect(host!.textContent).toContain("visible to owners and admins");
    expect(api.agents).not.toHaveBeenCalled();
  });
});

describe("Approvals", () => {
  const approval = {
    id: "ap_1", owner_id: "sub-ada", task_id: "t1", goal_id: null, server: "github", tool: "create_issue",
    args_hash: "sha256:0123456789abcdef", risk: "R2", data_class: "PUBLIC_SOURCE", cert_id: null,
    status: "pending" as const, requested_at: "2026-10-28T10:00:00Z", decided_at: null,
    expires_at: "2026-10-28T10:15:00Z", used_at: null, decided_by: null, note: null,
    owner_name: "Ada", owner_email: "ada@acme.co",
  };

  it("shows the exact call and allows or denies it", async () => {
    api.approvals.mockResolvedValue([approval]);
    api.approveApproval.mockResolvedValue({ id: "ap_1", status: "approved" });
    api.denyApproval.mockResolvedValue({ id: "ap_1", status: "denied" });
    await mount(<ApprovalsPage />);
    expect(host!.textContent).toContain("create_issue");
    expect(host!.textContent).toContain("Ada");
    expect(host!.textContent).toContain("sha256:0123456789ab");
    await click(btn("Allow once"));
    expect(api.approveApproval).toHaveBeenCalledWith("o1", "ap_1");
    await click(btn("Deny"));
    expect(api.denyApproval).toHaveBeenCalledWith("o1", "ap_1");
  });

  it("filters by status and explains an empty queue", async () => {
    api.approvals.mockResolvedValue([]);
    await mount(<ApprovalsPage />);
    expect(host!.textContent).toContain("Nothing is waiting on you.");
    await click(btn("Denied"));
    expect(api.approvals).toHaveBeenLastCalledWith("o1", "denied");
    expect(host!.textContent).toContain("No denied approvals.");
  });

  it("a member sees no approval table and makes no admin request", async () => {
    role = "member";
    await mount(<ApprovalsPage />);
    expect(host!.textContent).toContain("visible to owners and admins");
    expect(api.approvals).not.toHaveBeenCalled();
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
