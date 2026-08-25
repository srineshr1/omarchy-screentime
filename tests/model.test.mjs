import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"
import { loadQmlJs } from "./harness.mjs"

const Model = loadQmlJs("lib/Model.js")

test("formatDuration is compact and never negative", () => {
  assert.equal(Model.formatDuration(0), "0m")
  assert.equal(Model.formatDuration(59), "0m")
  assert.equal(Model.formatDuration(60), "1m")
  assert.equal(Model.formatDuration(3600), "1h")
  assert.equal(Model.formatDuration(3660), "1h 1m")
  assert.equal(Model.formatDuration(16200), "4h 30m")
  assert.equal(Model.formatDuration(-500), "0m")
  assert.equal(Model.formatDuration("16200"), "4h 30m")
  assert.equal(Model.formatDuration(null), "0m")
})

test("formatClock pads minutes", () => {
  assert.equal(Model.formatClock(16200), "4:30")
  assert.equal(Model.formatClock(3660), "1:01")
  assert.equal(Model.formatClock(0), "0:00")
})

test("formatLongDate is human and tolerant of junk", () => {
  assert.equal(Model.formatLongDate("2026-08-24"), "24 Aug 2026")
  assert.equal(Model.formatLongDate("2026-01-01"), "1 Jan 2026")
  assert.equal(Model.formatLongDate(""), "")
  assert.equal(Model.formatLongDate("nonsense"), "")
})

test("parseSnapshot fills defaults and flags failure", () => {
  const empty = Model.parseSnapshot("")
  assert.equal(empty.ok, false)
  assert.deepEqual(empty.weeks, [])

  const broken = Model.parseSnapshot("{not json")
  assert.equal(broken.ok, false)
  assert.match(broken.error, /Could not read/)

  const good = Model.parseSnapshot(JSON.stringify({
    ok: true,
    todayTotal: 16200,
    todayLabel: "4h 30m",
    weeks: [{ index: 0, days: [] }]
  }))
  assert.equal(good.ok, true)
  assert.equal(good.todayTotal, 16200)
  assert.equal(good.weeks.length, 1)
  // Untouched keys keep their defaults rather than becoming undefined.
  assert.equal(good.goalHours, 6)
  assert.equal(good.streak, 0)
})

test("parseSnapshot sanitises the error it carries", () => {
  const parsed = Model.parseSnapshot(JSON.stringify({
    ok: false,
    error: "<img src=x onerror=alert(1)>\nbad"
  }))
  assert.equal(parsed.error.indexOf("<"), -1)
  assert.equal(parsed.error.indexOf(">"), -1)
  assert.equal(parsed.error.indexOf("\n"), -1)
})

test("safeText strips markup, collapses newlines, and truncates", () => {
  assert.equal(Model.safeText("<b>hi</b>"), "bhi/b")
  assert.equal(Model.safeText("a\nb\tc"), "a b c")
  assert.equal(Model.safeText("", 10), "")
  assert.equal(Model.safeText(null), "")
  const long = Model.safeText("x".repeat(50), 10)
  assert.equal(long.length, 10)
  assert.ok(long.endsWith("\u2026"))
})

test("intSetting clamps and falls back", () => {
  assert.equal(Model.intSetting(8, 6, 1, 24), 8)
  assert.equal(Model.intSetting("8", 6, 1, 24), 8)
  assert.equal(Model.intSetting(99, 6, 1, 24), 24)
  assert.equal(Model.intSetting(0, 6, 1, 24), 1)
  assert.equal(Model.intSetting("nope", 6, 1, 24), 6)
  assert.equal(Model.intSetting(undefined, 6, 1, 24), 6)
})

test("pluginFilePath unwraps file URLs", () => {
  assert.equal(Model.pluginFilePath("file:///home/x/plugin/"), "/home/x/plugin")
  assert.equal(Model.pluginFilePath("file:///home/x/plugin/bin/screentime"),
    "/home/x/plugin/bin/screentime")
  assert.equal(Model.pluginFilePath(""), "")
})

