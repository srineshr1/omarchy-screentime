import QtQuick
import Quickshell
import Quickshell.Io

// Headless scene that runs one output reader against one producer and prints
// the outcome as a single JSON line for tests/test_qml_reader.py to assert on.
//
// This test exists because the property it checks is not visible to any pure
// function: the question is whether the *shell* allocates a producer's entire
// output before QML gets a say. Answering it needs a real QProcess, a real pipe
// and a real reader, so the test drives the actual Quickshell runtime and the
// runner measures peak RSS from outside.
//
// Run as the shell.qml of a config folder the runner assembles, with
// BoundedReader.qml and lib/ copied in beside it, which is the same shape the
// plugin itself has — a component picked up from its own directory with no
// import, since quickshell refuses module imports resolving outside the folder
// it was pointed at.
//
// Scenario comes from the environment so the same scene covers the bounded
// reader and the StdioCollector it replaced:
//   SCREENTIME_READER    bounded | collector
//   SCREENTIME_MAX_BYTES ceiling for the reader
//   SCREENTIME_STOP      1 | 0, whether tripping the ceiling stops the producer
//   SCREENTIME_PRODUCER  sh -c script to run as the producer
//   SCREENTIME_DEADLINE  ms before the scene gives up on its own
ShellRoot {
  id: scene

  function env(name, fallback) {
    var value = Quickshell.env(name)
    if (value === undefined || value === null || String(value).length === 0) return fallback
    return String(value)
  }

  readonly property string readerKind: env("SCREENTIME_READER", "bounded")
  readonly property int maxBytes: parseInt(env("SCREENTIME_MAX_BYTES", "1048576"), 10)
  readonly property bool stopOnOverflow: env("SCREENTIME_STOP", "1") !== "0"
  readonly property string producerScript: env("SCREENTIME_PRODUCER", "printf hello")
  readonly property int deadlineMs: parseInt(env("SCREENTIME_DEADLINE", "60000"), 10)

  property bool reported: false
  // Set by the collector path only, mirroring exactly what be9b1f6 did: clamp
  // the finished result on its way out of the collector.
  property string collectorKept: ""

  function report(fields) {
    if (reported) return
    reported = true
    fields.reader = readerKind
    fields.maxBytes = maxBytes
    console.log("SCENE_RESULT " + JSON.stringify(fields))
    Qt.exit(0)
  }

  Item {
    BoundedReader {
      id: boundedReader
      producer: proc
      maxBytes: scene.maxBytes
      stopOnOverflow: scene.stopOnOverflow
    }

    StdioCollector {
      id: collector
      waitForEnd: true
      onStreamFinished: scene.collectorKept =
        text.length > scene.maxBytes ? text.slice(0, scene.maxBytes) : text
    }

    Process {
      id: proc
      running: true
      command: ["sh", "-c", scene.producerScript]
      stdout: scene.readerKind === "collector" ? collector : boundedReader
      onExited: function (exitCode, exitStatus) {
        var kept = scene.readerKind === "collector"
          ? scene.collectorKept
          : boundedReader.collected()
        scene.report({
          kept: kept.length,
          overflowed: scene.readerKind === "collector" ? null : boundedReader.overflowed,
          dropped: scene.readerKind === "collector" ? null : boundedReader.dropped,
          exitCode: exitCode,
          // QProcess::CrashExit, which is what death by signal reports. The
          // producer stopping this way is the reader having cut it off.
          crashed: exitStatus === 1,
          timedOut: false
        })
      }
    }

    // A producer that never ends must not hang the suite. Reaching this is a
    // failure for the bounded reader: it is supposed to stop the producer, and
    // stopping it is what makes `exited` fire.
    Timer {
      running: true
      interval: scene.deadlineMs
      repeat: false
      onTriggered: scene.report({
        kept: scene.readerKind === "collector"
          ? scene.collectorKept.length
          : boundedReader.collected().length,
        overflowed: scene.readerKind === "collector" ? null : boundedReader.overflowed,
        dropped: scene.readerKind === "collector" ? null : boundedReader.dropped,
        exitCode: -1,
        crashed: false,
        timedOut: true
      })
    }
  }
}
