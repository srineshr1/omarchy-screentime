.pragma library

// Pure helpers shared by the service, the bar widget, the panel, and the grid.
// No QML types in here, so every function is testable under plain node.

var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
var WEEKDAYS_MON = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
var WEEKDAYS_SUN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
var LEVELS = ["NONE", "L1", "L2", "L3", "L4", "OVER"]

function emptySnapshot() {
  return {
    ok: false,
    error: "",
    today: "",
    todayTotal: 0,
    todayLabel: "0m",
    todayLevel: "NONE",
    todayApps: [],
    todayByApp: [],
    todayTree: [],
    yesterdayTotal: 0,
    yesterdayLabel: "0m",
    goalHours: 6,
    goalSeconds: 21600,
    overGoal: false,
    weeks: [],
    weekStartsMonday: true,
    months: 6,
    offset: 0,
    rangeStart: "",
    rangeEnd: "",
    rangeLabel: "",
    rangeTotal: 0,
    rangeTotalLabel: "0m",
    rangeDaysTracked: 0,
    rangeDailyAverage: 0,
    canGoBack: false,
    canGoForward: false,
    year: new Date().getFullYear(),
    availableYears: [],
    weekDays: [],
    weekTotal: 0,
    weekLabel: "0m",
    weekDailyAverage: 0,
    streak: 0,
    bestDay: { date: "", seconds: 0, label: "0m" },
    topApps: [],
    allTimeTotal: 0,
    allTimeLabel: "0m",
    daysTracked: 0,
    selectedDay: "",
    selectedTotal: 0,
    selectedLabel: "0m",
    selectedApps: [],
    selectedByApp: [],
    selectedTree: [],
    selectedDetailExpired: false,
    todayNet: emptyNet(),
    todayNetApps: [],
    selectedNet: emptyNet(),
    selectedNetApps: [],
    weekNet: emptyNet(),
    rangeNet: emptyNet(),
    allTimeNet: emptyNet(),
    netTracked: false,
    storePath: ""
  }
}

function parseSnapshot(text) {
  var raw = String(text === undefined || text === null ? "" : text)
  raw = raw.replace(/^\s+|\s+$/g, "")
  if (!raw.length) return emptySnapshot()
  var parsed = null
  try {
    parsed = JSON.parse(raw)
  } catch (e) {
    var fallback = emptySnapshot()
    fallback.error = "Could not read the screen time store"
    return fallback
  }
  if (!parsed || typeof parsed !== "object") return emptySnapshot()

  // Start from the defaults so a missing field is never undefined, then copy
  // *everything* the helper sent. Iterating the defaults instead would silently
  // drop any field added to the engine but not mirrored here — which is exactly
  // how todayTree went missing once.
  var base = emptySnapshot()
  var out = {}
  for (var key in base) out[key] = base[key]
  for (var incoming in parsed) {
    if (parsed[incoming] !== undefined && parsed[incoming] !== null)
      out[incoming] = parsed[incoming]
  }
  out.ok = parsed.ok === true
  out.error = safeText(parsed.error, 240)
  return out
}

// Anything that reaches a bar tooltip is rendered by a shared Text that
// leaves textFormat at AutoText, so strings that came from outside the
// plugin get their markup stripped before they go anywhere near it.
function safeText(value, limit) {
  var raw = String(value === undefined || value === null ? "" : value)
  raw = raw.replace(/[\r\n\t]+/g, " ").replace(/[<>&]/g, "")
  raw = raw.replace(/^\s+|\s+$/g, "")
  var max = limit || 160
  return raw.length > max ? raw.slice(0, max - 1) + "\u2026" : raw
}

function clampInt(value, fallback, min, max) {
  var number = parseInt(value, 10)
  if (!isFinite(number)) number = fallback
  return Math.max(min, Math.min(max, number))
}

function intSetting(value, fallback, min, max) {
  return clampInt(value, fallback, min, max)
}

// file:///path/to/plugin/x -> /path/to/plugin/x
function pluginFilePath(url) {
  var raw = String(url || "")
  if (raw.indexOf("file://") === 0) raw = raw.slice(7)
  return raw.replace(/\/+$/, "")
}

/** 4h 30m, 45m, 0m. Compact by design: this lands in a bar slot. */
function formatDuration(seconds) {
  var total = Math.max(0, Math.round(Number(seconds) || 0))
  var hours = Math.floor(total / 3600)
  var minutes = Math.floor((total % 3600) / 60)
  if (hours > 0 && minutes > 0) return hours + "h " + minutes + "m"
  if (hours > 0) return hours + "h"
  if (minutes > 0) return minutes + "m"
  return "0m"
}