test("deltaLabel compares against yesterday", () => {
  assert.equal(Model.deltaLabel(120, 100), "+20% vs yesterday")
  assert.equal(Model.deltaLabel(80, 100), "-20% vs yesterday")
  assert.equal(Model.deltaLabel(100, 100), "same as yesterday")
  assert.equal(Model.deltaLabel(100, 0), "")
})

test("goalFraction saturates at one", () => {
  assert.equal(Model.goalFraction(0, 21600), 0)
  assert.equal(Model.goalFraction(10800, 21600), 0.5)
  assert.equal(Model.goalFraction(43200, 21600), 1)
  assert.equal(Model.goalFraction(100, 0), 1)
})

test("gridWidth accounts for gaps and the weekday gutter", () => {
  assert.equal(Model.gridWidth(0, 9, 2, 18), 18)
  assert.equal(Model.gridWidth(1, 9, 2, 18), 27)
  assert.equal(Model.gridWidth(53, 9, 2, 18), 18 + 53 * 9 + 52 * 2)
})

test("fitCellSize solves for a cell that fits the given width", () => {
  // 26 columns, 2px gaps, 30px gutter, into 460px.
  const size = Model.fitCellSize(460, 26, 2, 30, 7, 12)
  assert.ok(size >= 7 && size <= 12)
  // The whole grid must fit inside the width it was given.
  assert.ok(Model.gridWidth(26, size, 2, 30) <= 460)
})

test("fitCellSize never exceeds the requested maximum", () => {
  // Only 4 columns: plenty of room, but cells stay at the design size.
  assert.equal(Model.fitCellSize(460, 4, 2, 30, 7, 12), 12)
})

test("fitCellSize never goes below the minimum, even when starved", () => {
  assert.equal(Model.fitCellSize(50, 53, 2, 30, 7, 12), 7)
  assert.equal(Model.fitCellSize(0, 26, 2, 30, 7, 12), 7)
  assert.equal(Model.fitCellSize(-100, 26, 2, 30, 7, 12), 7)
})

test("fitCellSize tolerates junk and a single column", () => {
  assert.equal(Model.fitCellSize(460, 0, 2, 30, 7, 12), 12)
  assert.equal(Model.fitCellSize(460, null, 2, 30, 7, 12), 12)
  assert.equal(Model.fitCellSize("460", 1, 2, 30, 7, 12), 12)
})

test("a fitted year grid still fits, just with smaller cells", () => {
  const year = Model.fitCellSize(460, 53, 2, 30, 5, 12)
  const half = Model.fitCellSize(460, 26, 2, 30, 5, 12)
  assert.ok(year < half, "53 columns must yield smaller cells than 26")
  assert.ok(Model.gridWidth(53, year, 2, 30) <= 460)
})

test("weekdayCaption is sparse and honours the week start", () => {
  assert.equal(Model.weekdayCaption(0, true), "Mon")
  assert.equal(Model.weekdayCaption(1, true), "")
  assert.equal(Model.weekdayCaption(2, true), "Wed")
  assert.equal(Model.weekdayCaption(4, true), "Fri")
  assert.equal(Model.weekdayCaption(1, false), "Mon")
  assert.equal(Model.weekdayCaption(0, false), "")
})

function weekOf(index, firstDate) {
  return {
    index,
    days: [{ date: firstDate, outOfRange: false, seconds: 0, level: "NONE" }]
  }
}

test("monthLabels emits one caption per month, spaced apart", () => {
  const weeks = [
    weekOf(0, "2026-01-01"),
    weekOf(1, "2026-01-08"),
    weekOf(2, "2026-01-15"),
    weekOf(3, "2026-01-22"),
    weekOf(4, "2026-02-01"),
    weekOf(5, "2026-02-08")
  ]
  const labels = Model.monthLabels(weeks)
  assert.deepEqual(labels.map((l) => l.label), ["Jan", "Feb"])
  assert.deepEqual(labels.map((l) => l.index), [0, 4])
})

