import assert from "node:assert/strict"
import { test } from "node:test"
import { loadQmlJs } from "./harness.mjs"

const Stream = loadQmlJs("lib/Stream.js")

// Feed a producer's output through the accumulator the way BoundedReader does,
// one chunk at a time, and report what the reader would have decided.
function drain(chunks, limit) {
  let buffer = Stream.emptyBuffer()
  let stops = 0
  let kills = 0
  for (const chunk of chunks) {
    const before = buffer
    buffer = Stream.accept(before, chunk, limit)
    if (Stream.tripped(before, buffer)) stops += 1
    else if (Stream.ignoredStop(before, buffer)) kills += 1
  }
  return { ...buffer, stops, kills }
}

test("an empty buffer holds nothing and has not overflowed", () => {
  const empty = Stream.emptyBuffer()
  assert.equal(empty.text, "")
  assert.equal(empty.bytes, 0)
  assert.equal(empty.overflowed, false)
  assert.equal(empty.dropped, 0)
})

test("output under the ceiling arrives intact", () => {
  const result = drain(["{\"ok\":", "true}"], 1024)
  assert.equal(result.text, "{\"ok\":true}")
  assert.equal(result.bytes, 11)
  assert.equal(result.overflowed, false)
  assert.equal(result.dropped, 0)
  assert.equal(result.stops, 0)
})

test("output exactly at the ceiling is not an overflow", () => {
  const result = drain(["abcde"], 5)
  assert.equal(result.text, "abcde")
  assert.equal(result.bytes, 5)
  assert.equal(result.overflowed, false)
  assert.equal(result.stops, 0)
})

test("the chunk that runs past the ceiling is kept up to it and no further", () => {
  const result = drain(["abcdefghij"], 4)
  assert.equal(result.text, "abcd")
  assert.equal(result.bytes, 4)
  assert.equal(result.overflowed, true)
  assert.equal(result.dropped, 6)
})

test("the ceiling holds across many chunks, not just one", () => {
  const chunks = Array.from({ length: 100 }, () => "x".repeat(1000))
  const result = drain(chunks, 2048)
  assert.equal(result.bytes, 2048)
  assert.equal(result.text.length, 2048)
  assert.equal(result.overflowed, true)
  // 100_000 produced, 2048 kept.
  assert.equal(result.dropped, 100000 - 2048)
})

test("the producer is asked to stop exactly once, on the chunk that trips", () => {
  const chunks = Array.from({ length: 50 }, () => "y".repeat(100))
  const result = drain(chunks, 250)
  assert.equal(result.stops, 1)
})

test("a producer that keeps writing after being stopped is killed, once", () => {
  const result = drain(["a".repeat(10), "b".repeat(10), "c".repeat(10)], 5)
  assert.equal(result.stops, 1)
  // Chunks two and three both count as ignoring the stop; BoundedReader latches
  // the kill so only the first turns into a signal.
  assert.equal(result.kills, 2)
  assert.equal(result.text, "aaaaa")
})

test("chunks arriving after the ceiling add nothing but dropped", () => {
  const first = Stream.accept(Stream.emptyBuffer(), "abcdef", 3)
  const second = Stream.accept(first, "ghijkl", 3)
  assert.equal(second.text, "abc")
  assert.equal(second.bytes, 3)
  assert.equal(second.dropped, 3 + 6)
  assert.equal(Stream.tripped(first, second), false, "already tripped, not again")
  assert.equal(Stream.ignoredStop(first, second), true)
})

test("a zero ceiling drains without retaining anything", () => {
  const result = drain(["noise", "more noise"], 0)
  assert.equal(result.text, "")
  assert.equal(result.bytes, 0)
  assert.equal(result.dropped, 15)
})

test("empty chunks change nothing", () => {
  const buffer = Stream.accept(Stream.emptyBuffer(), "", 10)
  assert.equal(buffer.text, "")
  assert.equal(buffer.overflowed, false)
  assert.equal(buffer.dropped, 0)
})

test("accept does not mutate the buffer it was given", () => {
  const before = Stream.accept(Stream.emptyBuffer(), "abc", 10)
  const after = Stream.accept(before, "def", 10)
  assert.equal(before.text, "abc", "the earlier buffer is untouched")
  assert.equal(before.bytes, 3)
  assert.equal(after.text, "abcdef")
})

test("a nonsense ceiling is treated as zero, never as unlimited", () => {
  for (const limit of [undefined, null, -1, NaN, "nope"]) {
    const result = drain(["anything at all"], limit)
    assert.equal(result.text, "", `limit ${String(limit)} retained text`)
    assert.equal(result.overflowed, true)
  }
})

test("a numeric string ceiling is honoured", () => {
  const result = drain(["abcdefghij"], "4")
  assert.equal(result.text, "abcd")
})

test("null and undefined chunks are ignored, not stringified", () => {
  let buffer = Stream.emptyBuffer()
  buffer = Stream.accept(buffer, null, 100)
  buffer = Stream.accept(buffer, undefined, 100)
  assert.equal(buffer.text, "")
  assert.equal(buffer.bytes, 0)
})

test("the overflow message names the stream and stays short", () => {
  const message = Stream.overflowMessage("output")
  assert.match(message, /too much output/)
  assert.ok(message.length < 80)
  assert.match(Stream.overflowMessage(), /too much output/)
})

test("a truncated JSON prefix fails to parse, which is the error path", () => {
  const snapshot = JSON.stringify({ ok: true, todayTotal: 1234 })
  const result = drain([snapshot], snapshot.length - 5)
  assert.equal(result.overflowed, true)
  assert.throws(() => JSON.parse(result.text), SyntaxError)
})
