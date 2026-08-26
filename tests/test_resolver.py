#!/usr/bin/env python3
"""Tests for bin/resolve-focus, the focused-window detail resolver."""

import importlib.machinery
import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "resolve-focus")

spec = importlib.util.spec_from_loader(
    "resolve_focus",
    importlib.machinery.SourceFileLoader("resolve_focus", SCRIPT),
)
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)

GHOSTTY = "com.mitchellh.ghostty"


class TestTerminalDetail(unittest.TestCase):
    def detail(self, title):
        return resolver.terminal_detail(title)

    def test_bare_program_name(self):
        self.assertEqual(self.detail("opencode"), "opencode")
        self.assertEqual(self.detail("kiro-cli"), "kiro-cli")
        self.assertEqual(self.detail("btop"), "btop")

    def test_trailing_segment_is_the_program(self):
        # Ghostty and most TUIs put "<session> - <program>" in the title.
        self.assertEqual(self.detail("Refactor the tracker - claude"), "claude")
        self.assertEqual(self.detail("ComfyUI + Flux Setup for Laptop - grok"), "grok")
        self.assertEqual(self.detail("a \u2014 opencode"), "opencode")

    def test_a_path_title_becomes_the_directory(self):
        self.assertEqual(self.detail("~/Projects/Screentime"), "Screentime")
        self.assertEqual(self.detail("~/temp"), "temp")
        self.assertEqual(self.detail("/etc/nginx"), "nginx")
        self.assertEqual(self.detail("~"), "~")
        self.assertEqual(self.detail("~/"), "~")

    def test_an_elided_path_still_becomes_the_directory(self):
        # Ghostty shortens long directories to "…/temp/ComfyUI/workflows",
        # which has no path prefix but is still a path. Without this the whole
        # elided path ended up as the row label.
        self.assertEqual(self.detail("\u2026/temp/ComfyUI/workflows"), "workflows")
        self.assertEqual(self.detail("\u2026/a/b"), "b")
        self.assertEqual(self.detail("\u2026"), "~")

    def test_looks_like_path_needs_a_single_token(self):
        self.assertTrue(resolver.looks_like_path("~/temp"))
        self.assertTrue(resolver.looks_like_path("\u2026/a/b"))
        self.assertTrue(resolver.looks_like_path("a/b"))
        # A sentence containing a slash is not a path.
        self.assertFalse(resolver.looks_like_path("read a/b and think"))
        self.assertFalse(resolver.looks_like_path(""))

    def test_a_command_line_reduces_to_the_command(self):
        self.assertEqual(self.detail("nvim README.md"), "nvim")
        self.assertEqual(self.detail("git log --oneline"), "git")

    def test_a_prose_title_is_not_mistaken_for_a_program(self):
        # A long multi-word trailing segment is a sentence, not a command.
        self.assertEqual(self.detail("some notes - and more prose here about things"), "")

    def test_empty_and_junk(self):
        self.assertEqual(self.detail(""), "")
        self.assertEqual(self.detail("   "), "")
        self.assertEqual(self.detail(None), "")

    def test_detail_is_length_bounded(self):
        self.assertLessEqual(len(self.detail("x" * 200)), resolver.MAX_DETAIL)

    def test_a_one_character_label_is_refused_as_noise(self):
        # A window briefly titled "a" must not earn a permanent row.
        self.assertEqual(self.detail("a"), "")
        self.assertEqual(self.detail("x"), "")
        # "~" is a real answer, not noise.
        self.assertEqual(self.detail("~"), "~")
        self.assertEqual(resolver.clean("a"), "")
        self.assertEqual(resolver.clean("~"), "~")
        self.assertEqual(resolver.clean("ab"), "ab")

    def test_a_short_directory_name_survives_the_noise_guard(self):
        # A directory really called "b" is a legitimate answer, unlike a bare
        # one-character window title.
        self.assertEqual(self.detail("\u2026/a/b"), "b")
        self.assertEqual(self.detail("~/b"), "b")
        self.assertEqual(resolver.clean("b", allow_short=True), "b")