test("monthLabels skips a caption that would collide with the previous one", () => {
  const weeks = [weekOf(0, "2026-01-29"), weekOf(1, "2026-02-05")]
  // Feb starts only one column later, closer than the minimum gap.
  assert.deepEqual(Model.monthLabels(weeks).map((l) => l.label), ["Jan"])
  // With no minimum gap both fit.
  assert.deepEqual(Model.monthLabels(weeks, 0).map((l) => l.label), ["Jan", "Feb"])
})

test("monthLabels ignores padding cells and empty input", () => {
  const padded = [{
    index: 0,
    days: [
      { date: "", outOfRange: true },
      { date: "2026-01-01", outOfRange: false }
    ]
  }]
  assert.deepEqual(Model.monthLabels(padded).map((l) => l.label), ["Jan"])
  assert.deepEqual(Model.monthLabels([]), [])
  assert.deepEqual(Model.monthLabels(null), [])
})

test("dayTooltip describes a day, and says nothing for padding", () => {
  assert.equal(
    Model.dayTooltip({ date: "2026-08-24", seconds: 16200, label: "4h 30m" }),
    "4h 30m \u00b7 24 Aug 2026"
  )
  assert.equal(
    Model.dayTooltip({ date: "2026-08-24", seconds: 0, label: "0m" }),
    "No screen time \u00b7 24 Aug 2026"
  )
  assert.equal(Model.dayTooltip({ date: "", outOfRange: true }), "")
  assert.equal(Model.dayTooltip(null), "")
})

test("levelIndex orders the ramp and defaults safely", () => {
  assert.equal(Model.levelIndex("NONE"), 0)
  assert.equal(Model.levelIndex("L1"), 1)
  assert.equal(Model.levelIndex("L4"), 4)
  assert.equal(Model.levelIndex("OVER"), 5)
  assert.equal(Model.levelIndex("garbage"), 0)
  assert.equal(Model.levelIndex(undefined), 0)
})

test("flattenDays returns only real days, in order", () => {
  const weeks = [
    { index: 0, days: [{ date: "", outOfRange: true }, { date: "2026-01-01", outOfRange: false }] },
    { index: 1, days: [{ date: "2026-01-08", outOfRange: false }] }
  ]
  assert.deepEqual(Model.flattenDays(weeks).map((d) => d.date), ["2026-01-01", "2026-01-08"])
  assert.deepEqual(Model.flattenDays(null), [])
})

test("peakSeconds and relativeShare scale the week bars", () => {
  const days = [{ seconds: 100 }, { seconds: 400 }, { seconds: 0 }]
  assert.equal(Model.peakSeconds(days), 400)
  assert.equal(Model.peakSeconds([]), 0)
  assert.equal(Model.relativeShare(100, 400), 0.25)
  assert.equal(Model.relativeShare(800, 400), 1)
  assert.equal(Model.relativeShare(0, 0), 0)
})

test("lastDays keeps the tail", () => {
  const days = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
  assert.deepEqual(Model.lastDays(days, 3), [8, 9, 10])
  assert.deepEqual(Model.lastDays([1, 2], 7), [1, 2])
})

test("barLabel respects the display mode", () => {
  assert.equal(Model.barLabel(16200, "icon"), "")
  assert.equal(Model.barLabel(16200, "clock"), "4:30")
  assert.equal(Model.barLabel(16200, "strip"), "4h 30m")
  assert.equal(Model.barLabel(16200, undefined), "4h 30m")
})

test("barTooltip reports remaining time under the limit and excess over it", () => {
  const snapshot = Model.parseSnapshot(JSON.stringify({
    ok: true,
    goalHours: 6,
    goalSeconds: 21600,
    yesterdayTotal: 14400,
    todayApps: [{ name: "Firefox", seconds: 3600, label: "1h" }]
  }))
  const under = Model.barTooltip(snapshot, 10800)
  assert.match(under, /Screen time today: 3h/)
  assert.match(under, /3h left of your 6h limit/)
  assert.match(under, /Most used: Firefox/)

  const over = Model.barTooltip(snapshot, 25200)
  assert.match(over, /1h over your 6h limit/)
})

