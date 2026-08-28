.pragma library

// Bounded accumulation of a helper's output, one chunk at a time.
//
// The shell reads helper stdout/stderr as a stream (see lib/BoundedReader.qml)
// instead of letting a collector buffer the whole thing, so the ceiling has to
// be decided per chunk, while the producer is still running. That decision is
// pure arithmetic, so it lives here and is tested under plain node.
//
// "Bytes" here means UTF-16 code units, which is what a QML string is measured
// and stored in. The helpers emit ASCII JSON — python's json.dump escapes
// non-ASCII by default — so for them the two are the same number; for any other
// producer a ceiling of N still bounds the retained allocation at 2N.

function emptyBuffer() {
  return { text: "", bytes: 0, overflowed: false, dropped: 0 }
}

// The ceiling, normalised. 0 means retain nothing: valid, and what a stream
// whose content is deliberately discarded should use.
function ceiling(maxBytes) {
  var max = parseInt(maxBytes, 10)
  if (!isFinite(max) || max < 0) return 0
  return max
}

// Fold one chunk in, keeping at most `maxBytes` of text in total.
//
// Returns a new buffer rather than mutating, so the caller can compare before
// and after to see what the chunk changed. A chunk that runs past the ceiling
// is kept up to it and the remainder counted in `dropped`: a truncated prefix
// still fails JSON.parse into the normal error path, and for stderr it is the
// first few hundred characters that carry the message anyway.
//
// Chunks that arrive after the ceiling has been reached add nothing but
// `dropped`. That case is not hypothetical — the producer is asked to stop the
// moment it trips, but whatever it already wrote is still in flight.
function accept(buffer, chunk, maxBytes) {
  var limit = ceiling(maxBytes)
  var text = String(chunk === undefined || chunk === null ? "" : chunk)
  var out = {
    text: buffer.text,
    bytes: buffer.bytes,
    overflowed: buffer.overflowed === true,
    dropped: buffer.dropped || 0
  }
  if (text.length === 0) return out

  var room = limit - out.bytes
  if (room <= 0) {
    out.overflowed = true
    out.dropped += text.length
    return out
  }
  if (text.length <= room) {
    // Exactly filling the ceiling is not an overflow: nothing was lost. Only
    // the next chunk, if there is one, makes it one.
    out.text += text
    out.bytes += text.length
    return out
  }
  out.text += text.slice(0, room)
  out.bytes = limit
  out.overflowed = true
  out.dropped += text.length - room
  return out
}

// True when this chunk is the one that first ran past the ceiling — the point
// at which the producer is worth stopping.
function tripped(before, after) {
  return after.overflowed === true && before.overflowed !== true
}

// True when a producer has written yet more after already being asked to stop.
// SIGTERM has had a full event-loop round trip by then, so this is the signal
// to stop being polite about it.
function ignoredStop(before, after) {
  return before.overflowed === true && (after.dropped || 0) > (before.dropped || 0)
}

// Surfaced in the panel when a helper was cut off. Kept distinct from a parse
// failure on purpose: "sent too much" points at the helper, while "could not
// read the store" points at the data.
function overflowMessage(what) {
  return "Screen time helper sent too much " + String(what || "output")
}
