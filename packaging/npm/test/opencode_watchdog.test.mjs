import test from "node:test"
import assert from "node:assert/strict"
import { mock } from "node:test"
import path from "node:path"
import { createWatchdog, opencodeLogDir } from "../lib/opencode/lib/watchdog.mjs"

test("opencodeLogDir: XDG wins, then the platform fallbacks", () => {
  assert.equal(opencodeLogDir({ XDG_DATA_HOME: "/xdg/data" }, "linux"), path.join("/xdg/data", "opencode", "log"))
  assert.equal(opencodeLogDir({ LOCALAPPDATA: "C:\\Users\\u\\AppData\\Local" }, "win32"), path.join("C:\\Users\\u\\AppData\\Local", "opencode", "log"))
  assert.equal(opencodeLogDir({ HOME: "/home/u" }, "linux"), path.join("/home/u", ".local", "share", "opencode", "log"))
})

test("no log before 60 s of silence", async () => {
  mock.timers.enable({ apis: ["setTimeout"] })
  try {
    const lines = []
    const wd = createWatchdog({ log: (l) => lines.push(l) })
    wd.arm("s1")
    mock.timers.tick(59_999)
    assert.equal(lines.length, 0)
    mock.timers.tick(1)
    assert.equal(lines.length, 1)
    assert.match(lines[0], /no progress for 60 s in session s1; see OpenCode's logs at/)
    assert.ok(lines[0].includes(path.join("opencode", "log")))
  } finally { mock.timers.reset() }
})

test("one timer per session: re-arming restarts it, sessions are independent", async () => {
  mock.timers.enable({ apis: ["setTimeout"] })
  try {
    const lines = []
    const wd = createWatchdog({ log: (l) => lines.push(l) })
    wd.arm("s1")
    mock.timers.tick(30_000)
    wd.arm("s1")                       // progress arrived: the clock starts again
    mock.timers.tick(59_999)           // t = 89,999
    assert.equal(lines.length, 0)
    mock.timers.tick(1)                // t = 90,000: s1 fires, 60 s after its last arm
    assert.equal(lines.length, 1)
    assert.match(lines[0], /session s1/)
    wd.arm("s2")                       // s2's own timer, armed later, is independent
    mock.timers.tick(59_999)
    assert.equal(lines.length, 1)
    mock.timers.tick(1)                // t = 150,000: s2 fires
    assert.equal(lines.length, 2)
    assert.equal(lines[0].includes("session s1"), true); assert.equal(lines[1].includes("session s2"), true)
  } finally { mock.timers.reset() }
})

test("a fire is said once, not at every further 60 s of silence", async () => {
  mock.timers.enable({ apis: ["setTimeout"] })
  try {
    const lines = []
    const wd = createWatchdog({ log: (l) => lines.push(l) })
    wd.arm("s1")
    mock.timers.tick(60_000)
    mock.timers.tick(120_000)
    assert.equal(lines.length, 1)
  } finally { mock.timers.reset() }
})

test("clear ends the timer: nothing writes between turns", async () => {
  mock.timers.enable({ apis: ["setTimeout"] })
  try {
    const lines = []
    const wd = createWatchdog({ log: (l) => lines.push(l) })
    wd.arm("s1")
    mock.timers.tick(30_000)
    wd.clear("s1")                     // experimental.idle: the turn ended
    mock.timers.tick(120_000)
    assert.equal(lines.length, 0)
    wd.arm("s1")                       // a later turn arms it again
    mock.timers.tick(60_000)
    assert.equal(lines.length, 1)
  } finally { mock.timers.reset() }
})