test("weekdayInitial maps a date to one letter", () => {
  assert.equal(Model.weekdayInitial("2026-08-24"), "M")
  assert.equal(Model.weekdayInitial("2026-08-23"), "S")
  assert.equal(Model.weekdayInitial(""), "")
})

test("isToday compares against a supplied clock", () => {
  const now = new Date(2026, 7, 24, 15, 0, 0, 0).getTime()
  assert.equal(Model.isToday("2026-08-24", now), true)
  assert.equal(Model.isToday("2026-08-23", now), false)
  assert.equal(Model.isToday("", now), false)
})

test("parseSnapshot keeps every field the engine sends", () => {
  // Regression: parseSnapshot used to iterate emptySnapshot()'s keys, so any
  // field added to bin/screentime but not mirrored in Model.js was silently
  // dropped. todayTree went missing that way and the breakdown rendered
  // "Nothing recorded yet today" next to a non-zero total.
  const engineKeys = JSON.parse(
    readFileSync(new URL("./fixtures/snapshot-keys.json", import.meta.url), "utf8"))
  const payload = { ok: true }
  for (const key of engineKeys) {
    if (key !== "ok") payload[key] = "sentinel:" + key
  }
  const parsed = Model.parseSnapshot(JSON.stringify(payload))
  for (const key of engineKeys) {
    assert.ok(key in parsed, `parseSnapshot dropped ${key}`)
    if (key !== "ok" && key !== "error") {
      assert.equal(parsed[key], "sentinel:" + key, `parseSnapshot mangled ${key}`)
    }
  }
})

test("emptySnapshot declares a default for every field the engine sends", () => {
  // Not strictly required now that parseSnapshot passes unknown keys through,
  // but a missing default means the panel reads undefined before the first
  // snapshot arrives.
  const engineKeys = JSON.parse(
    readFileSync(new URL("./fixtures/snapshot-keys.json", import.meta.url), "utf8"))
  const empty = Model.emptySnapshot()
  const missing = engineKeys.filter((key) => !(key in empty) && key !== "generatedAt")
  assert.deepEqual(missing, [], `emptySnapshot is missing: ${missing.join(", ")}`)
})

test("the tree fields survive parsing and default to empty arrays", () => {
  const empty = Model.emptySnapshot()
  assert.deepEqual(empty.todayTree, [])
  assert.deepEqual(empty.selectedTree, [])

  const parsed = Model.parseSnapshot(JSON.stringify({
    ok: true,
    todayTree: [{
      id: "com.mitchellh.ghostty",
      name: "Ghostty",
      label: "30m",
      share: 0.5,
      children: [{ id: "com.mitchellh.ghostty/opencode", name: "opencode", label: "20m" }]
    }]
  }))
  assert.equal(parsed.todayTree.length, 1)
  assert.equal(parsed.todayTree[0].children[0].name, "opencode")
})

test("emptySnapshot is a complete, safe shape", () => {
  const empty = Model.emptySnapshot()
  assert.equal(empty.ok, false)
  assert.deepEqual(empty.weeks, [])
  assert.deepEqual(empty.todayApps, [])
  assert.equal(empty.todayLabel, "0m")
  assert.equal(typeof empty.goalSeconds, "number")
  assert.equal(typeof empty.bestDay.label, "string")
  // Range fields must exist so the panel's bindings never read undefined.
  assert.equal(empty.months, 6)
  assert.equal(empty.offset, 0)
  assert.equal(empty.rangeLabel, "")
  assert.equal(empty.rangeTotalLabel, "0m")
  assert.equal(empty.canGoBack, false)
  assert.equal(empty.canGoForward, false)
})

test("parseSnapshot carries the range fields through", () => {
  const parsed = Model.parseSnapshot(JSON.stringify({
    ok: true,
    months: 4,
    offset: 2,
    rangeLabel: "May \u2013 Aug 2026",
    rangeTotal: 7200,
    rangeTotalLabel: "2h",
    rangeDailyAverage: 3600,
    canGoBack: true,
    canGoForward: true
  }))
  assert.equal(parsed.months, 4)
  assert.equal(parsed.offset, 2)
  assert.equal(parsed.rangeLabel, "May \u2013 Aug 2026")
  assert.equal(parsed.rangeTotalLabel, "2h")
  assert.equal(parsed.canGoBack, true)
})

