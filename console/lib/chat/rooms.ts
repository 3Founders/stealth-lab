import type { Room } from "./types";

/** Who may do what in a room. The same three roles as the signed-in chat (routing_kell_frontend), and the same rules; here they only govern the sandbox. */
export type Role = "owner" | "admin" | "member";

export const MAX_ROOMS = 12;

/** "Design Review" becomes "#design-review"; the sandbox keeps room names to letters, digits and dashes. */
export function newRoomId(existing: string[], raw: string): { id: string } | { error: string } {
  const name = raw.trim().replace(/^#/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 30);
  if (!name) return { error: "Give the room a name." };
  if (existing.length >= MAX_ROOMS) return { error: `You can have at most ${MAX_ROOMS} rooms here.` };
  if (existing.includes(`#${name}`)) return { error: `You already have #${name}.` };
  return { id: `#${name}` };
}

/** Only the owner changes roles, and the owner's own role is fixed. */
export const canChangeRole = (actor: Role | undefined, target: Role | undefined) => actor === "owner" && target !== undefined && target !== "owner";

/** The owner removes anyone but themselves; an admin removes plain members only; anyone may leave, except the owner (who dissolves the room). */
export function canRemove(actor: Role | undefined, target: Role | undefined, self: boolean): boolean {
  if (!actor || !target) return false;
  if (self) return actor !== "owner";
  if (target === "owner") return false;
  return actor === "owner" || (actor === "admin" && target === "member");
}

export const canDissolve = (actor: Role | undefined) => actor === "owner";

export type Roles = Record<string, Record<string, Role>>;

/** Everyone who is a person (not an agent handle, which has a slash) starts as a member; the owner is the one named. */
export function seedRoles(rooms: Room[], owner: string): Roles {
  return Object.fromEntries(rooms.map((r) => [r.id, Object.fromEntries(r.members.filter((h) => !h.includes("/")).map((h) => [h, h === owner ? "owner" : "member"] as [string, Role]))]));
}
