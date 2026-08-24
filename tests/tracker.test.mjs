import assert from "node:assert/strict"
import { test } from "node:test"
import { loadQmlJs } from "./harness.mjs"

const Tracker = loadQmlJs("lib/Tracker.js")

const INTERVAL = 5000
const opts = (now, appId, active) => ({
  now,
  appId,
  active,
  intervalMs: INTERVAL,
  maxStepMs: INTERVAL * 4
})

// A fixed local noon so the tests never straddle a real midnight.
const noon = new Date(2026, 7, 24, 12, 0, 0, 0).getTime()

function bufferOf(state, day) {
  return state.buffer[day] || {}
}

test("dayKeyOf uses local time, not UTC", () => {
  const lateEvening = new Date(2026, 7, 24, 23, 30, 0, 0).getTime()
  assert.equal(Tracker.dayKeyOf(lateEvening), "2026-08-24")
  const earlyMorning = new Date(2026, 0, 1, 0, 15, 0, 0).getTime()
  assert.equal(Tracker.dayKeyOf(earlyMorning), "2026-01-01")
})

test("the first observation only starts the clock", () => {
  const state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  assert.equal(state.pendingSeconds, 0)
  assert.equal(state.lastAppId, "firefox")
  assert.equal(state.lastActive, true)
})

test("elapsed time is credited to the app that held focus during the window", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  // Focus moves to ghostty five seconds later: those five seconds were firefox.
  state = Tracker.observe(state, opts(noon + INTERVAL, "ghostty", true))
  assert.deepEqual(bufferOf(state, "2026-08-24"), { firefox: 5 })
  state = Tracker.observe(state, opts(noon + INTERVAL * 2, "ghostty", true))
  assert.deepEqual(bufferOf(state, "2026-08-24"), { firefox: 5, ghostty: 5 })
})

test("nothing accrues while the seat is idle", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", false))
  state = Tracker.observe(state, opts(noon + INTERVAL, "firefox", false))
  assert.equal(Tracker.isEmpty(state), true)
  assert.equal(state.pendingSeconds, 0)
})

test("nothing accrues when no window has focus", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "", true))
  state = Tracker.observe(state, opts(noon + INTERVAL, "", true))
  assert.equal(Tracker.isEmpty(state), true)
})

test("going idle stops the clock but keeps what was already earned", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  state = Tracker.observe(state, opts(noon + INTERVAL, "firefox", true))
  assert.deepEqual(bufferOf(state, "2026-08-24"), { firefox: 5 })
  // Idle now: this window still belongs to firefox, the next one will not.
  state = Tracker.observe(state, opts(noon + INTERVAL * 2, "firefox", false))
  assert.deepEqual(bufferOf(state, "2026-08-24"), { firefox: 10 })
  state = Tracker.observe(state, opts(noon + INTERVAL * 3, "firefox", false))
  assert.deepEqual(bufferOf(state, "2026-08-24"), { firefox: 10 })
})

test("a suspend-sized gap is dropped rather than credited", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  // Laptop lid closed for eight hours.
  state = Tracker.observe(state, opts(noon + 8 * 3600 * 1000, "firefox", true))
  assert.equal(Tracker.isEmpty(state), true)
  assert.equal(state.droppedGaps, 1)
  // The clock resumes cleanly afterwards.
  state = Tracker.observe(state, opts(noon + 8 * 3600 * 1000 + INTERVAL, "firefox", true))
  assert.deepEqual(bufferOf(state, "2026-08-24"), { firefox: 5 })
})

test("a backwards clock step is dropped", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  state = Tracker.observe(state, opts(noon - 60000, "firefox", true))
  assert.equal(Tracker.isEmpty(state), true)
  assert.equal(state.droppedGaps, 1)
})

