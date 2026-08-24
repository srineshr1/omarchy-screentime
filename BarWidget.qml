import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "lib/Model.js" as Model

// Today's screen time in the bar, with the last seven days as heatmap boxes.
// All state lives in the service (loaded for the whole session); this reads it.
BarWidget {
  id: root
  moduleName: "io.github.ricky.screentime"

  // Font Awesome "desktop". Kept in the BMP so it needs no surrogate pair.
  readonly property string glyph: "\uf108"

  readonly property var service: bar && bar.shell
    ? bar.shell.serviceFor("io.github.ricky.screentime")
    : null

  readonly property int liveToday: service ? service.liveToday : 0
  readonly property bool overGoal: service ? service.overGoal : false
  readonly property var recentDays: service ? service.recentDays : []
  readonly property var snapshot: service ? service.snapshot : Model.emptySnapshot()

  // strip = boxes + time, total = time only, icon = glyph only.
  readonly property string barMode: {
    var value = String(root.setting("barMode", "strip"))
    if (value !== "strip" && value !== "total" && value !== "icon") return "strip"
    return value
  }
  readonly property bool showStrip: barMode === "strip" && !root.vertical && recentDays.length > 0
  readonly property string label: root.vertical
    ? Model.barLabel(liveToday, "clock")
    : Model.barLabel(liveToday, barMode === "icon" ? "icon" : "duration")

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true : false

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function toggle() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  function injectPanel() {
    if (!panelLoader.item) return
    panelLoader.item.bar = root.bar
    panelLoader.item.anchorItem = button
    panelLoader.item.hostWidget = root
    panelLoader.item.service = root.service
    panelLoader.item.settings = root.settings
  }

  // The widget owns the settings (they live on its shell.json entry), so it
  // hands them to the session-wide service; the daily limit configured here is
  // what the service buckets against.
  function pushSettings() {
    if (root.service) root.service.settings = root.settings
  }

  function flush() {
    if (root.service) root.service.commit()
  }

  // Right-click cycles the bar display and remembers it on the widget entry.
  function cycleBarMode() {
    var order = ["strip", "total", "icon"]
    var next = order[(order.indexOf(root.barMode) + 1) % order.length]
    var entry = { id: root.moduleName }
    for (var key in root.settings) if (key !== "id") entry[key] = root.settings[key]
    entry.barMode = next
    root.settings = entry
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function")
      root.bar.shell.updateEntryInline(root.moduleName, entry)
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: {
    injectPanel()
    pushSettings()
  }
  onSettingsChanged: {
    pushSettings()
    injectPanel()
  }
  onServiceChanged: {
    pushSettings()
    injectPanel()
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    hasVisualContent: true
    labelVisible: false
    active: root.overGoal
    // Nothing here is user- or network-supplied: the shell paints tooltipText
    // in a shared Text that leaves textFormat at AutoText, so only
    // plugin-owned phrases and formatted durations are allowed through.
    tooltipText: Model.barTooltip(root.snapshot, root.liveToday)
    fixedWidth: root.vertical
      ? root.barSize
      : Math.max(Style.space(24), contents.implicitWidth + Style.space(14))
    fixedHeight: root.barSize

    onPressed: function (buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
      else if (buttonCode === Qt.MiddleButton) root.flush()
      else if (buttonCode === Qt.RightButton) root.cycleBarMode()
    }

    Row {
      id: contents
      anchors.centerIn: parent
      spacing: Style.space(6)
      visible: !root.vertical

      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: root.glyph
        color: button.active && button.useActiveColor
          ? button.activeColor
          : (root.bar ? root.bar.barForeground : Color.foreground)
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
      }

      UsageGrid {
        visible: root.showStrip
        anchors.verticalCenter: parent.verticalCenter
        mode: "strip"
        days: root.recentDays
        todayKey: root.service ? root.service.todayKey : ""
        palette: String(root.setting("gridPalette", "accent"))
        accent: Color.accent
        urgent: root.bar ? root.bar.urgent : Color.urgent
        empty: root.bar ? root.bar.barForeground : Color.muted
        foreground: root.bar ? root.bar.barForeground : Color.foreground
        fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
        cellSize: Math.max(6, Style.space(8))
        cellGap: Style.space(2)
        showWeekdays: false
        showMonths: false
        interactive: false
      }

      Text {
        visible: root.label.length > 0
        anchors.verticalCenter: parent.verticalCenter
        text: root.label
        color: button.active && button.useActiveColor
          ? button.activeColor
          : (root.bar ? root.bar.barForeground : Color.foreground)
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
      }
    }

    // Vertical bars hide the button label, so the glyph and a compact 4:30
    // clock are stacked instead.
    Column {
      visible: root.vertical
      anchors.centerIn: parent
      spacing: Style.space(2)

      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: root.glyph
        color: button.active && button.useActiveColor
          ? button.activeColor
          : (root.bar ? root.bar.barForeground : Color.foreground)
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
      }

      Text {
        visible: root.barMode !== "icon"
        anchors.horizontalCenter: parent.horizontalCenter
        text: root.label
        color: button.active && button.useActiveColor
          ? button.activeColor
          : (root.bar ? root.bar.barForeground : Color.foreground)
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.caption
      }
    }
  }
}
