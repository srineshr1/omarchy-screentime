import QtQuick
import qs.Commons
import qs.Ui
import "lib/Model.js" as Model

// GitHub's contribution graph, but the quantity is hours in front of a screen.
//
//   mode: "calendar"  seven rows of weekdays, one column per week, month
//                     captions on top and sparse weekday captions on the left
//   mode: "strip"     a single row of the last N days, sized for a bar slot
//
// Cells size themselves to the width they are given rather than being fixed.
// A fixed cell size either clips the newest column or leaves the grid floating
// in whitespace, depending on how many weeks the window happens to contain.
//
// Levels arrive from the helper already bucketed against the daily limit, so
// this component only maps a level to a colour. Two palettes: "accent" keeps
// GitHub's single-hue ramp using the theme accent, and "traffic" ramps green
// to amber and hands over to the theme's urgent colour once the day is past
// its limit — which is the one that actually reads as a wellbeing signal.
Item {
  id: root

  property string mode: "calendar"
  property var weeks: []
  property var days: []
  property string todayKey: ""
  property string selectedDate: ""
  property string palette: "accent"
  property bool mondayFirst: true

  property color accent: Color.accent
  property color urgent: Color.urgent
  property color empty: Color.muted
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  // Requested cell size. In calendar mode this is the *maximum*: the grid
  // shrinks cells to fit `availableWidth` when there are many columns.
  property int cellSize: Style.space(12)
  property int minCellSize: Style.space(7)
  property int cellGap: Style.space(2)
  property int cellRadius: Math.min(3, Style.cornerRadius)

  // Width the grid must fit into. 0 leaves cellSize untouched.
  property int availableWidth: 0

  property bool showWeekdays: mode === "calendar"
  property bool showMonths: mode === "calendar"
  property bool interactive: mode === "calendar"

  signal dayClicked(string date)

  readonly property int rows: 7
  readonly property var stripDays: mode === "strip" ? (days || []) : []
  readonly property var calendarWeeks: mode === "calendar" ? (weeks || []) : []
  readonly property var labels: showMonths ? Model.monthLabels(calendarWeeks) : []

  // Wide enough for a three-letter weekday plus breathing room, so "Mon" no
  // longer sits flush against the first column.
  readonly property int weekdayWidth: showWeekdays ? Style.space(30) : 0
  readonly property int monthHeight: showMonths ? Style.space(16) : 0

  // The actual cell size in use. Every measurement below reads this, never
  // cellSize, so the grid and its labels can never disagree about the pitch.
  readonly property int cell: {
    if (mode !== "calendar" || availableWidth <= 0 || calendarWeeks.length === 0)
      return cellSize
    return Model.fitCellSize(availableWidth, calendarWeeks.length, cellGap,
                             weekdayWidth, minCellSize, cellSize)
  }

  readonly property int gridWidth: mode === "strip"
    ? Model.gridWidth(stripDays.length, cell, cellGap, 0)
    : Model.gridWidth(calendarWeeks.length, cell, cellGap, 0)
  readonly property int gridHeight: mode === "strip"
    ? cell
    : rows * cell + (rows - 1) * cellGap

  implicitWidth: weekdayWidth + gridWidth
  implicitHeight: monthHeight + gridHeight

  // Traffic ramp. Fixed hues rather than theme colours: the whole point is
  // that "a lot" looks like a warning regardless of which theme is loaded.
  readonly property color trafficLow: "#1f6f47"
  readonly property color trafficMid: "#2f9e5f"
  readonly property color trafficHigh: "#d4a017"
  readonly property color trafficPeak: "#e07b39"

  // Untracked days sit well back so real usage is what the eye lands on.
  readonly property color emptyFill: Qt.rgba(root.empty.r, root.empty.g, root.empty.b, 0.13)
  readonly property color emptyStroke: Qt.rgba(root.empty.r, root.empty.g, root.empty.b, 0.22)

  function fillFor(level) {
    var name = String(level || "NONE")
    if (name === "NONE" || name === "") return root.emptyFill
    if (name === "OVER") return root.urgent
    if (root.palette === "traffic") {
      switch (name) {
        case "L1": return root.trafficLow
        case "L2": return root.trafficMid
        case "L3": return root.trafficHigh
        case "L4": return root.trafficPeak
      }
      return root.trafficLow
    }
    var a = root.accent
    switch (name) {
      case "L4": return a
      case "L3": return Qt.rgba(a.r, a.g, a.b, 0.78)
      case "L2": return Qt.rgba(a.r, a.g, a.b, 0.56)
      case "L1": return Qt.rgba(a.r, a.g, a.b, 0.34)
    }
    return Qt.rgba(a.r, a.g, a.b, 0.34)
  }

  // Month captions across the top, aligned to the week they start in.
  Repeater {
    model: root.showMonths ? root.labels : []
    Text {
      required property var modelData
      x: root.weekdayWidth + modelData.index * (root.cell + root.cellGap)
      y: 0
      height: root.monthHeight
      text: modelData.label
      color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.62)
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      verticalAlignment: Text.AlignVCenter
      visible: text !== ""
    }
  }

  Column {
    id: weekdayCol
    visible: root.showWeekdays
    x: 0
    y: root.monthHeight
    width: root.weekdayWidth
    spacing: root.cellGap
    Repeater {
      model: root.rows
      Text {
        required property int index
        // Labels track the cell pitch so they stay on their own row as the
        // grid resizes.
        width: root.weekdayWidth - Style.space(6)
        height: root.cell
        text: Model.weekdayCaption(index, root.mondayFirst)
        color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.62)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        horizontalAlignment: Text.AlignLeft
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideNone
      }
    }
  }

  // The window: a column of seven days per week.
  Row {
    visible: root.mode === "calendar"
    x: root.weekdayWidth
    y: root.monthHeight
    spacing: root.cellGap
    Repeater {
      model: root.calendarWeeks
      Column {
        required property var modelData
        spacing: root.cellGap
        Repeater {
          model: modelData.days || []
          DayCell {
            required property var modelData
            day: modelData
          }
        }
      }
    }
  }

  // The compact last-N-days strip for the bar.
  Row {
    visible: root.mode === "strip"
    x: 0
    y: 0
    spacing: root.cellGap
    Repeater {
      model: root.stripDays
      DayCell {
        required property var modelData
        day: modelData
      }
    }
  }

  component DayCell: Rectangle {
    id: cell
    property var day: ({})
    readonly property bool blank: !day || !day.date || day.outOfRange === true
    readonly property bool isToday: !blank && day.date === root.todayKey
    readonly property bool isSelected: !blank && root.selectedDate.length > 0
      && day.date === root.selectedDate
    readonly property bool untracked: !blank && !(day.seconds > 0)

    width: root.cell
    height: root.cell
    radius: root.cellRadius
    color: blank ? "transparent" : root.fillFor(day.level)
    visible: !blank
    opacity: mouse.containsMouse ? 0.78 : 1.0

    // Untracked days get a hairline so the grid still reads as a grid.
    // Today gets a ring in the theme accent; a clicked day gets a thicker one.
    border.width: cell.isSelected ? 2 : (cell.isToday ? 1 : (cell.untracked ? 1 : 0))
    border.color: cell.isSelected || cell.isToday ? root.accent : root.emptyStroke

    MouseArea {
      id: mouse
      anchors.fill: parent
      enabled: root.interactive && !cell.blank
      hoverEnabled: enabled
      cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
      acceptedButtons: enabled ? Qt.LeftButton : Qt.NoButton
      onClicked: if (!cell.blank) root.dayClicked(String(cell.day.date))
    }

    PanelToolTip {
      visible: root.interactive && mouse.containsMouse && text.length > 0
      text: Model.dayTooltip(cell.day)
      fontFamily: root.fontFamily
    }
  }
}