test("a window spanning midnight splits across both days", () => {
  const beforeMidnight = new Date(2026, 7, 24, 23, 59, 58, 0).getTime()
  let state = Tracker.observe(Tracker.emptyState(), opts(beforeMidnight, "ghostty", true))
  state = Tracker.observe(state, opts(beforeMidnight + 4000, "ghostty", true))
  assert.deepEqual(bufferOf(state, "2026-08-24"), { ghostty: 2 })
  assert.deepEqual(bufferOf(state, "2026-08-25"), { ghostty: 2 })
  assert.equal(state.pendingSeconds, 4)
})

test("the screensaver is never counted, even though it inhibits idle", () => {
  const rules = Tracker.ignoreRules("")
  assert.equal(Tracker.isIgnored("org.omarchy.screensaver", rules), true)
  assert.equal(Tracker.isIgnored("hyprlock", rules), true)
  assert.equal(Tracker.isIgnored("swaylock", rules), true)
  assert.equal(Tracker.isIgnored("firefox", rules), false)
  assert.equal(Tracker.isIgnored("com.mitchellh.ghostty", rules), false)
})

test("ignore matching is case-insensitive and ignores nothing-focused", () => {
  const rules = Tracker.ignoreRules("")
  assert.equal(Tracker.isIgnored("ORG.OMARCHY.SCREENSAVER", rules), true)
  assert.equal(Tracker.isIgnored("HyprLock", rules), true)
  assert.equal(Tracker.isIgnored("", rules), true)
  assert.equal(Tracker.isIgnored(null, rules), true)
})

test("user rules are added to the built-ins, never replace them", () => {
  const rules = Tracker.ignoreRules("spotify, Files")
  assert.equal(Tracker.isIgnored("spotify", rules), true)
  assert.equal(Tracker.isIgnored("files", rules), true)
  // The built-in system surfaces still apply.
  assert.equal(Tracker.isIgnored("org.omarchy.screensaver", rules), true)
  assert.equal(Tracker.isIgnored("firefox", rules), false)
})

test("ignore rules accept an array and whitespace separation", () => {
  assert.equal(Tracker.isIgnored("foo", Tracker.ignoreRules(["foo"])), true)
  assert.equal(Tracker.isIgnored("bar", Tracker.ignoreRules("foo bar")), true)
  assert.equal(Tracker.isIgnored("bar", Tracker.ignoreRules("foo,,  bar ,")), true)
})

test("a trailing star matches a prefix", () => {
  const rules = Tracker.ignoreRules("steam_app_*")
  assert.equal(Tracker.isIgnored("steam_app_570", rules), true)
  assert.equal(Tracker.isIgnored("steam_app_", rules), true)
  assert.equal(Tracker.isIgnored("steam", rules), false)
})

test("an empty or missing setting still ignores the system surfaces", () => {
  for (const value of ["", null, undefined, "   ", ","]) {
    const rules = Tracker.ignoreRules(value)
    assert.equal(Tracker.isIgnored("org.omarchy.screensaver", rules), true,
      `value ${JSON.stringify(value)}`)
    assert.equal(Tracker.isIgnored("firefox", rules), false)
  }
})

test("defaultIgnoreList hands out a copy, not the shared array", () => {
  const first = Tracker.defaultIgnoreList()
  first.push("firefox")
  assert.equal(Tracker.isIgnored("firefox", Tracker.defaultIgnoreList()), false)
})

test("ignored apps accrue nothing even while focused and active", () => {
  const rules = Tracker.ignoreRules("")
  const active = (appId) => !Tracker.isIgnored(appId, rules)

  let state = Tracker.observe(Tracker.emptyState(),
    opts(noon, "org.omarchy.screensaver", active("org.omarchy.screensaver")))
  state = Tracker.observe(state,
    opts(noon + INTERVAL, "org.omarchy.screensaver", active("org.omarchy.screensaver")))
  assert.equal(Tracker.isEmpty(state), true)

  // Waking up to a real app starts billing again.
  state = Tracker.observe(state, opts(noon + INTERVAL * 2, "firefox", active("firefox")))
  state = Tracker.observe(state, opts(noon + INTERVAL * 3, "firefox", active("firefox")))
  assert.deepEqual(bufferOf(state, "2026-08-24"), { firefox: 5 })
})

