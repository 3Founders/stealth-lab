// Types for lib/engine.ts's @opencode/client use, kept out of any module webpack bundles: a `typeof import("@opencode/client")`
// written in engine.ts itself makes Next's webpack resolve the package (ESM-only, and webpack cannot read its "." export for a
// CJS context) even though the reference is erased. A .d.ts is type-only by definition, so nothing here is resolved at build time.
export type OC = typeof import("@opencode/client")
export type Client = ReturnType<OC["OpenCode"]["make"]>
