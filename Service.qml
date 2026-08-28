import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "lib/Model.js" as Model
import "lib/Stream.js" as Stream
import "lib/Tracker.js" as Tracker

// The screen time tracker. Loaded as a `service`, so it runs for the whole
// session whether or not the bar widget is on screen.
//
// How time is counted:
//   * The compositor's focused toplevel names the app (ToplevelManager).
//   * IdleMonitor stops the clock once there has been no input for
//     `idleTimeoutSec`, so walking away does not bill you.
//   * Nothing accrues when no window has focus — a bare desktop is not usage.
//   * A tick arriving much later than its interval is treated as a suspend or
//     a stall and dropped whole, rather than credited as hours of usage.
//
// Seconds accrue in memory (Tracker.js, pure) and are handed to
// `bin/screentime commit` on a 60s cadence and on every focus change of
// consequence. The helper is the only writer, and it writes atomically, so a
// crash costs at most the current batch and can never corrupt the store.
Item {
  id: root

  // Injected by omarchy-shell's service loader.
  property var shell: null

  // Bar widgets push their own settings in so the limit configured on the
  // widget drives the service's bucketing too.
  property var settings: ({})

  readonly property string pluginDir: Model.pluginFilePath(Qt.resolvedUrl("."))
  readonly property string helperPath: pluginDir + "/bin/screentime"
  readonly property string resolverPath: pluginDir + "/bin/resolve-focus"

  readonly property int goalHours: Model.intSetting(setting("dailyGoalHours", 6), 6, 1, 24)
  readonly property int idleTimeoutSec: Model.intSetting(setting("idleTimeoutSec", 120), 120, 30, 1800)
  readonly property int retentionDays: Model.intSetting(setting("detailRetentionDays", 120), 120, 7, 3650)
  readonly property int historyMonths: Model.intSetting(setting("historyMonths", 6), 6, 1, 24)
  readonly property bool mondayFirst: setting("weekStartsMonday", true) !== false

  // off = app names only, terminal = also what runs in terminals,
  // full = also which site a browser is on.
  readonly property string detailLevel: {
    var value = String(setting("detailLevel", "full"))
    if (value !== "off" && value !== "terminal" && value !== "full") return "full"
    return value
  }

  readonly property int sampleIntervalMs: 5000
  readonly property int commitIntervalMs: 60000
  // Socket counters die with their socket, so this has to be a good deal
  // shorter than the commit interval or short-lived connections go unmeasured.
  readonly property int netSampleIntervalMs: 15000

  // Per-app data usage, sampled from the TCP socket table.
  readonly property bool trackNetwork: setting("trackNetwork", true) !== false

  // Producer byte ceilings. Every helper is read through BoundedReader, which
  // enforces these *while the producer runs* and cuts it off at the limit —
  // rather than letting the whole stream into the shell and trimming what is
  // kept, which bounds the wrong thing. See BoundedReader.qml.
  //   * snapshot replies scale with the store (months x days x apps);
  //   * the resolver and netsample print one tiny JSON object each;
  //   * stderr is only ever surfaced as a short error label.
  readonly property int maxSnapshotBytes: 4 * 1024 * 1024
  readonly property int maxReplyBytes: 64 * 1024
  readonly property int maxStderrBytes: 4096

  // ---- published state the widget and panel read -------------------------

  property var snapshot: Model.emptySnapshot()
  property bool committing: false
  property string lastError: ""
  // How many months back the heatmap window is shifted. 0 ends today.
  property int windowOffset: 0
  property string selectedDay: ""

  readonly property string todayKey: Tracker.dayKeyOf(Date.now())
  readonly property int committedToday: Number(snapshot.todayTotal) || 0
  // Ticks up between commits so the bar is never a minute behind reality.
  property int liveToday: 0
  readonly property string todayLabel: Model.formatDuration(liveToday)
  readonly property bool overGoal: liveToday > (Number(snapshot.goalSeconds) || goalHours * 3600)
  readonly property var recentDays: Model.lastDays(snapshot.weekDays || [], 7)
  readonly property bool tracking: trackerState.lastActive === true
  readonly property string activeAppId: currentAppId
  // Bytes seen by the sampler but not yet folded into the store, so `status`
  // can show the collector is alive between commits.
  property int netPendingDown: 0
  property int netPendingUp: 0
  property int netSockets: 0

  readonly property string currentAppId: {
    var top = ToplevelManager.activeToplevel
    if (!top) return ""
    return Tracker.normalizeAppId(top.appId)
  }
  readonly property string currentTitle: {
    var top = ToplevelManager.activeToplevel
    if (!top) return ""
    return String(top.title || "")
  }

  // What is actually happening inside the focused window: the program running
  // in a terminal, or the site a browser is on. Resolved out of process.
  property string currentDetail: ""
  // The window the detail belongs to, so a late resolver reply cannot attach
  // "opencode" to whatever happens to be focused by the time it lands.
  property string detailForApp: ""
  property string detailForTitle: ""

  // The accounting key. Detail is appended after a slash; the engine splits it
  // back apart for display and can roll it up into per-app totals.
  readonly property string currentKey: {
    if (currentAppId.length === 0) return ""
    if (currentDetail.length === 0) return currentAppId
    if (detailForApp !== currentAppId) return currentAppId
    return currentAppId + "/" + currentDetail
  }

  readonly property bool seatIdle: idleMonitor.isIdle === true
  // Extra app ids the user never wants counted, on top of the built-in
  // screensaver/lock surfaces.
  readonly property var ignoreRules: Tracker.ignoreRules(setting("ignoredApps", ""))
  readonly property bool appIgnored: Tracker.isIgnored(currentAppId, ignoreRules)
  readonly property bool shouldCount: currentAppId.length > 0 && !seatIdle && !appIgnored

  property var trackerState: Tracker.emptyState()
  property bool _pendingSnapshot: false

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  // ---- accrual -----------------------------------------------------------

  function observeNow() {
    trackerState = Tracker.observe(trackerState, {
      now: Date.now(),
      appId: currentKey,
      active: shouldCount,
      intervalMs: sampleIntervalMs,
      maxStepMs: sampleIntervalMs * 4
    })
    liveToday = Tracker.liveTotal(committedToday, trackerState, todayKey)
  }

  // Focus, detail and idle changes must close the open window immediately,
  // otherwise up to one sample interval lands on the wrong row.
  onCurrentKeyChanged: observeNow()
  onSeatIdleChanged: {
    observeNow()
    // Coming back from idle: bank what we have so a crash later cannot lose
    // a whole session, and refresh so the panel is current when reopened.
    if (!seatIdle) Qt.callLater(commit)
  }

  // ---- detail resolution -------------------------------------------------

  function resolveDetail() {
    if (detailLevel === "off") {
      currentDetail = ""
      return
    }
    if (currentAppId.length === 0) {
      currentDetail = ""
      return
    }
    if (resolveProc.running) {
      _resolveQueued = true
      return
    }
    _resolveApp = currentAppId
    _resolveTitle = currentTitle
    resolveOut.reset()
    resolveErr.reset()
    resolveProc.command = [resolverPath, _resolveApp, _resolveTitle, detailLevel]
    resolveProc.running = true
  }

  // The title changes on every tab switch and every command a terminal runs,
  // so resolution is debounced rather than fired on each keystroke of a title.
  onCurrentTitleChanged: resolveDebounce.restart()
  onCurrentAppIdChanged: {
    // A new window: the old detail is definitely stale.
    if (detailForApp !== currentAppId) currentDetail = ""
    resolveDebounce.restart()
  }
  onDetailLevelChanged: {
    currentDetail = ""
    resolveDetail()
  }

  property string _resolveApp: ""
  property string _resolveTitle: ""
  property bool _resolveQueued: false

  function commit() {
    if (committing) {
      _pendingSnapshot = true
      return
    }
    observeNow()
    var payload = JSON.stringify(Tracker.commitPayload(trackerState))
    // Clear the buffer before the write returns. If the helper fails we lose
    // one batch; keeping it would risk double-counting on a partial success,
    // and over-reporting screen time is the worse failure for this plugin.
    trackerState = Tracker.drained(trackerState)
    committing = true
    commitOut.reset()
    commitErr.reset()
    // Runs even with nothing accrued, because this is also what drains the
    // network sampler's buffer, and data moves while the seat is idle. An empty
    // payload with nothing pending does not rewrite the store.
    commitProc.command = helperArgs(["commit", payload, "--prune"])
    commitProc.running = true
  }

  function refresh() {
    if (snapshotProc.running) return
    snapshotOut.reset()
    snapshotErr.reset()
    snapshotProc.command = helperArgs(["snapshot"])
    snapshotProc.running = true
  }

  // One pass over the socket table. Deliberately not chained to `commit`: the
  // sampler has to run far more often than the store is rewritten, and it only
  // touches its own small state file.
  function sampleNetwork() {
    if (!trackNetwork) return
    if (netProc.running) return
    netOut.reset()
    netErr.reset()
    netProc.command = [helperPath, "netsample"]
    netProc.running = true
  }

  // argparse binds options declared on the parent parser *before* the
  // subcommand, so the global flags go first and the subcommand and its own
  // flags follow. Appending them after "commit" makes argparse reject the
  // whole invocation.
  function helperArgs(extra) {
    var args = [helperPath]
    args.push("--goal", String(goalHours))
    args.push("--retention", String(retentionDays))
    args.push("--months", String(historyMonths))
    args.push("--offset", String(windowOffset))
    args.push(mondayFirst ? "--monday" : "--sunday")
    if (selectedDay.length > 0) args.push("--day", selectedDay)
    for (var i = 0; i < extra.length; i++) args.push(extra[i])
    return args
  }

  function applySnapshot(text, stderrText) {
    var parsed = Model.parseSnapshot(text)
    if (parsed.ok) {
      snapshot = parsed
      lastError = parsed.error
    } else {
      lastError = parsed.error
        || Model.safeText(stderrText, 240)
        || "Could not read the screen time store"
    }
    liveToday = Tracker.liveTotal(committedToday, trackerState, todayKey)
  }

  function selectDay(dayKey) {
    var key = String(dayKey || "")
    selectedDay = selectedDay === key ? "" : key
    refresh()
  }

  // delta < 0 walks back in time, delta > 0 walks forward toward today.
  // One month per step: with a six-month window that scrubs smoothly instead
  // of jumping a whole window and losing the overlap.
  function stepWindow(delta) {
    var step = Math.round(Number(delta) || 0)
    if (step === 0) return
    var next = windowOffset - step
    if (next < 0) next = 0
    if (next === windowOffset) return
    if (next > windowOffset && snapshot.canGoBack !== true) return
    windowOffset = next
    selectedDay = ""
    refresh()
  }

  function resetWindow() {
    if (windowOffset === 0 && selectedDay.length === 0) return
    windowOffset = 0
    selectedDay = ""
    refresh()
  }

  onHistoryMonthsChanged: Qt.callLater(refresh)

  Component.onCompleted: {
    trackerState = Tracker.rebase(Tracker.emptyState(), Date.now())
    refresh()
  }

  // Bank the buffer on the way out so a clean shell restart loses nothing.
  Component.onDestruction: {
    if (Tracker.isEmpty(trackerState)) return
    var payload = JSON.stringify(Tracker.commitPayload(trackerState))
    Quickshell.execDetached(helperArgs(["commit", payload]))
  }

  IdleMonitor {
    id: idleMonitor
    // Respecting inhibitors is deliberate: a video player holds an idle
    // inhibitor while it plays, and watching a two-hour film with no input is
    // still two hours of screen time.
    timeout: root.idleTimeoutSec
    respectInhibitors: true
  }

  Timer {
    id: sampleTimer
    interval: root.sampleIntervalMs
    running: true
    repeat: true
    onTriggered: root.observeNow()
  }

  // Debounce: a terminal running a command rewrites its title repeatedly, and
  // each resolve is a process spawn.
  Timer {
    id: resolveDebounce
    interval: 900
    repeat: false
    onTriggered: root.resolveDetail()
  }

  // A terminal can change what it is running without changing its title (a
  // shell with no integration), so re-check periodically as well.
  Timer {
    id: resolvePoll
    interval: 20000
    running: root.detailLevel !== "off"
    repeat: true
    onTriggered: if (root.shouldCount) root.resolveDetail()
  }

  Process {
    id: resolveProc
    running: false
    command: []
    stdout: BoundedReader {
      id: resolveOut
      producer: resolveProc
      maxBytes: root.maxReplyBytes
    }
    // Read but not kept: the resolver's own diagnostics are not the user's
    // problem. Draining it anyway is deliberate — an unread pipe fills up and
    // blocks the helper mid-write instead of letting it exit.
    stderr: BoundedReader {
      id: resolveErr
      producer: resolveProc
      maxBytes: 0
      stopOnOverflow: false
    }
    onExited: function (exitCode) {
      var detail = ""
      if (exitCode === 0 && !resolveOut.overflowed) {
        try {
          var parsed = JSON.parse(String(resolveOut.collected() || "{}"))
          if (parsed && parsed.app === root._resolveApp)
            detail = Tracker.normalizeDetail(parsed.detail)
        } catch (e) {
          detail = ""
        }
      }
      // Only adopt the answer if it still describes the focused window.
      if (root._resolveApp === root.currentAppId) {
        root.detailForApp = root._resolveApp
        root.detailForTitle = root._resolveTitle
        root.currentDetail = detail
      }
      if (root._resolveQueued) {
        root._resolveQueued = false
        Qt.callLater(root.resolveDetail)
      }
    }
  }

  Timer {
    id: commitTimer
    interval: root.commitIntervalMs
    running: true
    repeat: true
    onTriggered: root.commit()
  }

  // The sampler is not gated on `shouldCount`: a download finishing while the
  // screen is locked is still data you used, even though it is not screen time.
  Timer {
    id: netTimer
    interval: root.netSampleIntervalMs
    running: root.trackNetwork
    repeat: true
    triggeredOnStart: true
    onTriggered: root.sampleNetwork()
  }

  Process {
    id: netProc
    running: false
    command: []
    stdout: BoundedReader {
      id: netOut
      producer: netProc
      maxBytes: root.maxReplyBytes
    }
    // Swallowed on purpose: `ss` writes warnings about sockets it could not
    // read, and none of them are the user's problem. Kept at zero rather than
    // left unread, so a chatty run drains instead of filling the pipe, and
    // never cuts the sample short.
    stderr: BoundedReader {
      id: netErr
      producer: netProc
      maxBytes: 0
      stopOnOverflow: false
    }
    onExited: function (exitCode) {
      if (exitCode !== 0 || netOut.overflowed) return
      try {
        var parsed = JSON.parse(String(netOut.collected() || "{}"))
        if (parsed && parsed.ok === true) {
          root.netSockets = Number(parsed.sockets) || 0
          var pending = parsed.pending || [0, 0]
          root.netPendingDown = Number(pending[0]) || 0
          root.netPendingUp = Number(pending[1]) || 0
        }
      } catch (e) {
        // A malformed reply is not worth surfacing: the next sample overwrites
        // it, and the bytes are already banked in the state file either way.
      }
    }
  }

  // Roll the day over at midnight even if nothing else happens: the buffer is
  // already split per day, this just makes the bar reset promptly.
  Timer {
    id: middayGuard
    interval: 30000
    running: true
    repeat: true
    property string lastSeenDay: Tracker.dayKeyOf(Date.now())
    onTriggered: {
      var key = Tracker.dayKeyOf(Date.now())
      if (key === lastSeenDay) return
      lastSeenDay = key
      root.commit()
    }
  }

  Process {
    id: commitProc
    running: false
    command: []
    stdout: BoundedReader {
      id: commitOut
      producer: commitProc
      maxBytes: root.maxSnapshotBytes
    }
    stderr: BoundedReader {
      id: commitErr
      producer: commitProc
      maxBytes: root.maxStderrBytes
      // Capped but never fatal. The ceiling is what bounds the allocation; a
      // helper being noisy on stderr is not a reason to kill a commit, and the
      // store has already been written by the time it prints anything anyway.
      stopOnOverflow: false
    }
    onExited: function (exitCode) {
      root.committing = false
      // Only stdout overflowing means the reply itself was too big to trust.
      if (commitOut.overflowed)
        root.lastError = Stream.overflowMessage("output")
      else if (exitCode === 0)
        root.applySnapshot(commitOut.collected(), commitErr.collected())
      else
        root.lastError = Model.safeText(commitErr.collected(), 240) || "Could not save screen time"
      if (root._pendingSnapshot) {
        root._pendingSnapshot = false
        Qt.callLater(root.refresh)
      }
    }
  }

  Process {
    id: snapshotProc
    running: false
    command: []
    stdout: BoundedReader {
      id: snapshotOut
      producer: snapshotProc
      maxBytes: root.maxSnapshotBytes
    }
    stderr: BoundedReader {
      id: snapshotErr
      producer: snapshotProc
      maxBytes: root.maxStderrBytes
      stopOnOverflow: false
    }
    onExited: function (exitCode) {
      if (snapshotOut.overflowed)
        root.lastError = Stream.overflowMessage("output")
      else
        root.applySnapshot(snapshotOut.collected(), snapshotErr.collected())
    }
  }

  IpcHandler {
    target: "screentime"

    function today(): string {
      return root.todayLabel
    }
    function total(): string {
      return String(root.liveToday)
    }
    function app(): string {
      return root.currentAppId
    }
    function flush(): string {
      root.commit()
      return "ok"
    }
    function refresh(): string {
      root.refresh()
      return "ok"
    }
    function net(): string {
      var today = root.snapshot.todayNet || Model.emptyNet()
      return "D " + today.downLabel + " \u00b7 U " + today.upLabel
    }
    function status(): string {
      return JSON.stringify({
        today: root.todayLabel,
        seconds: root.liveToday,
        app: root.currentAppId,
        detail: root.currentDetail,
        key: root.currentKey,
        title: root.currentTitle,
        detailLevel: root.detailLevel,
        counting: root.shouldCount,
        idle: root.seatIdle,
        ignored: root.appIgnored,
        goalHours: root.goalHours,
        overGoal: root.overGoal,
        pending: root.trackerState.pendingSeconds || 0,
        droppedGaps: root.trackerState.droppedGaps || 0,
        trackNetwork: root.trackNetwork,
        netToday: root.snapshot.todayNet || Model.emptyNet(),
        netPending: [root.netPendingDown, root.netPendingUp],
        netSockets: root.netSockets,
        weeks: (root.snapshot.weeks || []).length,
        range: root.snapshot.rangeLabel || "",
        months: root.historyMonths,
        offset: root.windowOffset,
        store: root.snapshot.storePath || "",
        helper: root.helperPath,
        error: root.lastError
      })
    }
  }
}
