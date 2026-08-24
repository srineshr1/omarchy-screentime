.pragma library

// Pure accrual state machine for the screen time tracker.
//
// Service.qml owns the side effects (timers, IdleMonitor, ToplevelManager,
// spawning the helper). This file owns the arithmetic, so the rules about
// what counts as screen time are testable without a compositor.
//
// The rules:
//   * Elapsed time belongs to the app that held focus *during* the window,
//     which is the previously observed app, not the one focused right now.
//   * Nothing accrues while the seat is idle or while no window has focus.
//   * A tick that arrives far later than expected is a gap, not usage: the
//     machine suspended, or the shell stalled. Gaps are dropped whole.
//   * Buffers are keyed by local calendar day, so a session running through
//     midnight splits across two days on its own.

// Surfaces that are never screen time, however long they hold focus.
//
// The screensaver is the important one: it is a real toplevel and it holds an
// idle inhibitor while it runs, so the idle monitor stays "active" and the
// clock would otherwise bill you for sitting in front of a screensaver.
var SYSTEM_SURFACES = [
  "org.omarchy.screensaver",
  "hyprlock",
  "swaylock",
  "gtklock",
  "waylock",
  "org.omarchy.lock"
]

function defaultIgnoreList() {
  return SYSTEM_SURFACES.slice()
}

/**
 * The full ignore list: the built-in system surfaces plus whatever the user
 * added. Additive on purpose — nobody wants to opt back in to counting their
 * lock screen, and an additive list has no "how do I clear it" question.
 *
 * `extra` may be an array or a comma/space separated string.
 */
function ignoreRules(extra) {
  var rules = defaultIgnoreList()
  var parts = []
  if (extra === undefined || extra === null) parts = []
  else if (Array.isArray(extra)) parts = extra
  else parts = String(extra).split(/[,\s]+/)
  for (var i = 0; i < parts.length; i++) {
    var entry = String(parts[i]).replace(/^\s+|\s+$/g, "").toLowerCase()
    if (entry.length) rules.push(entry)
  }
  return rules
}

/** Exact match, or prefix match when a rule ends in "*".
 *
 * Accepts a bare app id or a composite "app/detail" key; the rules always
 * apply to the app, so ignoring the screensaver cannot be sidestepped by
 * whatever detail happened to be resolved for it.
 */
function isIgnored(appId, rules) {
  var id = keyApp(normalizeKey(appId)).toLowerCase()
  if (!id.length) return true
  var list = rules && rules.length ? rules : SYSTEM_SURFACES
  for (var i = 0; i < list.length; i++) {
    var rule = String(list[i]).toLowerCase()
    if (!rule.length) continue
    if (rule.charAt(rule.length - 1) === "*") {
      if (id.indexOf(rule.slice(0, rule.length - 1)) === 0) return true
    } else if (id === rule) {
      return true
    }
  }
  return false
}

function emptyState() {
  return {
    buffer: {},        // { "YYYY-MM-DD": { appId: seconds } }
    lastAt: 0,         // ms timestamp of the last observation
    lastAppId: "",     // app focused during the window that ends at lastAt
    lastActive: false, // whether that window counted
    pendingSeconds: 0, // buffered seconds not yet committed
    droppedGaps: 0     // observations discarded as suspend/stall gaps
  }
}

// Local calendar day for a ms timestamp. Deliberately not toISOString():
// that is UTC, and would file late-evening usage under tomorrow.
function dayKeyOf(millis) {
  var when = new Date(millis)
  var month = when.getMonth() + 1
  var day = when.getDate()
  return when.getFullYear() + "-" + (month < 10 ? "0" : "") + month + "-" + (day < 10 ? "0" : "") + day
}

function normalizeAppId(value) {
  var raw = String(value === undefined || value === null ? "" : value)
  raw = raw.replace(/^\s+|\s+$/g, "")
  if (!raw.length) return ""
  // Toplevels occasionally report a trailing instance suffix or stray path.
  if (raw.indexOf("/") >= 0) raw = raw.split("/").pop()
  return raw.slice(0, 128)
}

/**
 * Detail is appended to an app id after a slash to form the store key, so it
 * must not contain one itself, and it has to stay short enough to read in a
 * bar popup.
 */
function normalizeDetail(value) {
  var raw = String(value === undefined || value === null ? "" : value)
  raw = raw.replace(/[\/\r\n\t\x00]+/g, " ").replace(/\s+/g, " ")
  raw = raw.replace(/^\s+|\s+$/g, "")
  if (!raw.length) return ""
  return raw.slice(0, 40)
}

/** "app" or "app/detail". */
function composeKey(appId, detail) {
  var app = normalizeAppId(appId)
  if (!app.length) return ""
  var extra = normalizeDetail(detail)
  return extra.length ? app + "/" + extra : app
}

/** The app half of a store key, which is what ignore rules apply to. */
function keyApp(key) {
  var raw = String(key || "")
  var slash = raw.indexOf("/")
  return slash < 0 ? raw : raw.slice(0, slash)
}

/** The detail half of a store key, empty when there is none. */
function keyDetail(key) {
  var raw = String(key || "")
  var slash = raw.indexOf("/")
  return slash < 0 ? "" : raw.slice(slash + 1)
}