/** 4:30 for the tightest bar layouts. */
function formatClock(seconds) {
  var total = Math.max(0, Math.round(Number(seconds) || 0))
  var hours = Math.floor(total / 3600)
  var minutes = Math.floor((total % 3600) / 60)
  return hours + ":" + (minutes < 10 ? "0" : "") + minutes
}

var BYTE_UNITS = ["kB", "MB", "GB", "TB"]

/**
 * Decimal units, the way every ISP and speed test counts them, and the same
 * rounding the engine uses so a row and its tooltip never disagree.
 */
function formatBytes(bytes) {
  var total = Math.max(0, Math.round(Number(bytes) || 0))
  if (total < 1000) return total + " B"
  var value = total
  for (var i = 0; i < BYTE_UNITS.length; i++) {
    value /= 1000
    if (value < 1000 || i === BYTE_UNITS.length - 1) {
      if (value < 10) return (Math.round(value * 10) / 10).toFixed(1) + " " + BYTE_UNITS[i]
      return Math.round(value) + " " + BYTE_UNITS[i]
    }
  }
  return total + " B"
}

/** "1.2G", "45M". For the breakdown rows, where the column has to be narrow. */
function formatBytesShort(bytes) {
  var full = formatBytes(bytes)
  return full.replace(/ B$/, "B").replace(/ kB$/, "K").replace(/ MB$/, "M")
             .replace(/ GB$/, "G").replace(/ TB$/, "T")
}

/**
 * "D 1.2G  U 45M" — down first, because that is the number people are looking
 * for. Empty when nothing was measured, so a row with no traffic stays quiet
 * rather than claiming a confident zero.
 */
function netLabel(net, short) {
  if (!net) return ""
  var down = Math.max(0, Number(net.down) || 0)
  var up = Math.max(0, Number(net.up) || 0)
  if (down <= 0 && up <= 0) return ""
  var format = short === false ? formatBytes : formatBytesShort
  var join = short === false ? " \u00b7 " : "  "
  return "D " + format(down) + join + "U " + format(up)
}

function emptyNet() {
  return {
    down: 0,
    up: 0,
    total: 0,
    downLabel: "0 B",
    upLabel: "0 B",
    totalLabel: "0 B",
    label: "D 0 B \u00b7 U 0 B"
  }
}

function formatLongDate(dayKey) {
  var parts = String(dayKey || "").split("-")
  if (parts.length !== 3) return ""
  var year = parseInt(parts[0], 10)
  var month = parseInt(parts[1], 10)
  var day = parseInt(parts[2], 10)
  if (!isFinite(year) || !isFinite(month) || !isFinite(day)) return ""
  var name = MONTHS[Math.max(0, Math.min(11, month - 1))]
  return day + " " + name + " " + year
}

function weekdayNames(mondayFirst) {
  return mondayFirst === false ? WEEKDAYS_SUN : WEEKDAYS_MON
}

/** Row captions down the left of the grid, sparse like GitHub's. */
function weekdayCaption(row, mondayFirst) {
  var names = weekdayNames(mondayFirst)
  if (mondayFirst === false) return row === 1 || row === 3 || row === 5 ? names[row] : ""
  return row === 0 || row === 2 || row === 4 ? names[row] : ""
}

/**
 * Month captions above the grid: one per month, placed at the first column
 * whose week actually starts in that month, and never so close to the
 * previous caption that the two would collide.
 */
function monthLabels(weeks, minGap) {
  var list = weeks || []
  var gap = minGap === undefined ? 3 : minGap
  var out = []
  var lastMonth = -1
  var lastIndex = -99
  for (var i = 0; i < list.length; i++) {
    var days = list[i] ? list[i].days || [] : []
    var found = null
    for (var d = 0; d < days.length; d++) {
      if (days[d] && days[d].date && !days[d].outOfRange) {
        found = days[d]
        break
      }
    }
    if (!found) continue
    var month = parseInt(String(found.date).split("-")[1], 10)
    if (!isFinite(month) || month === lastMonth) continue
    if (i - lastIndex < gap) {
      lastMonth = month
      continue
    }
    out.push({ index: i, month: month, label: MONTHS[Math.max(0, Math.min(11, month - 1))] })
    lastMonth = month
    lastIndex = i
  }
  return out
}

function levelIndex(level) {
  var idx = LEVELS.indexOf(String(level || "NONE"))
  return idx < 0 ? 0 : idx
}

function dayTooltip(day) {
  if (!day || !day.date || day.outOfRange === true) return ""
  var label = day.label || formatDuration(day.seconds)
  var when = formatLongDate(day.date)
  var data = netLabel(day.net, false)
  var head = day.seconds ? label + " \u00b7 " + when : "No screen time \u00b7 " + when
  return data ? head + "\n" + data : head
}

