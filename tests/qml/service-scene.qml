import QtQuick
import Quickshell

// Drives the real Service.qml, with the real bin/screentime beside it, and
// reports what the service made of the reply.
//
// The reader tests prove the boundary in isolation; this one proves it is
// actually wired into the service — that the ceilings named in Service.qml
// reach the readers, and that a helper flooding stdout lands in lastError
// instead of in the shell's heap.
//
// Run as the shell.qml of a config folder holding a copy of the plugin. See
// tests/test_qml_reader.py.
//   SCREENTIME_SETTLE_MS  how long to let the service run before reporting
ShellRoot {
  id: scene

  readonly property int settleMs: {
    var value = Quickshell.env("SCREENTIME_SETTLE_MS")
    var parsed = parseInt(String(value === undefined || value === null ? "" : value), 10)
    return isFinite(parsed) && parsed > 0 ? parsed : 3000
  }

  Service { id: service }

  Timer {
    running: true
    interval: scene.settleMs
    repeat: false
    onTriggered: {
      console.log("SERVICE_RESULT " + JSON.stringify({
        ok: service.snapshot.ok === true,
        error: String(service.lastError || ""),
        todayLabel: String(service.todayLabel || ""),
        weeks: (service.snapshot.weeks || []).length,
        hasStorePath: String(service.snapshot.storePath || "").length > 0
      }))
      Qt.exit(0)
    }
  }
}
