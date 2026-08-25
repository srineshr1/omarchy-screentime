import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "lib/Model.js" as Model

// The screen time panel: today at the top, a year of daily boxes in the
// middle, the last seven days at the bottom. Clicking a box in the heatmap
// swaps the breakdown over to that day.
Panel {
  id: root
  moduleName: "io.github.ricky.screentime"
  ipcTarget: "io.github.ricky.screentime"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  readonly property var barIdentity: hostWidget || root

  property bool showAllApps: false
  // Which app folders are open, keyed by app id. Reassigned wholesale rather
  // than mutated so the bindings that read it actually re-evaluate.
  property var expandedApps: ({})
  property bool expandAll: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color secondaryForeground: Qt.darker(foreground, 1.5)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var snapshot: service ? service.snapshot : Model.emptySnapshot()
  readonly property int liveToday: service ? service.liveToday : 0
  readonly property int goalSeconds: Number(snapshot.goalSeconds) || 21600
  readonly property string selectedDay: service ? service.selectedDay : ""
  readonly property bool viewingPast: selectedDay.length > 0 && selectedDay !== snapshot.today

  // The breakdown follows the selection: today by default, otherwise whichever
  // heatmap box was clicked. One node per app, resolved detail as its children.
  readonly property var breakdownApps: {
    if (root.viewingPast) return snapshot.selectedTree || []
    return snapshot.todayTree || []
  }
  readonly property var visibleApps: root.showAllApps
    ? (root.breakdownApps || [])
    : (root.breakdownApps || []).slice(0, 7)

  // Whether anything can be opened at all; with detailLevel off, nothing can.
  readonly property bool hasFolders: {
    var list = root.breakdownApps || []
    for (var i = 0; i < list.length; i++) {
      if (list[i].children && list[i].children.length > 0) return true
    }
    return false
  }
  readonly property int breakdownTotal: root.viewingPast
    ? Number(snapshot.selectedTotal) || 0
    : root.liveToday
  readonly property var breakdownNet: root.viewingPast
    ? snapshot.selectedNet
    : snapshot.todayNet
  // Nothing to show a column for until some traffic has been recorded, and the
  // sampler can be switched off entirely.
  readonly property bool showNet: snapshot.netTracked === true
  readonly property string breakdownTitle: root.viewingPast
    ? Model.formatLongDate(root.selectedDay).toUpperCase()
    : "TODAY"

  readonly property int cellSize: Style.space(12)
  readonly property int cellGap: Style.space(3)
  // A comfortable popup width; the heatmap fits itself into whatever is left
  // rather than the panel stretching to fit 53 columns of boxes.
  readonly property int bodyWidth: Style.space(460)

  function open() {
    setCenterHoverRevealSuppressed(false)
    if (service) service.refresh()
    root.controller.show()
  }

  function openFromHotkey() {
    if (service) service.refresh()
    root.controller.show()
    Qt.callLater(function () {
      if (root.opened) setCenterHoverRevealSuppressed(true)
    })
  }

  function close() {
    setCenterHoverRevealSuppressed(false)
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.openFromHotkey()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function setCenterHoverRevealSuppressed(value) {
    if (root.bar && "centerHoverRevealSuppressed" in root.bar)
      root.bar.centerHoverRevealSuppressed = value
  }

  function refresh() {
    if (service) service.commit()
  }

  function selectDay(dayKey) {
    if (service) service.selectDay(dayKey)
  }

  function clearSelection() {
    if (service && service.selectedDay.length > 0) service.selectDay(service.selectedDay)
  }

  function isExpanded(id) {
    if (root.expandAll) return true
    return root.expandedApps[String(id)] === true
  }

  function toggleApp(id) {
    var key = String(id)
    var next = {}
    for (var existing in root.expandedApps) next[existing] = root.expandedApps[existing]
    if (root.expandAll) {
      // Collapsing one folder while everything is open: keep the rest open.
      for (var i = 0; i < root.breakdownApps.length; i++)
        next[String(root.breakdownApps[i].id)] = true
      root.expandAll = false
    }
    if (next[key]) delete next[key]
    else next[key] = true
    root.expandedApps = next
  }

  function toggleExpandAll() {
    root.expandAll = !root.expandAll
    root.expandedApps = ({})
  }

  function stepWindow(delta) {
    if (service) service.stepWindow(delta)
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.openFromHotkey() }
    function close(): void { root.close() }
    function show(): void { root.openFromHotkey() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { root.refresh() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(root.bodyWidth)
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(700))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (text) {
        if (text === "r" || text === "R") root.refresh()
        else if (text === "t" || text === "T") root.clearSelection()
        else if (text === "a" || text === "A") root.showAllApps = !root.showAllApps
        else if (text === "g" || text === "G") root.toggleExpandAll()
        else if (text === "[") root.stepWindow(-1)
        else if (text === "]") root.stepWindow(1)
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        // No ScrollBar attached on purpose: the panel still scrolls by wheel,
        // drag and keyboard, but a visible bar overlaps the content in a
        // surface this narrow.

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: Model.formatDuration(root.liveToday)
            meta: {
              var bits = []
              var delta = Model.deltaLabel(root.liveToday, root.snapshot.yesterdayTotal)
              if (delta) bits.push(delta)
              var remaining = root.goalSeconds - root.liveToday
              bits.push(remaining > 0
                ? Model.formatDuration(remaining) + " left today"
                : Model.formatDuration(-remaining) + " over limit")
              return bits.join(" \u00b7 ")
            }
            // No `detail` here on purpose: the badge it rendered sat next to
            // the title and read as a notification. The streak moved to the
            // footer line with the rest of the standing totals.
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                text: "\uf108"
                color: root.snapshot.overGoal ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
            trailingControl: Component {
              PanelActionButton {
                iconText: "\uf021"
                tooltipText: "Refresh"
                enabled: root.service && !root.service.committing
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.refresh()
              }
            }
          }

          // Progress toward the daily limit.
          Rectangle {
            width: parent.width
            height: Style.space(4)
            radius: height / 2
            color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14)

            Rectangle {
              width: parent.width * Model.goalFraction(root.liveToday, root.goalSeconds)
              height: parent.height
              radius: parent.radius
              color: root.snapshot.overGoal ? root.urgent : Color.accent
              Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
            }
          }

          Text {
            visible: root.service && root.service.lastError.length > 0
            width: parent.width
            text: root.service ? root.service.lastError : ""
            textFormat: Text.PlainText
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          PanelSeparator { foreground: root.foreground }

          // ---- per-app breakdown ------------------------------------------

          Item {
            width: parent.width
            implicitHeight: Math.max(breakdownHeader.implicitHeight, breakdownMeta.implicitHeight)

            PanelSectionHeader {
              id: breakdownHeader
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: root.breakdownTitle
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Row {
              id: breakdownMeta
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(10)

              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: {
                  var data = Model.netLabel(root.breakdownNet, true)
                  var time = Model.formatDuration(root.breakdownTotal)
                  return data ? data + " \u00b7 " + time : time
                }
                color: root.secondaryForeground
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

              Text {
                visible: root.hasFolders
                text: root.expandAll ? "collapse" : "expand all"
                anchors.verticalCenter: parent.verticalCenter
                color: Color.accent
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                MouseArea {
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.toggleExpandAll()
                }
              }

              Text {
                visible: root.viewingPast
                anchors.verticalCenter: parent.verticalCenter
                text: "back to today"
                color: Color.accent
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                MouseArea {
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.clearSelection()
                }
              }
            }
          }

          Text {
            visible: root.visibleApps.length === 0
            width: parent.width
            text: {
              if (root.snapshot.selectedDetailExpired === true)
                return "Per-app detail for this day has aged out. The daily total is kept."
              if (root.viewingPast) return "No screen time recorded on this day."
              return "Nothing recorded yet today."
            }
            textFormat: Text.PlainText
            color: root.secondaryForeground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.visibleApps.length > 0

            Repeater {
              model: root.visibleApps

              // One app folder: its own row, then its children when open.
              Column {
                id: folder
                required property var modelData
                readonly property var kids: modelData.children || []
                readonly property bool open: root.isExpanded(modelData.id)
                width: parent.width
                spacing: Style.space(6)

                UsageRow {
                  width: folder.width
                  row: folder.modelData
                  expandable: folder.kids.length > 0
                  expanded: folder.open
                  showNet: root.showNet
                  foreground: root.foreground
                  dim: root.secondaryForeground
                  accent: Color.accent
                  fontFamily: root.fontFamily
                  onToggled: root.toggleApp(folder.modelData.id)
                }

                Column {
                  width: folder.width
                  spacing: Style.space(6)
                  visible: folder.open && folder.kids.length > 0

                  Repeater {
                    model: folder.kids
                    UsageRow {
                      required property var modelData
                      width: folder.width
                      row: modelData
                      child: true
                      showNet: root.showNet
                      foreground: root.foreground
                      dim: root.secondaryForeground
                      accent: Color.accent
                      fontFamily: root.fontFamily
                    }
                  }
                }
              }
            }

            Text {
              visible: root.breakdownApps.length > 7
              text: root.showAllApps
                ? "Show less"
                : "Show all " + root.breakdownApps.length + " apps"
              color: Color.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.showAllApps = !root.showAllApps
              }
            }
          }

          PanelSeparator { foreground: root.foreground }

          // ---- the rolling window of boxes ---------------------------------

          Item {
            width: parent.width
            implicitHeight: Math.max(rangeHeader.implicitHeight, rangeNav.implicitHeight)

            PanelSectionHeader {
              id: rangeHeader
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: root.snapshot.rangeLabel || "HISTORY"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Row {
              id: rangeNav
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(6)

              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: {
                  var avg = Number(root.snapshot.rangeDailyAverage) || 0
                  if (avg <= 0) return root.snapshot.rangeTotalLabel || ""
                  return root.snapshot.rangeTotalLabel + " \u00b7 "
                    + Model.formatDuration(avg) + "/day"
                }
                color: root.secondaryForeground
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

              PanelActionButton {
                anchors.verticalCenter: parent.verticalCenter
                iconText: "\uf053"
                tooltipText: "Earlier months"
                enabled: root.snapshot.canGoBack === true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.stepWindow(-1)
              }

              PanelActionButton {
                anchors.verticalCenter: parent.verticalCenter
                iconText: "\uf054"
                tooltipText: "Later months"
                enabled: root.snapshot.canGoForward === true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.stepWindow(1)
              }
            }
          }

          UsageGrid {
            id: heatmap
            width: parent.width
            // Cells solve for this instead of overflowing it, so the newest
            // column always lands inside the panel.
            availableWidth: parent.width
            mode: "calendar"
            weeks: root.snapshot.weeks || []
            todayKey: root.snapshot.today
            selectedDate: root.selectedDay
            palette: String(root.setting("gridPalette", "accent"))
            mondayFirst: root.snapshot.weekStartsMonday !== false
            accent: Color.accent
            urgent: root.urgent
            // Muted, not foreground: untracked days should sit behind the data
            // rather than compete with it.
            empty: Color.muted
            foreground: root.foreground
            fontFamily: root.fontFamily
            cellSize: root.cellSize
            cellGap: root.cellGap
            onDayClicked: function (date) { root.selectDay(date) }
          }

          // Legend, mirroring GitHub's "less <boxes> more".
          Row {
            spacing: Style.space(4)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "less"
              color: root.secondaryForeground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            Repeater {
              model: ["NONE", "L1", "L2", "L3", "L4", "OVER"]
              Rectangle {
                required property var modelData
                anchors.verticalCenter: parent.verticalCenter
                width: heatmap.cell
                height: heatmap.cell
                radius: heatmap.cellRadius
                color: heatmap.fillFor(modelData)
                border.width: modelData === "NONE" ? 1 : 0
                border.color: heatmap.emptyStroke
              }
            }

            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "more \u00b7 over " + root.snapshot.goalHours + "h"
              color: root.secondaryForeground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          PanelSeparator { foreground: root.foreground }

          // ---- last seven days --------------------------------------------

          Item {
            width: parent.width
            implicitHeight: Math.max(weekHeader.implicitHeight, weekMeta.implicitHeight)

            PanelSectionHeader {
              id: weekHeader
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: "LAST 7 DAYS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              id: weekMeta
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: {
                var bits = [root.snapshot.weekLabel + " \u00b7 "
                  + Model.formatDuration(root.snapshot.weekDailyAverage) + "/day"]
                var data = Model.netLabel(root.snapshot.weekNet, true)
                if (data) bits.push(data)
                return bits.join(" \u00b7 ")
              }
              color: root.secondaryForeground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Row {
            id: weekRow
            width: parent.width
            height: Style.space(72)
            spacing: Style.space(6)

            readonly property int peak: Model.peakSeconds(root.snapshot.weekDays || [])
            readonly property real columnWidth: (width - spacing * 6) / 7

            Repeater {
              model: root.snapshot.weekDays || []

              Item {
                required property var modelData
                width: weekRow.columnWidth
                height: weekRow.height

                MouseArea {
                  id: dayMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectDay(String(modelData.date))
                }

                // Bar grows from the baseline that sits above the caption.
                Rectangle {
                  readonly property bool empty: !(modelData.seconds > 0)
                  anchors.horizontalCenter: parent.horizontalCenter
                  anchors.bottom: caption.top
                  anchors.bottomMargin: Style.space(4)
                  width: parent.width * 0.62
                  radius: Style.space(2)
                  height: empty
                    ? Style.space(2)
                    : Math.max(Style.space(3),
                        (parent.height - caption.height - Style.space(8))
                          * Model.relativeShare(modelData.seconds, weekRow.peak))
                  color: {
                    // A day with nothing on it is a faint rule, not a stub of
                    // colour that reads as a small amount of usage.
                    if (empty) return Qt.rgba(root.foreground.r, root.foreground.g,
                                              root.foreground.b, 0.16)
                    if (modelData.date === root.selectedDay) return Color.accent
                    if (modelData.level === "OVER") return root.urgent
                    return Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b,
                      dayMouse.containsMouse ? 0.9 : 0.6)
                  }
                  Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                }

                Text {
                  id: caption
                  anchors.bottom: parent.bottom
                  anchors.horizontalCenter: parent.horizontalCenter
                  text: Model.weekdayInitial(modelData.date)
                  color: modelData.date === root.snapshot.today
                    ? root.foreground
                    : root.secondaryForeground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: modelData.date === root.snapshot.today
                }

                PanelToolTip {
                  visible: dayMouse.containsMouse
                  text: Model.dayTooltip(modelData)
                  fontFamily: root.fontFamily
                }
              }
            }
          }

          // ---- footer ------------------------------------------------------

          Text {
            width: parent.width
            text: {
              var bits = []
              var streak = Number(root.snapshot.streak) || 0
              if (streak > 0)
                bits.push(streak + (streak === 1 ? " day" : " days") + " under limit")
              if (root.snapshot.daysTracked > 0)
                bits.push(root.snapshot.daysTracked + " days tracked")
              if (root.snapshot.allTimeTotal > 0)
                bits.push(root.snapshot.allTimeLabel + " all time")
              if (root.snapshot.bestDay && root.snapshot.bestDay.seconds > 0)
                bits.push("busiest " + Model.formatLongDate(root.snapshot.bestDay.date)
                  + " (" + root.snapshot.bestDay.label + ")")
              var data = Model.netLabel(root.snapshot.allTimeNet, false)
              if (data) bits.push(data + " all time")
              return bits.join(" \u00b7 ")
            }
            textFormat: Text.PlainText
            color: root.secondaryForeground
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }
        }
      }
    }
  }
}