/** "+12% vs yesterday" / "-8% vs yesterday" / "" when there is no baseline. */
function deltaLabel(todaySeconds, yesterdaySeconds) {
  var today = Math.max(0, Number(todaySeconds) || 0)
  var previous = Math.max(0, Number(yesterdaySeconds) || 0)
  if (previous <= 0) return ""
  var percent = Math.round((today - previous) / previous * 100)
  if (percent === 0) return "same as yesterday"
  return (percent > 0 ? "+" : "") + percent + "% vs yesterday"
}

/** What the bar shows next to the glyph. */
function barLabel(seconds, mode) {
  if (mode === "icon") return ""
  if (mode === "clock") return formatClock(seconds)
  return formatDuration(seconds)
}

/** One-line summary for the bar tooltip. Plugin-owned phrases only. */
function barTooltip(snapshot, liveSeconds) {
  var snap = snapshot || emptySnapshot()
  var bits = ["Screen time today: " + formatDuration(liveSeconds)]
  var goal = Number(snap.goalSeconds) || 0
  if (goal > 0) {
    var remaining = goal - liveSeconds
    bits.push(remaining > 0
      ? formatDuration(remaining) + " left of your " + snap.goalHours + "h limit"
      : formatDuration(-remaining) + " over your " + snap.goalHours + "h limit")
  }
  var delta = deltaLabel(liveSeconds, snap.yesterdayTotal)
  if (delta) bits.push(delta)
  var data = netLabel(snap.todayNet, false)
  if (data) bits.push(data)
  if (snap.todayApps && snap.todayApps.length > 0)
    bits.push("Most used: " + safeText(snap.todayApps[0].name, 40))
  return bits.join("\n")
}

/** Progress toward the daily limit, capped so the ring never overflows. */
function goalFraction(seconds, goalSeconds) {
  var goal = Math.max(1, Number(goalSeconds) || 1)
  return Math.max(0, Math.min(1, (Number(seconds) || 0) / goal))
}

/** Grid rows are fixed at 7; this is how wide the whole thing wants to be. */
function gridWidth(weekCount, cellSize, cellGap, gutter) {
  var count = Math.max(0, Number(weekCount) || 0)
  return (gutter || 0) + count * cellSize + Math.max(0, count - 1) * cellGap
}

/**
 * Largest square cell that lets `count` columns fit inside `available` px.
 *
 * The heatmap is the widest thing in the panel, and a fixed cell size either
 * clips the newest column or leaves the grid floating in whitespace. Solving
 * for the cell instead means the grid always ends exactly at the right edge.
 */
function fitCellSize(available, count, cellGap, gutter, minSize, maxSize) {
  var columns = Math.max(1, Math.round(Number(count) || 1))
  var gap = Math.max(0, Number(cellGap) || 0)
  var low = Math.max(1, Number(minSize) || 1)
  var high = Math.max(low, Number(maxSize) || low)
  var usable = (Number(available) || 0) - (Number(gutter) || 0) - (columns - 1) * gap
  if (!(usable > 0)) return low
  return Math.max(low, Math.min(high, Math.floor(usable / columns)))
}

function lastDays(days, count) {
  var list = days || []
  if (list.length <= count) return list
  return list.slice(list.length - count)
}

/** Flatten the week columns back into a date-ordered list of in-range days. */
function flattenDays(weeks) {
  var out = []
  var list = weeks || []
  for (var i = 0; i < list.length; i++) {
    var days = list[i] ? list[i].days || [] : []
    for (var d = 0; d < days.length; d++) {
      if (days[d] && days[d].date && days[d].outOfRange !== true) out.push(days[d])
    }
  }
  return out
}

/** Share of a bar-chart row, scaled against the busiest day on screen. */
function relativeShare(seconds, peakSeconds) {
  var peak = Math.max(1, Number(peakSeconds) || 1)
  return Math.max(0, Math.min(1, (Number(seconds) || 0) / peak))
}

function peakSeconds(days) {
  var list = days || []
  var peak = 0
  for (var i = 0; i < list.length; i++) {
    var value = Number(list[i] ? list[i].seconds : 0) || 0
    if (value > peak) peak = value
  }
  return peak
}

/** Short weekday initial under the week bars. */
function weekdayInitial(dayKey) {
  var parts = String(dayKey || "").split("-")
  if (parts.length !== 3) return ""
  var when = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10))
  if (isNaN(when.getTime())) return ""
  return WEEKDAYS_SUN[when.getDay()].slice(0, 1)
}

function isToday(dayKey, nowMillis) {
  if (!dayKey) return false
  var when = new Date(nowMillis === undefined ? Date.now() : nowMillis)
  var month = when.getMonth() + 1
  var day = when.getDate()
  var key = when.getFullYear() + "-" + (month < 10 ? "0" : "") + month + "-" + (day < 10 ? "0" : "") + day
  return key === dayKey
}
