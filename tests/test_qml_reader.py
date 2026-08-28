"""The read boundary between a helper process and the shell.

Everything else about the ceilings is arithmetic, and tests/stream.test.mjs
covers that. What cannot be checked without the real runtime is the thing the
security review was actually about: whether the *shell* allocates a producer's
entire output before any QML code gets a say in it.

StdioCollector does. Its parseBytes appends every chunk into one buffer and only
hands the result over from streamEnded, so a clamp applied to the finished text
bounds what is kept and not what was taken. These tests demonstrate that
difference in peak RSS, and that BoundedReader does not have it.

Each case runs a real quickshell process against a real producer over a real
pipe. Skipped when quickshell is not installed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENE = os.path.join(REPO, "tests", "qml", "reader-scene.qml")
QS = shutil.which("qs") or shutil.which("quickshell")

# Producers. `yes` is the cheapest way to fill a pipe faster than the shell can
# drain it, which is the interesting case.
LINE = "x" * 48
MB = 1024 * 1024
BIG_BYTES = 64 * MB
BIG = f"yes {LINE} | head -c {BIG_BYTES}"
ENDLESS = f"exec yes {LINE}"
# Ignores SIGTERM and never stops writing, so only SIGKILL ends it. Nothing the
# plugin ships behaves this way; the point is that the reader does not depend on
# the producer's cooperation.
STUBBORN = (
    f"exec {sys.executable} -c '\n"
    "import signal, sys\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    'buf = b"x" * 65536\n'
    "while True: sys.stdout.buffer.write(buf)\n"
    "'"
)

# Peak RSS of the shell with nothing to read is ~190 MB, most of it Qt. These
# are deliberately loose: the claim is a difference of hundreds of megabytes,
# not a precise figure.
BOUNDED_HEADROOM_MB = 32
COLLECTOR_FLOOR_MB = 96

# Runs qs and reports the peak RSS of that child alone. RUSAGE_CHILDREN is exact
# and race-free, unlike polling /proc for a process that is about to exit, and a
# fresh wrapper per run keeps one measurement out of the next.
WRAPPER = """
import resource, subprocess, sys
done = subprocess.run(sys.argv[1:], capture_output=True, text=True)
peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
sys.stdout.write("PEAK_RSS_KB %d\\n" % peak)
sys.stdout.write(done.stdout)
sys.stdout.write(done.stderr)
sys.exit(0)
"""


@unittest.skipUnless(QS, "quickshell is not installed")
class ReadBoundaryTest(unittest.TestCase):
    """Each case drives tests/qml/reader-scene.qml once."""

    @classmethod
    def setUpClass(cls):
        # quickshell refuses module imports that resolve outside the folder it
        # was pointed at, so the scene is assembled into a config folder shaped
        # the way the plugin is: the component beside its consumer, lib/ under
        # it.
        cls.root = tempfile.mkdtemp(prefix="screentime-qml-")
        shutil.copyfile(SCENE, os.path.join(cls.root, "shell.qml"))
        shutil.copyfile(
            os.path.join(REPO, "BoundedReader.qml"),
            os.path.join(cls.root, "BoundedReader.qml"),
        )
        os.mkdir(os.path.join(cls.root, "lib"))
        shutil.copyfile(
            os.path.join(REPO, "lib", "Stream.js"),
            os.path.join(cls.root, "lib", "Stream.js"),
        )
        cls.baseline_mb = cls.run_scene("printf hi")[1]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def run_scene(cls, producer, reader="bounded", max_bytes=MB, stop=True,
                  deadline_ms=30000):
        env = dict(os.environ)
        env.update({
            "QT_QPA_PLATFORM": "offscreen",
            "SCREENTIME_READER": reader,
            "SCREENTIME_MAX_BYTES": str(max_bytes),
            "SCREENTIME_STOP": "1" if stop else "0",
            "SCREENTIME_PRODUCER": producer,
            "SCREENTIME_DEADLINE": str(deadline_ms),
        })
        done = subprocess.run(
            [sys.executable, "-c", WRAPPER, QS, "--no-color", "-p",
             os.path.join(cls.root, "shell.qml")],
            capture_output=True, text=True, env=env,
            timeout=deadline_ms / 1000.0 + 90,
        )
        result = None
        peak_mb = 0.0
        for line in done.stdout.splitlines():
            if line.startswith("PEAK_RSS_KB"):
                peak_mb = int(line.split()[1]) / 1024.0
            marker = line.find("SCENE_RESULT ")
            if marker != -1:
                result = json.loads(line[marker + len("SCENE_RESULT "):])
        if result is None:
            raise AssertionError(
                "scene printed no result.\n" + done.stdout[-4000:] + done.stderr[-4000:]
            )
        return result, peak_mb

    # ---- the ceiling itself ------------------------------------------------

    def test_small_output_arrives_intact(self):
        reply = '{"ok":true}'
        result, _ = self.run_scene("printf '%s' '" + reply + "'")
        self.assertEqual(result["kept"], len(reply))
        self.assertFalse(result["overflowed"])
        self.assertEqual(result["dropped"], 0)
        self.assertEqual(result["exitCode"], 0)
        self.assertFalse(result["crashed"], "a producer under the ceiling is left alone")

    def test_output_exactly_at_the_ceiling_is_not_an_overflow(self):
        result, _ = self.run_scene(
            "head -c 4096 /dev/zero | tr '\\0' a", max_bytes=4096
        )
        self.assertEqual(result["kept"], 4096)
        self.assertFalse(result["overflowed"])
        self.assertEqual(result["exitCode"], 0)

    def test_oversized_output_is_cut_off_at_the_ceiling(self):
        result, _ = self.run_scene(BIG, max_bytes=MB)
        self.assertEqual(result["kept"], MB, "kept exactly the ceiling, no more")
        self.assertTrue(result["overflowed"])
        # The producer is stopped, so only what was already in flight is lost.
        # Well under the 64 MB it wanted to write is the whole point.
        self.assertLess(result["dropped"], 8 * MB)

    def test_an_oversized_producer_is_stopped_not_just_ignored(self):
        result, _ = self.run_scene(BIG, max_bytes=MB)
        self.assertTrue(
            result["crashed"],
            "the producer should die by signal, not run to completion",
        )
        self.assertNotEqual(result["exitCode"], 0)

    def test_an_endless_producer_is_stopped(self):
        # No `head` to end it: the process only exits because the reader kills
        # it. Reaching the scene's deadline instead means it never did.
        result, _ = self.run_scene(ENDLESS, max_bytes=MB, deadline_ms=20000)
        self.assertFalse(result["timedOut"], "endless producer was never stopped")
        self.assertTrue(result["crashed"])
        self.assertEqual(result["kept"], MB)

    def test_a_producer_that_ignores_sigterm_is_killed(self):
        # The escalation path. This producer ignores SIGTERM and never stops
        # writing, so `running = false` alone would leave it streaming into a
        # reader that has stopped listening -- the cost moved, not removed.
        result, peak_mb = self.run_scene(STUBBORN, max_bytes=MB, deadline_ms=20000)
        self.assertFalse(result["timedOut"], "the producer was never killed")
        self.assertTrue(result["crashed"])
        self.assertEqual(result["exitCode"], 9, "expected SIGKILL after SIGTERM was ignored")
        self.assertEqual(result["kept"], MB)
        self.assertLess(peak_mb - self.baseline_mb, BOUNDED_HEADROOM_MB)

    def test_a_zero_ceiling_drains_without_retaining_or_blocking(self):
        # How the resolver's and the sampler's stderr are read: the content is
        # deliberately discarded, but the pipe still has to be emptied or the
        # helper blocks mid-write and never exits.
        result, peak_mb = self.run_scene(BIG, max_bytes=0, stop=False)
        self.assertEqual(result["kept"], 0)
        self.assertEqual(result["dropped"], BIG_BYTES, "the whole stream was drained")
        self.assertEqual(result["exitCode"], 0, "the producer finished normally")
        self.assertFalse(result["crashed"])
        self.assertLess(peak_mb - self.baseline_mb, BOUNDED_HEADROOM_MB)

    # ---- the allocation boundary -------------------------------------------

    def test_bounded_reader_does_not_grow_with_producer_output(self):
        _, peak_mb = self.run_scene(BIG, max_bytes=MB)
        growth = peak_mb - self.baseline_mb
        self.assertLess(
            growth, BOUNDED_HEADROOM_MB,
            f"reading {BIG_BYTES // MB} MB grew the shell by {growth:.1f} MB",
        )

    def test_stdio_collector_grows_with_producer_output(self):
        """The regression this replaced, kept as a test so it cannot come back.

        The collector is used here exactly as it was at be9b1f6: waitForEnd, and
        the finished text clamped to the same ceiling. The clamp is honoured --
        `kept` is the ceiling -- and the shell still allocates for the whole 64
        MB, because it had already read all of it before the clamp could run.
        """
        result, peak_mb = self.run_scene(BIG, reader="collector", max_bytes=MB)
        self.assertEqual(result["kept"], MB, "the after-the-fact clamp does apply")
        self.assertEqual(
            result["exitCode"], 0,
            "and the producer was never stopped -- it wrote all 64 MB",
        )
        growth = peak_mb - self.baseline_mb
        self.assertGreater(
            growth, COLLECTOR_FLOOR_MB,
            f"expected the collector to hold the stream, but it grew {growth:.1f} MB",
        )

    def test_bounded_reader_costs_far_less_than_the_collector(self):
        _, bounded_mb = self.run_scene(BIG, max_bytes=MB)
        _, collector_mb = self.run_scene(BIG, reader="collector", max_bytes=MB)
        self.assertLess(
            bounded_mb + COLLECTOR_FLOOR_MB, collector_mb,
            f"bounded peaked at {bounded_mb:.1f} MB, collector at {collector_mb:.1f} MB",
        )


@unittest.skipUnless(QS, "quickshell is not installed")
class ServiceWiringTest(unittest.TestCase):
    """The same boundary, reached through the real Service.qml.

    The cases above prove BoundedReader works. These prove it is actually what
    the service reads its helper through, which is the part a refactor could
    quietly undo.
    """

    SERVICE_SCENE = os.path.join(REPO, "tests", "qml", "service-scene.qml")

    def build(self, helper=None):
        """A config folder holding the plugin, optionally with a fake helper."""
        root = tempfile.mkdtemp(prefix="screentime-service-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        shutil.copyfile(self.SERVICE_SCENE, os.path.join(root, "shell.qml"))
        for name in ("Service.qml", "BoundedReader.qml"):
            shutil.copyfile(os.path.join(REPO, name), os.path.join(root, name))
        shutil.copytree(os.path.join(REPO, "lib"), os.path.join(root, "lib"))
        shutil.copytree(os.path.join(REPO, "bin"), os.path.join(root, "bin"))
        if helper is not None:
            path = os.path.join(root, "bin", "screentime")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(helper)
            os.chmod(path, 0o755)
        return root

    def run_service(self, root, settle_ms=4000):
        data_home = tempfile.mkdtemp(prefix="screentime-data-")
        self.addCleanup(shutil.rmtree, data_home, ignore_errors=True)
        env = dict(os.environ)
        env.update({
            "QT_QPA_PLATFORM": "offscreen",
            "XDG_DATA_HOME": data_home,
            "SCREENTIME_SETTLE_MS": str(settle_ms),
        })
        done = subprocess.run(
            [sys.executable, "-c", WRAPPER, QS, "--no-color", "-p",
             os.path.join(root, "shell.qml")],
            capture_output=True, text=True, env=env, timeout=settle_ms / 1000.0 + 90,
        )
        result = None
        peak_mb = 0.0
        for line in done.stdout.splitlines():
            if line.startswith("PEAK_RSS_KB"):
                peak_mb = int(line.split()[1]) / 1024.0
            marker = line.find("SERVICE_RESULT ")
            if marker != -1:
                result = json.loads(line[marker + len("SERVICE_RESULT "):])
        if result is None:
            raise AssertionError(
                "service printed no result.\n" + done.stdout[-4000:] + done.stderr[-4000:]
            )
        return result, peak_mb

    def test_a_real_snapshot_still_round_trips(self):
        """The swap must not have cost the service its actual job."""
        result, _ = self.run_service(self.build())
        self.assertTrue(result["ok"], f"snapshot failed: {result['error']}")
        self.assertEqual(result["error"], "")
        self.assertTrue(result["hasStorePath"])
        # Six months of daily boxes, which is what the panel draws.
        self.assertGreater(result["weeks"], 20)

    def test_a_flooding_helper_becomes_an_error_not_an_allocation(self):
        flood = f"#!/bin/sh\nyes {LINE} | head -c {BIG_BYTES}\n"
        baseline, baseline_mb = self.run_service(self.build())
        self.assertTrue(baseline["ok"], "baseline snapshot should succeed")

        result, peak_mb = self.run_service(self.build(helper=flood))
        self.assertFalse(result["ok"], "a flood is not a valid snapshot")
        self.assertIn(
            "too much", result["error"],
            f"expected the overflow message, got {result['error']!r}",
        )
        growth = peak_mb - baseline_mb
        self.assertLess(
            growth, BOUNDED_HEADROOM_MB,
            f"a flooding helper grew the shell by {growth:.1f} MB",
        )

    def test_a_noisy_helper_is_capped_on_stderr_but_still_believed(self):
        """stderr is capped without being fatal.

        The ceiling is what bounds the allocation; killing a helper over noise
        on stderr would cost a committed batch for nothing. So a helper that
        writes far past the stderr ceiling and then prints a good reply is still
        read, and the shell still does not grow.
        """
        reply = '{"ok":true,"todayTotal":42,"storePath":"/tmp/screentime-test"}'
        noisy = (
            "#!/bin/sh\n"
            f"yes {LINE} | head -c {16 * MB} >&2\n"
            f"printf '%s' '{reply}'\n"
        )
        _, baseline_mb = self.run_service(self.build())
        result, peak_mb = self.run_service(self.build(helper=noisy), settle_ms=8000)
        self.assertTrue(
            result["ok"],
            f"a noisy but valid helper should be believed, got {result['error']!r}",
        )
        self.assertEqual(result["error"], "")
        growth = peak_mb - baseline_mb
        self.assertLess(
            growth, BOUNDED_HEADROOM_MB,
            f"16 MB of stderr grew the shell by {growth:.1f} MB",
        )


if __name__ == "__main__":
    unittest.main()
