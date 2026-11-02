// Runs once when the Next server starts. The desktop shell starts this server as a child and passes its own pid; if that
// pid disappears (the shell was killed outright, so its exit handlers never ran) the server and the OpenCode engine it owns
// would be orphaned and keep their memory until reboot. Poll the parent and take the engine down with us.
export function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return
  const parent = Number(process.env.KELL_PARENT_PID || 0)
  if (!parent) return
  const g = globalThis as typeof globalThis & { __kellParentWatch?: unknown; __kellEngineState?: { child?: { kill(): void } } }
  if (g.__kellParentWatch) return
  g.__kellParentWatch = setInterval(() => {
    try { process.kill(parent, 0) } catch (e: any) {
      if (e?.code === "EPERM") return // alive, owned by another user
      try { g.__kellEngineState?.child?.kill() } catch { /* already gone */ }
      process.exit(0)
    }
  }, 5000)
  ;(g.__kellParentWatch as { unref?: () => void }).unref?.()
}