class TestUninformative(unittest.TestCase):
    def test_a_title_repeating_the_app_name_is_useless(self):
        self.assertTrue(resolver.is_uninformative(GHOSTTY, GHOSTTY))
        self.assertTrue(resolver.is_uninformative("ghostty", GHOSTTY))
        self.assertTrue(resolver.is_uninformative("foot", "foot"))
        self.assertTrue(resolver.is_uninformative("Alacritty", "alacritty"))
        self.assertTrue(resolver.is_uninformative("", "foot"))
        self.assertTrue(resolver.is_uninformative("terminal", "foot"))
        self.assertTrue(resolver.is_uninformative("console", "foot"))

    def test_a_partial_app_name_is_also_useless(self):
        # Omarchy's floating terminal is class org.omarchy.terminal titled
        # "Omarchy"; that must not become a row called "Omarchy".
        self.assertTrue(resolver.is_uninformative("Omarchy", "org.omarchy.terminal"))
        self.assertTrue(resolver.is_uninformative("Ghostty", GHOSTTY))

    def test_a_real_title_is_informative(self):
        self.assertFalse(resolver.is_uninformative("opencode", GHOSTTY))
        self.assertFalse(resolver.is_uninformative("~/temp", GHOSTTY))
        self.assertFalse(resolver.is_uninformative("grok", GHOSTTY))
        self.assertFalse(resolver.is_uninformative("kiro-cli", "org.omarchy.terminal"))


class TestBrowserDetail(unittest.TestCase):
    def detail(self, title):
        return resolver.browser_detail(title)

    def test_youtube(self):
        self.assertEqual(self.detail("Never Gonna Give You Up - YouTube - Helium"), "YouTube")
        self.assertEqual(self.detail("(2) YouTube - Helium"), "YouTube")
        self.assertEqual(self.detail("Best of 2026 - YouTube \u2014 Mozilla Firefox"), "YouTube")

    def test_common_sites(self):
        self.assertEqual(self.detail("r/archlinux - Reddit - Helium"), "Reddit")
        self.assertEqual(self.detail("Inbox (12) - Gmail - Helium"), "Gmail")
        self.assertEqual(self.detail("How to fix this - Google Search - Helium"), "Google Search")
        self.assertEqual(
            self.detail("Model.js at main \u00b7 ricky/screentime \u00b7 GitHub - Helium"),
            "GitHub")

    def test_the_page_title_itself_is_never_the_answer(self):
        # This is the privacy property: what you searched for or watched is
        # dropped, only the site survives.
        for title, site in [
            ("how to treat a rash - Google Search - Helium", "Google Search"),
            ("Extremely Embarrassing Video - YouTube - Helium", "YouTube"),
        ]:
            detail = self.detail(title)
            self.assertEqual(detail, site)
            self.assertNotIn("rash", detail.lower())
            self.assertNotIn("embarrassing", detail.lower())

    def test_an_unknown_web_app_uses_its_short_trailing_segment(self):
        self.assertEqual(self.detail("*Unsaved Workflow - ComfyUI - Helium"), "ComfyUI")

    def test_a_long_trailing_fragment_is_refused(self):
        long_tail = "a much longer trailing fragment of prose that is not a site"
        self.assertEqual(self.detail(f"Headline - {long_tail} - Helium"), "")

    def test_an_unrecognisable_title_yields_no_detail(self):
        # No site marker and no trailing segment: this is counted as plain
        # browser time. Storing "Some Page Title" would be page content, which
        # is exactly what this resolver refuses to keep.
        self.assertEqual(self.detail("Some Page Title - Helium"), "")
        self.assertEqual(self.detail("Helium"), "")
        self.assertEqual(self.detail(""), "")

    def test_browser_suffix_stripping(self):
        self.assertEqual(resolver.strip_browser_suffix("Page - Helium"), "Page")
        self.assertEqual(resolver.strip_browser_suffix("Page \u2014 Mozilla Firefox"), "Page")
        self.assertEqual(resolver.strip_browser_suffix("Page - Google Chrome"), "Page")
        self.assertEqual(resolver.strip_browser_suffix("Page"), "Page")

    def test_detail_is_length_bounded(self):
        self.assertLessEqual(len(self.detail("a - " + "x" * 200)), resolver.MAX_DETAIL)