test("composeKey joins an app and its detail, and omits an empty detail", () => {
  assert.equal(Tracker.composeKey("com.mitchellh.ghostty", "opencode"),
    "com.mitchellh.ghostty/opencode")
  assert.equal(Tracker.composeKey("helium", "YouTube"), "helium/YouTube")
  assert.equal(Tracker.composeKey("firefox", ""), "firefox")
  assert.equal(Tracker.composeKey("firefox", null), "firefox")
  assert.equal(Tracker.composeKey("", "opencode"), "")
})

test("keyApp and keyDetail split a store key apart", () => {
  assert.equal(Tracker.keyApp("com.mitchellh.ghostty/opencode"), "com.mitchellh.ghostty")
  assert.equal(Tracker.keyDetail("com.mitchellh.ghostty/opencode"), "opencode")
  assert.equal(Tracker.keyApp("firefox"), "firefox")
  assert.equal(Tracker.keyDetail("firefox"), "")
  // Only the first slash is a boundary, so a detail with a slash cannot
  // smuggle in a fake app.
  assert.equal(Tracker.keyApp("helium/a/b"), "helium")
  assert.equal(Tracker.keyDetail("helium/a/b"), "a/b")
})

test("normalizeDetail strips slashes, control characters, and over-long text", () => {
  assert.equal(Tracker.normalizeDetail("opencode"), "opencode")
  assert.equal(Tracker.normalizeDetail("  YouTube  "), "YouTube")
  assert.equal(Tracker.normalizeDetail("a/b"), "a b")
  assert.equal(Tracker.normalizeDetail("a\nb\tc"), "a b c")
  assert.equal(Tracker.normalizeDetail(""), "")
  assert.equal(Tracker.normalizeDetail(null), "")
  assert.equal(Tracker.normalizeDetail("x".repeat(200)).length, 40)
})

test("normalizeKey keeps the app/detail boundary that normalizeAppId destroys", () => {
  // normalizeAppId strips paths by keeping the last component, which would
  // reduce a composite key to just its detail.
  assert.equal(Tracker.normalizeAppId("ghostty/opencode"), "opencode")
  assert.equal(Tracker.normalizeKey("ghostty/opencode"), "ghostty/opencode")
  assert.equal(Tracker.normalizeKey("firefox"), "firefox")
  assert.equal(Tracker.normalizeKey(""), "")
  // A real path is still treated as a path, not as a composite key.
  assert.equal(Tracker.normalizeKey("/usr/bin/ghostty"), "ghostty")
})

test("detail is tracked as its own row, and switching detail attributes cleanly", () => {
  const key = (app, detail) => Tracker.composeKey(app, detail)
  let state = Tracker.observe(Tracker.emptyState(),
    opts(noon, key("ghostty", "opencode"), true))
  state = Tracker.observe(state, opts(noon + INTERVAL, key("ghostty", "opencode"), true))
  assert.deepEqual(bufferOf(state, "2026-08-24"), { "ghostty/opencode": 5 })

  // Same terminal, different program: a separate row, and the first five
  // seconds stay with opencode.
  state = Tracker.observe(state, opts(noon + INTERVAL * 2, key("ghostty", "claude"), true))
  state = Tracker.observe(state, opts(noon + INTERVAL * 3, key("ghostty", "claude"), true))
  assert.deepEqual(bufferOf(state, "2026-08-24"), {
    "ghostty/opencode": 10,
    "ghostty/claude": 5
  })
})

test("browser sites accrue separately from each other", () => {
  let state = Tracker.rebase(Tracker.emptyState(), noon)
  // 30 minutes of YouTube, then 5 of GitHub.
  for (let i = 1; i <= 360; i++) {
    state = Tracker.observe(state, opts(noon + i * INTERVAL, "helium/YouTube", true))
  }
  for (let i = 361; i <= 420; i++) {
    state = Tracker.observe(state, opts(noon + i * INTERVAL, "helium/GitHub", true))
  }
  const day = bufferOf(state, "2026-08-24")
  assert.equal(day["helium/YouTube"], 1800)
  assert.equal(day["helium/GitHub"], 295)
})

