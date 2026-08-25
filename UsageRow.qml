import QtQuick
import qs.Commons
import qs.Ui
import "lib/Model.js" as Model

// One line of the breakdown: a name, its data usage, a duration, and a share
// bar under all three.
//
// A parent row is an app and behaves like a folder — a chevron on the left, the
// whole row clickable to open it. A child row is what was running inside that
// app, indented under it and not clickable.
Item {
  id: root

  property var row: ({})
  property bool child: false
  property bool expandable: false
  property bool expanded: false
  // Off until the store has some traffic in it, so a fresh install does not
  // show an empty column.
  property bool showNet: false

  property color foreground: Color.foreground
  property color dim: Qt.darker(Color.foreground, 1.5)
  property color accent: Color.accent
  property string fontFamily: Style.font.family

  signal toggled()

  readonly property real indent: child ? Style.space(20) : 0
  readonly property real chevronWidth: Style.space(14)
  readonly property real fraction: Math.max(0, Math.min(1, Number(row ? row.share : 0) || 0))
  // "other" is the plugin's own word for unresolved time, not an app name, so
  // it is set in the dim colour to read as a remainder rather than a program.
  readonly property bool remainder: child && String(row ? row.name : "") === "other"
  readonly property string netText: Model.netLabel(row ? row.net : null, true)

  implicitHeight: label.implicitHeight + Style.space(8)

  MouseArea {
    id: mouse
    anchors.fill: parent
    enabled: root.expandable
    hoverEnabled: enabled
    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
    acceptedButtons: enabled ? Qt.LeftButton : Qt.NoButton
    onClicked: root.toggled()
  }

  // Chevron: right when closed, down when open. Leaf rows keep the same gutter
  // so every name in the list starts at the same x.
  Text {
    id: chevron
    x: root.indent
    anchors.top: parent.top
    width: root.child ? 0 : root.chevronWidth
    visible: root.expandable
    text: root.expanded ? "\uf107" : "\uf105"
    color: mouse.containsMouse ? root.foreground : root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    horizontalAlignment: Text.AlignLeft
  }

  Text {
    id: label
    x: root.indent + (root.child ? 0 : root.chevronWidth)
    anchors.top: parent.top
    // Yields to the data column when there is one, so a long app name elides
    // instead of overprinting the numbers.
    width: Math.max(0, (data.visible ? data.x : duration.x) - x - Style.space(8))
    text: Model.safeText(root.row ? root.row.name : "", 40)
    textFormat: Text.PlainText
    color: root.remainder
      ? root.dim
      : (root.child || !mouse.containsMouse ? root.foreground : root.accent)
    font.family: root.fontFamily
    font.pixelSize: root.child ? Style.font.caption : Style.font.body
    elide: Text.ElideRight
  }

  // Down and up for this app. Blank rather than "D 0 B" when nothing was
  // measured: detail rows inside a folder have no traffic of their own, and a
  // confident zero there would be a lie.
  Text {
    id: data
    anchors.right: duration.left
    anchors.rightMargin: Style.space(10)
    anchors.baseline: label.baseline
    visible: root.showNet && root.netText.length > 0
    text: root.netText
    textFormat: Text.PlainText
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    id: duration
    anchors.right: parent.right
    anchors.top: parent.top
    text: root.row ? root.row.label : ""
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: root.child ? Style.font.caption : Style.font.body
  }

  // The share bar spans the full width for a parent and is inset for a child,
  // so the nesting is legible even with the chevron collapsed.
  //
  // Anchored on both sides rather than positioned with x: setting x together
  // with anchors.right leaves the width undefined and the bar never paints.
  Rectangle {
    anchors.left: parent.left
    anchors.leftMargin: root.indent
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    height: root.child ? Style.space(2) : Style.space(3)
    radius: height / 2
    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)

    Rectangle {
      width: parent.width * root.fraction
      height: parent.height
      radius: parent.radius
      color: root.remainder
        ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.28)
        : (root.child
            ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.62)
            : root.accent)
      Behavior on width { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
    }
  }
}