class TestResolve(unittest.TestCase):
    def test_terminals_resolve_their_program(self):
        detail, kind = resolver.resolve(GHOSTTY, "opencode")
        self.assertEqual(detail, "opencode")
        self.assertEqual(kind, "terminal")

    def test_browsers_resolve_their_site(self):
        detail, kind = resolver.resolve("helium", "clip - YouTube - Helium")
        self.assertEqual(detail, "YouTube")
        self.assertEqual(kind, "site")

    def test_other_apps_get_no_detail(self):
        self.assertEqual(resolver.resolve("Spotify", "Madvillain - All Caps"), ("", ""))
        self.assertEqual(resolver.resolve("org.gnome.Nautilus", "Pictures"), ("", ""))
        self.assertEqual(resolver.resolve("", ""), ("", ""))

    def test_detail_level_off_disables_everything(self):
        self.assertEqual(resolver.resolve(GHOSTTY, "opencode", "off"), ("", ""))
        self.assertEqual(resolver.resolve("helium", "x - YouTube - Helium", "off"), ("", ""))

    def test_detail_level_terminal_keeps_terminals_only(self):
        self.assertEqual(resolver.resolve(GHOSTTY, "opencode", "terminal"),
                         ("opencode", "terminal"))
        self.assertEqual(resolver.resolve("helium", "x - YouTube - Helium", "terminal"),
                         ("", ""))

    def test_an_uninformative_terminal_title_is_not_echoed_back(self):
        # Would otherwise produce a row literally named "com.mitchellh.ghostty".
        detail, _ = resolver.resolve(GHOSTTY, GHOSTTY)
        self.assertNotEqual(detail, GHOSTTY)

    def test_resolve_never_raises(self):
        for app, title in [(None, None), (123, 456), ("x" * 500, "y" * 500)]:
            resolver.resolve(str(app), str(title))


class TestCli(unittest.TestCase):
    def test_main_always_succeeds_and_prints_json(self):
        import io
        import json
        from contextlib import redirect_stdout

        for argv in ([], [GHOSTTY], [GHOSTTY, "opencode"],
                     ["helium", "a - YouTube - Helium", "full"],
                     [GHOSTTY, "opencode", "off"]):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = resolver.main(argv)
            self.assertEqual(code, 0, argv)
            payload = json.loads(buffer.getvalue())
            self.assertIn("app", payload)
            self.assertIn("detail", payload)
            self.assertIn("kind", payload)
            self.assertNotIn("/", payload["detail"])


class TestProcessHelpers(unittest.TestCase):
    def test_read_stat_on_our_own_process(self):
        stat = resolver.read_stat(os.getpid())
        self.assertIsNotNone(stat)
        comm, pgrp, tpgid = stat
        self.assertIsInstance(comm, str)
        self.assertIsInstance(pgrp, int)
        self.assertIsInstance(tpgid, int)

    def test_read_stat_on_a_missing_process(self):
        self.assertIsNone(resolver.read_stat(999999999))

    def test_children_and_descendants_are_safe_on_missing_pids(self):
        self.assertEqual(resolver.children_of(999999999), [])
        self.assertEqual(resolver.descendants(999999999), [])

    def test_cwd_name_of_our_own_process(self):
        self.assertTrue(resolver.cwd_name(os.getpid()))

    def test_process_detail_on_junk_pid(self):
        self.assertEqual(resolver.process_detail(0), "")
        self.assertEqual(resolver.process_detail(999999999), "")

    def test_cmdline_name_unwraps_the_interpreter(self):
        # Our own process is python running a script; the script wins.
        name = resolver.cmdline_name(os.getpid())
        self.assertNotIn(name.lower(), ("python", "python3"))


class TestPidForWindow(unittest.TestCase):
    """The resolver asks Hyprland which process owns the window it was given,
    over the compositor's control socket."""

    def ask(self, payload):
        runtime = tempfile.TemporaryDirectory()
        try:
            instance = os.path.join(runtime.name, "hypr", "testsig")
            os.makedirs(instance)
            path = os.path.join(instance, ".socket.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(1)

            def serve():
                try:
                    conn, _ = server.accept()
                    with conn:
                        conn.recv(256)
                        conn.sendall(payload)
                except OSError:
                    pass

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            env = {
                "HYPRLAND_INSTANCE_SIGNATURE": "testsig",
                "XDG_RUNTIME_DIR": runtime.name,
            }
            try:
                with mock.patch.dict(os.environ, env):
                    return resolver.pid_for_window(
                        GHOSTTY, "opencode")
            finally:
                server.close()
                thread.join(timeout=2)
        finally:
            runtime.cleanup()

    def test_the_window_owner_is_returned(self):
        clients = [{"class": GHOSTTY, "title": "opencode", "pid": 4242}]
        self.assertEqual(self.ask(json.dumps(clients).encode()), 4242)

    def test_no_matching_client_means_no_pid(self):
        clients = [{"class": "helium", "title": "other", "pid": 7}]
        self.assertEqual(self.ask(json.dumps(clients).encode()), 0)

    def test_garbage_json_reads_as_no_pid(self):
        self.assertEqual(self.ask(b"not json"), 0)

    def test_an_oversized_reply_is_refused_whole(self):
        # A reply past the ceiling is refused rather than parsed truncated:
        # half a client list could match the wrong window's pid.
        with mock.patch.object(resolver, "MAX_SOCKET_BYTES", 64):
            self.assertEqual(self.ask(b"x" * 8192), 0)


if __name__ == "__main__":
    unittest.main()