test("an ignored app stays ignored whatever detail is attached to it", () => {
  const rules = Tracker.ignoreRules("")
  assert.equal(Tracker.isIgnored("org.omarchy.screensaver/anything", rules), true)
  assert.equal(Tracker.isIgnored("hyprlock/~", rules), true)
  assert.equal(Tracker.isIgnored("helium/YouTube", rules), false)
})

test("a user ignore rule matches the app, not the detail", () => {
  const rules = Tracker.ignoreRules("helium")
  assert.equal(Tracker.isIgnored("helium/YouTube", rules), true)
  // The detail alone must not be enough to trigger a rule.
  assert.equal(Tracker.isIgnored("firefox/helium", rules), false)
})

test("normalizeAppId trims, strips paths, and bounds length", () => {
  assert.equal(Tracker.normalizeAppId("  firefox  "), "firefox")
  assert.equal(Tracker.normalizeAppId("/usr/bin/ghostty"), "ghostty")
  assert.equal(Tracker.normalizeAppId(null), "")
  assert.equal(Tracker.normalizeAppId(undefined), "")
  assert.equal(Tracker.normalizeAppId("x".repeat(500)).length, 128)
})

test("commitPayload emits whole seconds keyed by day, dropping empties", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  state = Tracker.observe(state, opts(noon + INTERVAL, "firefox", true))
  const payload = Tracker.commitPayload(state)
  assert.deepEqual(payload, { days: { "2026-08-24": { firefox: 5 } } })
  assert.deepEqual(Tracker.commitPayload(Tracker.emptyState()), { days: {} })
})

test("drained clears the buffer but keeps the clock running", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  state = Tracker.observe(state, opts(noon + INTERVAL, "firefox", true))
  const after = Tracker.drained(state)
  assert.equal(Tracker.isEmpty(after), true)
  assert.equal(after.pendingSeconds, 0)
  assert.equal(after.lastAt, state.lastAt)
  assert.equal(after.lastAppId, "firefox")
  assert.equal(after.lastActive, true)
})

test("observe never mutates the state it was given", () => {
  const first = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  const snapshot = JSON.stringify(first)
  Tracker.observe(first, opts(noon + INTERVAL, "firefox", true))
  assert.equal(JSON.stringify(first), snapshot)
})

test("liveTotal adds the uncommitted buffer to the stored total", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  state = Tracker.observe(state, opts(noon + INTERVAL, "firefox", true))
  assert.equal(Tracker.liveTotal(3600, state, "2026-08-24"), 3605)
  assert.equal(Tracker.liveTotal(3600, state, "2026-08-25"), 3600)
  assert.equal(Tracker.liveTotal(0, Tracker.emptyState(), "2026-08-24"), 0)
})

test("rebase resets the clock without crediting anything", () => {
  let state = Tracker.observe(Tracker.emptyState(), opts(noon, "firefox", true))
  const rebased = Tracker.rebase(state, noon + 99999)
  assert.equal(rebased.lastAt, noon + 99999)
  assert.equal(rebased.lastActive, false)
  assert.equal(Tracker.isEmpty(rebased), true)
})

test("an hour of alternating focus adds up to an hour", () => {
  let state = Tracker.rebase(Tracker.emptyState(), noon)
  const apps = ["firefox", "ghostty", "code"]
  for (let step = 1; step <= 720; step++) {
    state = Tracker.observe(state, opts(noon + step * INTERVAL, apps[step % 3], true))
  }
  const day = bufferOf(state, "2026-08-24")
  const total = Object.values(day).reduce((sum, value) => sum + value, 0)
  assert.equal(total, 3600 - 5) // the final open window is not yet closed
  assert.equal(state.pendingSeconds, 3595)
})