/**
 * Normalise a whole store key. Distinct from normalizeAppId, which strips
 * paths by keeping the last slash-separated component — doing that to
 * "ghostty/opencode" would throw the app away and keep only the detail.
 */
function normalizeKey(value) {
  var raw = String(value === undefined || value === null ? "" : value)
  raw = raw.replace(/[\r\n\t\x00]+/g, " ").replace(/^\s+|\s+$/g, "")
  if (!raw.length) return ""
  var slash = raw.indexOf("/")
  if (slash < 0) return normalizeAppId(raw)
  var app = normalizeAppId(raw.slice(0, slash))
  // A leading slash means this was a path, not a composite key.
  if (!app.length) return normalizeAppId(raw)
  return composeKey(app, raw.slice(slash + 1))
}

function cloneState(state) {
  var source = state || emptyState()
  var buffer = {}
  for (var day in source.buffer) {
    var apps = {}
    for (var app in source.buffer[day]) apps[app] = source.buffer[day][app]
    buffer[day] = apps
  }
  return {
    buffer: buffer,
    lastAt: source.lastAt || 0,
    lastAppId: source.lastAppId || "",
    lastActive: source.lastActive === true,
    pendingSeconds: source.pendingSeconds || 0,
    droppedGaps: source.droppedGaps || 0
  }
}

function addSeconds(state, dayKey, appId, seconds) {
  if (!dayKey || !appId || !(seconds > 0)) return state
  if (!state.buffer[dayKey]) state.buffer[dayKey] = {}
  var day = state.buffer[dayKey]
  day[appId] = (day[appId] || 0) + seconds
  state.pendingSeconds += seconds
  return state
}

// Split an elapsed window across midnight so each day gets its real share.
function creditWindow(state, appId, fromMillis, toMillis) {
  var cursor = fromMillis
  var guard = 0
  while (cursor < toMillis && guard < 400) {
    guard++
    var dayKey = dayKeyOf(cursor)
    var midnight = new Date(cursor)
    midnight.setHours(24, 0, 0, 0)
    var boundary = Math.min(toMillis, midnight.getTime())
    var seconds = Math.round((boundary - cursor) / 1000)
    if (seconds > 0) addSeconds(state, dayKey, appId, seconds)
    cursor = boundary
  }
  return state
}

/**
 * Fold one observation into the state.
 *
 * opts.now         ms timestamp of this observation
 * opts.appId       app currently focused ("" when nothing is)
 * opts.active      whether time should count right now (focused && !idle)
 * opts.intervalMs  the sampling period
 * opts.maxStepMs   anything longer than this is a gap (default 4x interval)
 */
function observe(state, opts) {
  var next = cloneState(state)
  var now = Number(opts && opts.now)
  if (!isFinite(now) || now <= 0) return next

  var intervalMs = Math.max(1000, Number(opts.intervalMs) || 5000)
  var maxStepMs = Math.max(intervalMs * 2, Number(opts.maxStepMs) || intervalMs * 4)
  var appId = normalizeKey(opts.appId)
  var active = opts.active === true && appId.length > 0

  if (next.lastAt > 0) {
    var elapsed = now - next.lastAt
    if (elapsed < 0) {
      // Clock stepped backwards (NTP, timezone change). Drop the window.
      next.droppedGaps++
    } else if (elapsed > maxStepMs) {
      next.droppedGaps++
    } else if (next.lastActive && next.lastAppId.length > 0 && elapsed > 0) {
      creditWindow(next, next.lastAppId, next.lastAt, now)
    }
  }

  next.lastAt = now
  next.lastAppId = appId
  next.lastActive = active
  return next
}

// Called when focus or idle state changes between ticks so the window that
// just ended is attributed before the new state takes over.
function mark(state, opts) {
  return observe(state, opts)
}

// Reset the clock without crediting anything, e.g. on resume from suspend.
function rebase(state, millis) {
  var next = cloneState(state)
  next.lastAt = Number(millis) || 0
  next.lastActive = false
  return next
}

function isEmpty(state) {
  var source = state || emptyState()
  for (var day in source.buffer) {
    for (var app in source.buffer[day]) {
      if (source.buffer[day][app] > 0) return false
    }
  }
  return true
}

// Payload for `screentime commit`.
function commitPayload(state) {
  var source = state || emptyState()
  var days = {}
  for (var day in source.buffer) {
    var apps = {}
    var any = false
    for (var app in source.buffer[day]) {
      var seconds = Math.round(source.buffer[day][app])
      if (seconds > 0) {
        apps[app] = seconds
        any = true
      }
    }
    if (any) days[day] = apps
  }
  return { days: days }
}

// Drop the buffer after a successful commit, keeping the clock intact.
function drained(state) {
  var next = cloneState(state)
  next.buffer = {}
  next.pendingSeconds = 0
  return next
}

// Live view of today's total: what the store last reported plus what is
// still sitting in the buffer, so the bar ticks up between commits.
function liveTotal(committedSeconds, state, dayKey) {
  var base = Math.max(0, Number(committedSeconds) || 0)
  var source = state || emptyState()
  var day = source.buffer[dayKey]
  if (!day) return base
  var extra = 0
  for (var app in day) extra += day[app] || 0
  return base + Math.max(0, Math.round(extra))
}