test("formatBytes uses decimal units, matching the engine", () => {
  assert.equal(Model.formatBytes(0), "0 B")
  assert.equal(Model.formatBytes(999), "999 B")
  assert.equal(Model.formatBytes(1000), "1.0 kB")
  assert.equal(Model.formatBytes(1500), "1.5 kB")
  assert.equal(Model.formatBytes(12000), "12 kB")
  assert.equal(Model.formatBytes(9400000), "9.4 MB")
  assert.equal(Model.formatBytes(1234567890), "1.2 GB")
  assert.equal(Model.formatBytes(5e12), "5.0 TB")
})

test("formatBytes treats junk as nothing rather than NaN", () => {
  assert.equal(Model.formatBytes(-5), "0 B")
  assert.equal(Model.formatBytes(null), "0 B")
  assert.equal(Model.formatBytes(undefined), "0 B")
  assert.equal(Model.formatBytes("nonsense"), "0 B")
})

test("formatBytesShort drops the space so the column stays narrow", () => {
  assert.equal(Model.formatBytesShort(1234567890), "1.2G")
  assert.equal(Model.formatBytesShort(9400000), "9.4M")
  assert.equal(Model.formatBytesShort(12000), "12K")
  assert.equal(Model.formatBytesShort(500), "500B")
})

test("netLabel puts down first and stays quiet when nothing was measured", () => {
  assert.equal(Model.netLabel({ down: 1234567890, up: 45000000 }, true),
    "D 1.2G  U 45M")
  assert.equal(Model.netLabel({ down: 1234567890, up: 45000000 }, false),
    "D 1.2 GB \u00b7 U 45 MB")
  // A detail row inside a folder has no traffic of its own, and a confident
  // "D 0 B" there would be a lie.
  assert.equal(Model.netLabel({ down: 0, up: 0 }, true), "")
  assert.equal(Model.netLabel(null, true), "")
  assert.equal(Model.netLabel(undefined, false), "")
})

test("netLabel reports an app that only uploaded", () => {
  assert.equal(Model.netLabel({ down: 0, up: 2000 }, true), "D 0B  U 2.0K")
})

test("emptyNet is a safe zero", () => {
  const net = Model.emptyNet()
  assert.equal(net.down, 0)
  assert.equal(net.downLabel, "0 B")
  assert.equal(typeof net.label, "string")
})

test("emptySnapshot declares the net fields", () => {
  const empty = Model.emptySnapshot()
  assert.equal(empty.netTracked, false)
  assert.equal(empty.todayNet.down, 0)
  assert.equal(empty.selectedNet.up, 0)
  assert.deepEqual(empty.todayNetApps, [])
  assert.equal(empty.allTimeNet.downLabel, "0 B")
})

test("dayTooltip adds the day's data usage on a second line", () => {
  assert.equal(
    Model.dayTooltip({
      date: "2026-08-24", seconds: 16200, label: "4h 30m",
      net: { down: 2000000, up: 250000 }
    }),
    "4h 30m \u00b7 24 Aug 2026\nD 2.0 MB \u00b7 U 250 kB")
  // A day with no traffic keeps the single-line tooltip it always had.
  assert.equal(
    Model.dayTooltip({ date: "2026-08-24", seconds: 16200, label: "4h 30m" }),
    "4h 30m \u00b7 24 Aug 2026")
})

test("barTooltip mentions today's data when there is some", () => {
  const snapshot = Model.emptySnapshot()
  snapshot.goalSeconds = 21600
  snapshot.goalHours = 6
  snapshot.todayNet = { down: 3000000, up: 400000 }
  assert.match(Model.barTooltip(snapshot, 3600), /D 3\.0 MB/)

  const quiet = Model.emptySnapshot()
  quiet.goalSeconds = 21600
  quiet.goalHours = 6
  assert.doesNotMatch(Model.barTooltip(quiet, 3600), /D 0 B/)
})
