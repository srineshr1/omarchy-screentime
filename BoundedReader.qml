import QtQuick
import Quickshell.Io
import "lib/Stream.js" as Stream

// A helper-output reader whose byte ceiling holds *while the producer is still
// running*.
//
// StdioCollector cannot do that, by construction. Its parseBytes appends every
// chunk into one buffer and it only hands the result to QML from streamEnded:
//
//     void StdioCollector::parseBytes(QByteArray& incoming, QByteArray& buffer) {
//       buffer.append(incoming);   // no ceiling, no owner, no way to refuse
//       ...
//     }
//
// So by the time any QML code can see the text — including code whose whole job
// is to clamp it — the entire output of the producer has already been allocated
// inside the shell. Clamping there bounds what is *kept*, not what was *taken*,
// and the allocation is the thing that matters for a process that lives as long
// as the session does.
//
// SplitParser with an empty splitMarker is the opposite. Its parseBytes emits
// each chunk as it arrives and leaves `buffer` untouched, so it retains nothing
// of its own between chunks. That turns the pipe into a plain stream, which is
// what lets this component hold a real ceiling: it counts as it goes, stops
// retaining at the limit, and cuts the producer off.
//
// What is still unavoidable is one chunk in flight — QProcess hands over
// whatever it has buffered when it wakes us, normally a pipe's worth. That is a
// transient the size of a read, not an accumulation the size of the output.
SplitParser {
  id: reader

  // The Process this is reading. Stopping the producer is half the point: a
  // reader that quietly stops accumulating while the producer keeps streaming
  // has moved the cost somewhere else, not removed it.
  property var producer: null

  // Ceiling on retained text. 0 retains nothing, which is the right setting for
  // a stream that is read only to keep the pipe from filling up.
  property int maxBytes: 64 * 1024

  // Whether tripping the ceiling should stop the producer. Off for streams
  // whose content is discarded anyway, where a chatty run is not a reason to
  // abandon the work the process was started to do.
  property bool stopOnOverflow: true

  // True once more arrived than the ceiling allows. Meaningless, and unread,
  // when maxBytes is 0.
  readonly property bool overflowed: _buffer.overflowed === true
  readonly property int dropped: _buffer.dropped || 0

  property var _buffer: Stream.emptyBuffer()
  property bool _killed: false

  // Every chunk, buffered nowhere. This is the whole reason for the component.
  splitMarker: ""

  // Read once the producer has exited. A function and not a bound property on
  // purpose: binding it would convert the entire accumulated string into a
  // QString again on every single chunk.
  function collected() {
    return _buffer.text
  }

  // Readers outlive the processes they read, so each run starts here.
  function reset() {
    _buffer = Stream.emptyBuffer()
    _killed = false
  }

  onRead: function (data) {
    var before = _buffer
    var after = Stream.accept(before, data, maxBytes)
    _buffer = after
    if (!stopOnOverflow) return
    if (Stream.tripped(before, after)) _stop()
    else if (Stream.ignoredStop(before, after)) _kill()
  }

  // terminate(), via running = false.
  function _stop() {
    if (!producer) return
    producer.running = false
  }

  // A producer that wrote more after SIGTERM is not going to stop for it.
  function _kill() {
    if (_killed || !producer) return
    _killed = true
    if (producer.running) producer.signal(9)
  }
}
