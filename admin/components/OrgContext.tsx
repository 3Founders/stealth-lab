"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ApiState } from "@/lib/api";
import { getMyOrgs, type Org } from "@/lib/org-api";

interface OrgCtx {
  orgs: ApiState<Org[]>;
  current: Org | undefined;
  select: (organizationId: string) => void;
}

const Ctx = createContext<OrgCtx>({ orgs: { kind: "loading" }, current: undefined, select: () => {} });
const KEY = "kel-admin-org";

/** The signed-in user's organisations, and which one the console is acting on. The choice is a per-browser
 * convenience only (localStorage); the backend re-checks membership and role on every request. */
export function OrgProvider({ children }: { children: ReactNode }) {
  const [orgs, setOrgs] = useState<ApiState<Org[]>>({ kind: "loading" });
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    try { setSelected(window.localStorage.getItem(KEY)); } catch { /* storage blocked: first org is used */ }
    const ac = new AbortController();
    getMyOrgs(ac.signal).then(setOrgs);
    return () => ac.abort();
  }, []);

  const select = useCallback((id: string) => {
    setSelected(id);
    try { window.localStorage.setItem(KEY, id); } catch { /* ignore */ }
  }, []);

  const value = useMemo<OrgCtx>(() => {
    const list = orgs.kind === "ok" ? orgs.data : [];
    return { orgs, select, current: list.find((x) => x.organization_id === selected) ?? list[0] };
  }, [orgs, selected, select]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useOrg = () => useContext(Ctx);
