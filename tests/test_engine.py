#!/usr/bin/env python3
"""python3 -m unittest discover -s tests

bin/screentime has no .py suffix, so it is loaded by path.
"""

import contextlib
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "..", "bin", "screentime")

spec = importlib.util.spec_from_loader(
    "screentime_engine",
    importlib.machinery.SourceFileLoader("screentime_engine", HELPER),
)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class StoreTestCase(unittest.TestCase):
    """Every test gets its own XDG_DATA_HOME so the real store is untouched."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp.name
        self.today = date(2026, 8, 24)

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.previous
        self.tmp.cleanup()

    def store_with(self, days):
        return {"version": 1, "days": {
            key: {"total": sum(apps.values()), "apps": dict(apps)}
            for key, apps in days.items()
        }}


class TestFormatting(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(engine.format_duration(0), "0m")
        self.assertEqual(engine.format_duration(60), "1m")
        self.assertEqual(engine.format_duration(3600), "1h")
        self.assertEqual(engine.format_duration(16200), "4h 30m")
        self.assertEqual(engine.format_duration(-10), "0m")
        self.assertEqual(engine.format_duration("16200"), "4h 30m")
        self.assertEqual(engine.format_duration(None), "0m")

    def test_is_day_key(self):
        self.assertTrue(engine.is_day_key("2026-08-24"))
        self.assertFalse(engine.is_day_key("2026-8-24"))
        self.assertFalse(engine.is_day_key("2026-02-30"))
        self.assertFalse(engine.is_day_key("total"))
        self.assertFalse(engine.is_day_key(None))
        self.assertFalse(engine.is_day_key(20260824))

    def test_to_int(self):
        self.assertEqual(engine.to_int("12"), 12)
        self.assertEqual(engine.to_int(12.9), 12)
        self.assertEqual(engine.to_int("nope"), 0)
        self.assertEqual(engine.to_int(None), 0)

    def test_pretty_name_falls_back_without_a_desktop_entry(self):
        self.assertEqual(engine.pretty_name("firefox"), "Firefox")
        self.assertEqual(engine.pretty_name("com.example.NoSuchAppXyz"), "NoSuchAppXyz")
        self.assertEqual(engine.pretty_name("some_random_thing_xyz"), "Some random thing xyz")
        self.assertEqual(engine.pretty_name(""), "Unknown")
        self.assertEqual(engine.pretty_name(None), "Unknown")

    def test_level_for_buckets_against_the_goal(self):
        goal = 6 * 3600
        self.assertEqual(engine.level_for(0, goal), "NONE")
        self.assertEqual(engine.level_for(60, goal), "L1")
        self.assertEqual(engine.level_for(int(goal * 0.3), goal), "L2")
        self.assertEqual(engine.level_for(int(goal * 0.6), goal), "L3")
        self.assertEqual(engine.level_for(int(goal * 0.8), goal), "L4")
        self.assertEqual(engine.level_for(goal, goal), "L4")
        self.assertEqual(engine.level_for(goal + 1, goal), "OVER")
        # A zero goal must not divide by zero.
        self.assertEqual(engine.level_for(100, 0), "OVER")


class TestCommit(StoreTestCase):
    def test_merge_adds_and_accumulates(self):
        store = {"version": 1, "days": {}}
        added = engine.merge_commit(store, {"days": {"2026-08-24": {"firefox": 600}}})
        self.assertEqual(added, 600)
        self.assertEqual(store["days"]["2026-08-24"]["total"], 600)

        # A second commit for the same day accumulates rather than replacing.
        engine.merge_commit(store, {"days": {"2026-08-24": {"firefox": 300, "code": 120}}})
        self.assertEqual(store["days"]["2026-08-24"]["apps"], {"firefox": 900, "code": 120})
        self.assertEqual(store["days"]["2026-08-24"]["total"], 1020)

    def test_merge_accepts_a_json_string(self):
        store = {"version": 1, "days": {}}
        added = engine.merge_commit(store, json.dumps({"days": {"2026-08-24": {"x": 5}}}))
        self.assertEqual(added, 5)

    def test_merge_accepts_a_bare_day_map(self):
        store = {"version": 1, "days": {}}
        self.assertEqual(engine.merge_commit(store, {"2026-08-24": {"x": 7}}), 7)

    def test_merge_rejects_junk(self):
        store = {"version": 1, "days": {}}
        self.assertEqual(engine.merge_commit(store, "not json"), 0)
        self.assertEqual(engine.merge_commit(store, None), 0)
        self.assertEqual(engine.merge_commit(store, 42), 0)
        self.assertEqual(engine.merge_commit(store, {"days": {"bad-key": {"x": 5}}}), 0)
        self.assertEqual(engine.merge_commit(store, {"days": {"2026-08-24": "nope"}}), 0)
        self.assertEqual(store["days"], {})

    def test_merge_rejects_impossible_durations(self):
        store = {"version": 1, "days": {}}
        # More than a day of one app in a single batch is a stuck clock.
        self.assertEqual(engine.merge_commit(store, {"days": {"2026-08-24": {"x": 90000}}}), 0)
        self.assertEqual(engine.merge_commit(store, {"days": {"2026-08-24": {"x": -50}}}), 0)
        self.assertEqual(store["days"], {})

    def test_total_never_falls_below_the_breakdown(self):
        store = {"version": 1, "days": {"2026-08-24": {"total": 0, "apps": {}}}}
        engine.merge_commit(store, {"days": {"2026-08-24": {"a": 100, "b": 50}}})
        self.assertEqual(store["days"]["2026-08-24"]["total"], 150)

    def test_round_trip_through_disk_is_atomic_and_lossless(self):
        store = self.store_with({"2026-08-24": {"firefox": 600}})
        engine.save_store(store)
        self.assertTrue(os.path.isfile(engine.store_path()))
        reloaded = engine.load_store()
        self.assertEqual(reloaded["days"]["2026-08-24"]["total"], 600)
        # No temp files left behind.
        leftovers = [n for n in os.listdir(os.path.dirname(engine.store_path()))
                     if n.startswith(".history-")]
        self.assertEqual(leftovers, [])

    def test_load_store_survives_a_corrupt_file(self):
        os.makedirs(os.path.dirname(engine.store_path()), exist_ok=True)
        with open(engine.store_path(), "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        self.assertEqual(engine.load_store(), {"version": 1, "days": {}})

    def test_load_store_discards_bad_entries(self):
        os.makedirs(os.path.dirname(engine.store_path()), exist_ok=True)
        with open(engine.store_path(), "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "days": {
                "2026-08-24": {"total": 100, "apps": {"a": 100}},
                "garbage": {"total": 5, "apps": {}},
                "2026-08-25": "nope",
            }}, handle)
        loaded = engine.load_store()
        self.assertEqual(sorted(loaded["days"].keys()), ["2026-08-24"])


class TestPrune(StoreTestCase):
    def test_prune_drops_detail_but_keeps_totals(self):
        old = self.today - timedelta(days=200)
        recent = self.today - timedelta(days=5)
        store = self.store_with({
            old.isoformat(): {"firefox": 3600},
            recent.isoformat(): {"firefox": 1800},
        })
        dropped = engine.prune(store, 120, self.today)
        self.assertEqual(dropped, 1)
        self.assertEqual(store["days"][old.isoformat()]["apps"], {})
        self.assertEqual(store["days"][old.isoformat()]["total"], 3600)
        self.assertEqual(store["days"][recent.isoformat()]["apps"], {"firefox": 1800})

    def test_prune_is_idempotent(self):
        old = (self.today - timedelta(days=200)).isoformat()
        store = self.store_with({old: {"firefox": 3600}})
        self.assertEqual(engine.prune(store, 120, self.today), 1)
        self.assertEqual(engine.prune(store, 120, self.today), 0)


class TestWindow(StoreTestCase):
    def test_month_start_and_end(self):
        self.assertEqual(engine.month_start(date(2026, 8, 24)), date(2026, 8, 1))
        self.assertEqual(engine.month_start(date(2026, 8, 24), -5), date(2026, 3, 1))
        self.assertEqual(engine.month_start(date(2026, 1, 15), -1), date(2025, 12, 1))
        self.assertEqual(engine.month_start(date(2026, 12, 1), 1), date(2027, 1, 1))
        self.assertEqual(engine.month_end(date(2026, 2, 10)), date(2026, 2, 28))
        self.assertEqual(engine.month_end(date(2024, 2, 10)), date(2024, 2, 29))

    def test_default_window_is_six_whole_months_ending_today(self):
        start, end = engine.window_bounds(6, 0, self.today)
        self.assertEqual(start, date(2026, 3, 1))
        self.assertEqual(end, self.today)

    def test_window_never_runs_past_today(self):
        _, end = engine.window_bounds(12, 0, self.today)
        self.assertEqual(end, self.today)

    def test_offset_shifts_the_window_back_by_whole_months(self):
        start, end = engine.window_bounds(6, 1, self.today)
        self.assertEqual(start, date(2026, 2, 1))
        self.assertEqual(end, date(2026, 7, 31))

    def test_offset_crosses_a_year_boundary(self):
        start, end = engine.window_bounds(6, 6, self.today)
        self.assertEqual(start, date(2025, 9, 1))
        self.assertEqual(end, date(2026, 2, 28))

    def test_window_clamps_junk_input(self):
        self.assertEqual(engine.window_bounds(0, 0, self.today),
                         engine.window_bounds(6, 0, self.today))
        self.assertEqual(engine.window_bounds(-4, -4, self.today),
                         engine.window_bounds(6, 0, self.today))
        start, _ = engine.window_bounds(999, 0, self.today)
        self.assertEqual(start, engine.month_start(self.today, -23))

    def test_six_months_is_far_fewer_columns_than_a_year(self):
        start, end = engine.window_bounds(6, 0, self.today)
        window = engine.build_grid({"days": {}}, start, end, 21600, True)
        year = engine.build_weeks({"days": {}}, 2026, 21600, True, self.today)
        self.assertLess(len(window), len(year))
        # A popup can show this many columns without clipping the newest one.
        self.assertLessEqual(len(window), 28)

    def test_range_label_reads_naturally(self):
        self.assertEqual(engine.range_label(date(2026, 3, 1), date(2026, 8, 24)),
                         "Mar \u2013 Aug 2026")
        self.assertEqual(engine.range_label(date(2025, 9, 1), date(2026, 2, 28)),
                         "Sep 2025 \u2013 Feb 2026")

    def test_grid_covers_exactly_the_requested_range(self):
        start, end = engine.window_bounds(6, 0, self.today)
        weeks = engine.build_grid({"days": {}}, start, end, 21600, True)
        real = [d for w in weeks for d in w["days"] if not d["outOfRange"]]
        self.assertEqual(real[0]["date"], start.isoformat())
        self.assertEqual(real[-1]["date"], end.isoformat())
        self.assertTrue(all(len(w["days"]) == 7 for w in weeks))

    def test_grid_is_empty_when_the_range_is_inverted(self):
        self.assertEqual(
            engine.build_grid({"days": {}}, date(2026, 5, 1), date(2026, 4, 1), 21600, True),
            [],
        )


class TestWeeks(StoreTestCase):
    def test_grid_is_seven_rows_and_starts_on_the_chosen_weekday(self):
        store = self.store_with({"2026-08-24": {"firefox": 3600}})
        weeks = engine.build_weeks(store, 2026, 6 * 3600, True, self.today)
        self.assertTrue(all(len(week["days"]) == 7 for week in weeks))
        # 2026-01-01 is a Thursday, so a Monday-first grid pads three cells.
        first = weeks[0]["days"]
        self.assertTrue(first[0]["outOfRange"])
        self.assertTrue(first[1]["outOfRange"])
        self.assertTrue(first[2]["outOfRange"])
        self.assertEqual(first[3]["date"], "2026-01-01")

    def test_sunday_first_shifts_the_padding(self):
        store = {"version": 1, "days": {}}
        weeks = engine.build_weeks(store, 2026, 6 * 3600, False, self.today)
        # Sunday-first: Thursday 1 Jan lands on row 4.
        self.assertEqual(weeks[0]["days"][4]["date"], "2026-01-01")

    def test_current_year_stops_at_today(self):
        store = {"version": 1, "days": {}}
        weeks = engine.build_weeks(store, 2026, 6 * 3600, True, self.today)
        real = [d for w in weeks for d in w["days"] if not d["outOfRange"]]
        self.assertEqual(real[-1]["date"], "2026-08-24")

    def test_past_year_covers_the_whole_year(self):
        store = {"version": 1, "days": {}}
        weeks = engine.build_weeks(store, 2025, 6 * 3600, True, self.today)
        real = [d for w in weeks for d in w["days"] if not d["outOfRange"]]
        self.assertEqual(real[0]["date"], "2025-01-01")
        self.assertEqual(real[-1]["date"], "2025-12-31")
        self.assertEqual(len(real), 365)

    def test_leap_year(self):
        store = {"version": 1, "days": {}}
        weeks = engine.build_weeks(store, 2024, 6 * 3600, True, self.today)
        real = [d for w in weeks for d in w["days"] if not d["outOfRange"]]
        self.assertEqual(len(real), 366)

    def test_cells_carry_seconds_and_level(self):
        store = self.store_with({"2026-08-24": {"firefox": 6 * 3600 + 60}})
        weeks = engine.build_weeks(store, 2026, 6 * 3600, True, self.today)
        cell = next(d for w in weeks for d in w["days"] if d["date"] == "2026-08-24")
        self.assertEqual(cell["seconds"], 6 * 3600 + 60)
        self.assertEqual(cell["level"], "OVER")
        self.assertEqual(cell["label"], "6h 1m")


class TestSnapshot(StoreTestCase):
    def build(self, days, **kwargs):
        store = self.store_with(days)
        return engine.build_snapshot(
            store,
            kwargs.get("goal", 6),
            kwargs.get("monday", True),
            kwargs.get("year"),
            self.today,
            kwargs.get("selected"),
            kwargs.get("months", 6),
            kwargs.get("offset", 0),
        )

    def test_today_totals_and_app_shares(self):
        snap = self.build({"2026-08-24": {"firefox": 5400, "ghostty": 10800}})
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["todayTotal"], 16200)
        self.assertEqual(snap["todayLabel"], "4h 30m")
        self.assertEqual(snap["todayApps"][0]["id"], "ghostty")
        self.assertAlmostEqual(snap["todayApps"][0]["share"], 10800 / 16200, places=3)
        self.assertEqual(sum(a["seconds"] for a in snap["todayApps"]), 16200)

    def test_apps_are_sorted_descending_and_ties_are_stable(self):
        snap = self.build({"2026-08-24": {"b": 100, "a": 100, "c": 300}})
        self.assertEqual([a["id"] for a in snap["todayApps"]], ["c", "a", "b"])

    def test_empty_store_is_still_a_valid_snapshot(self):
        snap = self.build({})
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["todayTotal"], 0)
        self.assertEqual(snap["todayApps"], [])
        self.assertEqual(len(snap["weekDays"]), 7)
        self.assertEqual(snap["availableYears"], [2026])
        self.assertTrue(len(snap["weeks"]) > 0)

    def test_week_window_is_seven_days_ending_today(self):
        snap = self.build({
            "2026-08-24": {"a": 3600},
            "2026-08-18": {"a": 60},
            "2026-08-17": {"a": 999},  # eight days ago, outside the window
        })
        self.assertEqual(len(snap["weekDays"]), 7)
        self.assertEqual(snap["weekDays"][0]["date"], "2026-08-18")
        self.assertEqual(snap["weekDays"][-1]["date"], "2026-08-24")
        self.assertEqual(snap["weekTotal"], 3660)

    def test_over_goal_flag(self):
        self.assertFalse(self.build({"2026-08-24": {"a": 3600}})["overGoal"])
        self.assertTrue(self.build({"2026-08-24": {"a": 25000}})["overGoal"])

    def test_streak_counts_consecutive_days_under_the_limit(self):
        snap = self.build({
            "2026-08-24": {"a": 3600},
            "2026-08-23": {"a": 3600},
            "2026-08-22": {"a": 3600},
            "2026-08-21": {"a": 25000},  # over the limit, breaks the streak
            "2026-08-20": {"a": 3600},
        })
        self.assertEqual(snap["streak"], 3)

    def test_streak_ignores_a_day_that_has_not_started(self):
        # Nothing recorded today yet, so the streak is measured from yesterday.
        snap = self.build({"2026-08-23": {"a": 3600}, "2026-08-22": {"a": 3600}})
        self.assertEqual(snap["streak"], 2)

    def test_streak_stops_at_an_untracked_gap(self):
        snap = self.build({"2026-08-24": {"a": 3600}, "2026-08-22": {"a": 3600}})
        self.assertEqual(snap["streak"], 1)

    def test_available_years_include_history_and_today(self):
        snap = self.build({"2024-05-05": {"a": 60}, "2026-08-24": {"a": 60}})
        self.assertEqual(snap["availableYears"], [2024, 2026])

    def test_snapshot_reports_the_window_range(self):
        snap = self.build({"2026-08-24": {"a": 3600}, "2026-04-10": {"a": 1800}})
        self.assertEqual(snap["rangeStart"], "2026-03-01")
        self.assertEqual(snap["rangeEnd"], "2026-08-24")
        self.assertEqual(snap["rangeLabel"], "Mar \u2013 Aug 2026")
        self.assertEqual(snap["months"], 6)
        self.assertEqual(snap["offset"], 0)
        self.assertEqual(snap["rangeTotal"], 5400)
        self.assertEqual(snap["rangeDaysTracked"], 2)
        self.assertEqual(snap["rangeDailyAverage"], 2700)

    def test_range_total_excludes_days_outside_the_window(self):
        snap = self.build({"2026-08-24": {"a": 3600}, "2025-11-02": {"a": 99999}})
        self.assertEqual(snap["rangeTotal"], 3600)
        self.assertEqual(snap["allTimeTotal"], 103599)

    def test_can_go_back_only_when_older_data_exists(self):
        self.assertFalse(self.build({"2026-08-24": {"a": 60}})["canGoBack"])
        self.assertTrue(self.build({
            "2026-08-24": {"a": 60},
            "2025-11-02": {"a": 60},
        })["canGoBack"])

    def test_can_go_forward_only_when_shifted_back(self):
        self.assertFalse(self.build({}, offset=0)["canGoForward"])
        self.assertTrue(self.build({}, offset=3)["canGoForward"])

    def test_offset_moves_the_window_and_its_totals(self):
        days = {"2026-08-24": {"a": 3600}, "2026-01-15": {"a": 1800}}
        current = self.build(days)
        self.assertEqual(current["rangeTotal"], 3600)
        shifted = self.build(days, offset=6)
        self.assertEqual(shifted["rangeStart"], "2025-09-01")
        self.assertEqual(shifted["rangeEnd"], "2026-02-28")
        self.assertEqual(shifted["rangeTotal"], 1800)

    def test_months_setting_widens_the_window(self):
        snap = self.build({}, months=3)
        self.assertEqual(snap["rangeStart"], "2026-06-01")
        self.assertEqual(snap["months"], 3)
        wide = self.build({}, months=12)
        self.assertEqual(wide["rangeStart"], "2025-09-01")

    def test_grid_column_count_stays_panel_sized(self):
        # The whole point of the window: a full year is ~53 columns and clips.
        self.assertLessEqual(len(self.build({}, months=6)["weeks"]), 28)
        self.assertLessEqual(len(self.build({}, months=4)["weeks"]), 20)

    def test_selected_day_returns_that_days_breakdown(self):
        snap = self.build(
            {"2026-08-20": {"firefox": 1800, "code": 600}, "2026-08-24": {"a": 60}},
            selected="2026-08-20",
        )
        self.assertEqual(snap["selectedDay"], "2026-08-20")
        self.assertEqual(snap["selectedTotal"], 2400)
        self.assertEqual([a["id"] for a in snap["selectedApps"]], ["firefox", "code"])
        self.assertFalse(snap["selectedDetailExpired"])

    def test_selected_day_flags_expired_detail(self):
        store = {"version": 1, "days": {"2026-01-05": {"total": 3600, "apps": {}}}}
        snap = engine.build_snapshot(store, 6, True, None, self.today, "2026-01-05")
        self.assertEqual(snap["selectedTotal"], 3600)
        self.assertEqual(snap["selectedApps"], [])
        self.assertTrue(snap["selectedDetailExpired"])

    def test_selected_day_ignores_a_bad_key(self):
        snap = self.build({"2026-08-24": {"a": 60}}, selected="not-a-date")
        self.assertEqual(snap["selectedDay"], "")

    def test_best_day_and_all_time(self):
        snap = self.build({
            "2026-08-24": {"a": 3600},
            "2026-08-23": {"a": 18000},
            "2026-08-22": {"a": 600},
        })
        self.assertEqual(snap["bestDay"]["date"], "2026-08-23")
        self.assertEqual(snap["bestDay"]["label"], "5h")
        self.assertEqual(snap["allTimeTotal"], 22200)
        self.assertEqual(snap["daysTracked"], 3)

    def test_top_apps_roll_up_the_last_thirty_days(self):
        snap = self.build({
            "2026-08-24": {"firefox": 600},
            "2026-08-10": {"firefox": 1200, "code": 300},
            "2026-06-01": {"firefox": 99999},  # older than 30 days, excluded
        })
        top = {a["id"]: a["seconds"] for a in snap["topApps"]}
        self.assertEqual(top["firefox"], 1800)
        self.assertEqual(top["code"], 300)

    def test_snapshot_is_json_serialisable(self):
        snap = self.build({"2026-08-24": {"firefox": 600}})
        json.dumps(snap)  # must not raise


class TestStoreLocation(StoreTestCase):
    def test_store_is_not_inside_the_omarchy_install_dir(self):
        """Regression: <data>/omarchy is a symlink to the root-owned
        /usr/share/omarchy on a real Omarchy install, so a store under that
        prefix fails with EACCES for every user."""
        path = engine.store_path()
        self.assertTrue(path.startswith(self.tmp.name), path)
        relative = os.path.relpath(path, self.tmp.name)
        self.assertEqual(relative.split(os.sep)[0], "omarchy-screentime")

    def test_store_honours_xdg_data_home(self):
        self.assertTrue(engine.store_path().startswith(self.tmp.name))

    def test_store_falls_back_to_home_when_xdg_is_unset(self):
        os.environ.pop("XDG_DATA_HOME", None)
        expected = os.path.join(os.path.expanduser("~"), ".local", "share",
                                "omarchy-screentime", "history.json")
        self.assertEqual(engine.store_path(), expected)

    def test_writing_creates_the_directory_tree(self):
        engine.save_store({"version": 1, "days": {}})
        self.assertTrue(os.path.isfile(engine.store_path()))


class TestCompositeKeys(StoreTestCase):
    def test_split_key(self):
        self.assertEqual(engine.split_key("firefox"), ("firefox", ""))
        self.assertEqual(engine.split_key("com.mitchellh.ghostty/opencode"),
                         ("com.mitchellh.ghostty", "opencode"))
        self.assertEqual(engine.split_key("helium/YouTube"), ("helium", "YouTube"))
        # Only the first slash is the boundary.
        self.assertEqual(engine.split_key("helium/a/b"), ("helium", "a/b"))
        self.assertEqual(engine.split_key(""), ("", ""))

    def test_app_list_puts_the_detail_first_and_the_host_second(self):
        rows = engine.app_list({"com.mitchellh.ghostty/opencode": 1200}, 1200)
        self.assertEqual(rows[0]["name"], "opencode")
        self.assertEqual(rows[0]["host"], "Ghostty")
        self.assertEqual(rows[0]["detail"], "opencode")
        self.assertEqual(rows[0]["app"], "com.mitchellh.ghostty")
        self.assertEqual(rows[0]["label"], "20m")

    def test_app_list_without_detail_has_no_host(self):
        rows = engine.app_list({"firefox": 600}, 600)
        self.assertEqual(rows[0]["name"], "Firefox")
        self.assertEqual(rows[0]["host"], "")
        self.assertEqual(rows[0]["detail"], "")

    def test_merge_by_app_rolls_detail_back_up(self):
        apps = {
            "com.mitchellh.ghostty/opencode": 1200,
            "com.mitchellh.ghostty/claude": 600,
            "helium/YouTube": 5400,
            "helium/GitHub": 300,
            "org.gnome.Nautilus": 60,
        }
        self.assertEqual(engine.merge_by_app(apps), {
            "com.mitchellh.ghostty": 1800,
            "helium": 5700,
            "org.gnome.Nautilus": 60,
        })

    def test_merge_by_app_is_lossless(self):
        apps = {"a/x": 10, "a/y": 20, "b": 30}
        self.assertEqual(sum(engine.merge_by_app(apps).values()), 60)

    def test_shares_are_computed_against_the_day_total(self):
        rows = engine.app_list({"helium/YouTube": 5400, "helium/GitHub": 1800}, 7200)
        self.assertAlmostEqual(rows[0]["share"], 0.75, places=3)
        self.assertAlmostEqual(rows[1]["share"], 0.25, places=3)

    def test_commit_accepts_composite_keys(self):
        store = {"version": 1, "days": {}}
        added = engine.merge_commit(store, {"days": {"2026-08-24": {
            "com.mitchellh.ghostty/opencode": 1200,
            "helium/YouTube": 5400,
        }}})
        self.assertEqual(added, 6600)
        self.assertEqual(store["days"]["2026-08-24"]["total"], 6600)
        self.assertIn("helium/YouTube", store["days"]["2026-08-24"]["apps"])

    def test_snapshot_exposes_both_readings(self):
        store = self.store_with({"2026-08-24": {
            "com.mitchellh.ghostty/opencode": 1200,
            "com.mitchellh.ghostty/claude": 600,
            "helium/YouTube": 5400,
        }})
        snap = engine.build_snapshot(store, 6, True, None, self.today, None, 6, 0)
        detailed = {r["name"]: r["seconds"] for r in snap["todayApps"]}
        self.assertEqual(detailed["YouTube"], 5400)
        self.assertEqual(detailed["opencode"], 1200)
        self.assertEqual(detailed["claude"], 600)
        grouped = {r["name"]: r["seconds"] for r in snap["todayByApp"]}
        self.assertEqual(grouped["Ghostty"], 1800)
        self.assertEqual(grouped["Helium"], 5400)
        # Both views must add up to the same day total.
        self.assertEqual(sum(r["seconds"] for r in snap["todayApps"]),
                         sum(r["seconds"] for r in snap["todayByApp"]))

    def test_selected_day_also_has_both_readings(self):
        store = self.store_with({"2026-08-20": {"helium/YouTube": 3600}})
        snap = engine.build_snapshot(store, 6, True, None, self.today,
                                     "2026-08-20", 6, 0)
        self.assertEqual(snap["selectedApps"][0]["name"], "YouTube")
        self.assertEqual(snap["selectedByApp"][0]["name"], "Helium")

    def test_daily_totals_are_unaffected_by_detail(self):
        store = self.store_with({"2026-08-24": {"a/x": 100, "a/y": 200}})
        snap = engine.build_snapshot(store, 6, True, None, self.today, None, 6, 0)
        self.assertEqual(snap["todayTotal"], 300)
        cell = next(d for w in snap["weeks"] for d in w["days"]
                    if d["date"] == "2026-08-24")
        self.assertEqual(cell["seconds"], 300)


class TestAppTree(StoreTestCase):
    APPS = {
        "com.mitchellh.ghostty/opencode": 1200,
        "com.mitchellh.ghostty/claude": 600,
        "com.mitchellh.ghostty": 200,
        "helium/YouTube": 5400,
        "org.gnome.Nautilus": 60,
    }
    TOTAL = 7460

    def tree(self, apps=None, total=None, limit=0):
        return engine.app_tree(
            self.APPS if apps is None else apps,
            self.TOTAL if total is None else total,
            limit,
        )

    def test_one_node_per_app_sorted_by_size(self):
        names = [n["name"] for n in self.tree()]
        self.assertEqual(names, ["Helium", "Ghostty", "Files"])

    def test_detail_becomes_children(self):
        ghostty = next(n for n in self.tree() if n["name"] == "Ghostty")
        self.assertEqual([c["name"] for c in ghostty["children"]],
                         ["opencode", "claude", "other"])
        self.assertEqual(ghostty["seconds"], 2000)

    def test_an_app_with_no_detail_is_a_leaf(self):
        files = next(n for n in self.tree() if n["name"] == "Files")
        self.assertEqual(files["children"], [])

    def test_plain_time_alongside_detail_becomes_an_other_child(self):
        ghostty = next(n for n in self.tree() if n["name"] == "Ghostty")
        other = next(c for c in ghostty["children"] if c["name"] == "other")
        self.assertEqual(other["seconds"], 200)

    def test_no_other_child_when_detail_accounts_for_everything(self):
        tree = self.tree({"helium/YouTube": 600}, 600)
        self.assertEqual([c["name"] for c in tree[0]["children"]], ["YouTube"])

    def test_no_other_child_for_a_leaf_app(self):
        tree = self.tree({"firefox": 600}, 600)
        self.assertEqual(tree[0]["children"], [])

    def test_children_always_account_for_their_parent(self):
        for node in self.tree():
            if node["children"]:
                self.assertEqual(sum(c["seconds"] for c in node["children"]),
                                 node["seconds"], node["name"])

    def test_the_tree_accounts_for_the_whole_day(self):
        self.assertEqual(sum(n["seconds"] for n in self.tree()), self.TOTAL)

    def test_shares_are_against_the_day_so_children_nest_visually(self):
        ghostty = next(n for n in self.tree() if n["name"] == "Ghostty")
        self.assertAlmostEqual(ghostty["share"], 2000 / self.TOTAL, places=3)
        opencode = ghostty["children"][0]
        self.assertAlmostEqual(opencode["share"], 1200 / self.TOTAL, places=3)
        # parentShare is the slice of the folder, for reference.
        self.assertAlmostEqual(opencode["parentShare"], 1200 / 2000, places=3)
        self.assertLess(opencode["share"], ghostty["share"])

    def test_child_ids_are_the_store_keys(self):
        ghostty = next(n for n in self.tree() if n["name"] == "Ghostty")
        self.assertEqual(ghostty["id"], "com.mitchellh.ghostty")
        self.assertEqual(ghostty["children"][0]["id"],
                         "com.mitchellh.ghostty/opencode")

    def test_children_carry_their_host_for_display(self):
        ghostty = next(n for n in self.tree() if n["name"] == "Ghostty")
        self.assertEqual(ghostty["children"][0]["host"], "Ghostty")

    def test_limit_truncates_folders_not_children(self):
        tree = self.tree(limit=1)
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["name"], "Helium")

    def test_empty_input(self):
        self.assertEqual(engine.app_tree({}, 0), [])

    def test_zero_total_does_not_divide_by_zero(self):
        tree = engine.app_tree({"a/x": 10}, 0)
        self.assertTrue(tree[0]["share"] >= 0)

    def test_snapshot_exposes_the_tree(self):
        store = self.store_with({"2026-08-24": dict(self.APPS)})
        snap = engine.build_snapshot(store, 6, True, None, self.today, None, 6, 0)
        self.assertEqual(sum(n["seconds"] for n in snap["todayTree"]),
                         snap["todayTotal"])
        helium = next(n for n in snap["todayTree"] if n["name"] == "Helium")
        self.assertEqual(helium["children"][0]["name"], "YouTube")

    def test_selected_day_exposes_its_own_tree(self):
        store = self.store_with({"2026-08-20": {"helium/YouTube": 3600}})
        snap = engine.build_snapshot(store, 6, True, None, self.today,
                                     "2026-08-20", 6, 0)
        self.assertEqual(snap["selectedTree"][0]["name"], "Helium")
        self.assertEqual(snap["selectedTree"][0]["children"][0]["name"], "YouTube")


class TestSnapshotContract(StoreTestCase):
    """The JS side asserts it preserves every field listed in the fixture.

    This asserts the fixture still matches what the engine actually emits, so
    adding a field here without regenerating the fixture fails loudly instead of
    the field being silently dropped on its way to the panel.
    """

    FIXTURE = os.path.join(HERE, "fixtures", "snapshot-keys.json")

    def test_fixture_matches_the_engine_output(self):
        store = self.store_with({
            self.today.isoformat(): {"com.mitchellh.ghostty/opencode": 60},
        })
        snap = engine.build_snapshot(store, 6, True, None, self.today,
                                     self.today.isoformat(), 6, 0)
        with open(self.FIXTURE, "r", encoding="utf-8") as handle:
            recorded = set(json.load(handle))
        actual = set(snap.keys())
        missing = sorted(actual - recorded)
        stale = sorted(recorded - actual)
        self.assertEqual(missing, [], f"regenerate {self.FIXTURE}: new keys {missing}")
        self.assertEqual(stale, [], f"regenerate {self.FIXTURE}: removed keys {stale}")

    def test_commit_adds_only_the_committed_keys(self):
        store = {"version": 1, "days": {}}
        snap = engine.build_snapshot(store, 6, True, None, self.today, None, 6, 0)
        snap["committed"] = 0
        snap["committedNet"] = [0, 0]
        with open(self.FIXTURE, "r", encoding="utf-8") as handle:
            recorded = set(json.load(handle))
        self.assertEqual(set(snap.keys()) - recorded, {"committed", "committedNet"})


class TestCli(StoreTestCase):
    def run_cli(self, argv):
        # The CLI prints to stdout by design; swallow it so the test log stays
        # readable and assert on the store and exit code instead.
        with contextlib.redirect_stdout(io.StringIO()):
            return engine.main(argv)

    def test_commit_then_snapshot_via_the_cli(self):
        real_today = date.today().isoformat()
        payload = json.dumps({"days": {real_today: {"firefox": 600}}})
        self.assertEqual(self.run_cli(["commit", payload]), 0)
        store = engine.load_store()
        self.assertEqual(store["days"][real_today]["total"], 600)

    def test_commit_reads_the_payload_from_stdin(self):
        real_today = date.today().isoformat()
        payload = json.dumps({"days": {real_today: {"firefox": 120}}})
        stdin = sys.stdin
        sys.stdin = io.StringIO(payload)
        try:
            self.assertEqual(self.run_cli(["commit", "-"]), 0)
        finally:
            sys.stdin = stdin
        self.assertEqual(engine.load_store()["days"][real_today]["total"], 120)

    def test_commit_with_junk_payload_exits_clean(self):
        self.assertEqual(self.run_cli(["commit", "garbage"]), 0)

    def test_snapshot_on_an_empty_store_exits_clean(self):
        self.assertEqual(self.run_cli(["snapshot"]), 0)

    def test_human_commands_exit_clean(self):
        for argv in (["today"], ["week"], ["year"], ["json"], ["prune"], ["path"]):
            self.assertEqual(self.run_cli(argv), 0, argv)

    def test_default_command_is_today(self):
        self.assertEqual(self.run_cli([]), 0)

    def test_service_argv_shape_is_accepted(self):
        """Regression: argparse binds parent-parser options only *before* the
        subcommand. Service.qml builds argv in exactly this order, and getting
        it wrong made every commit fail with "unrecognized arguments"."""
        real_today = date.today().isoformat()
        payload = json.dumps({"days": {real_today: {"firefox": 300}}})
        argv = ["--goal", "6", "--retention", "120", "--months", "6",
                "--offset", "0", "--monday", "commit", payload, "--prune"]
        self.assertEqual(self.run_cli(argv), 0)
        self.assertEqual(engine.load_store()["days"][real_today]["total"], 300)

    def test_service_snapshot_argv_shape_is_accepted(self):
        argv = ["--goal", "8", "--retention", "90", "--months", "4",
                "--offset", "2", "--sunday", "--day", "2026-08-20", "snapshot"]
        self.assertEqual(self.run_cli(argv), 0)

    def test_flags_after_the_subcommand_are_rejected(self):
        """The failure mode this guards against, asserted directly."""
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                engine.build_parser().parse_args(
                    ["commit", "{}", "--goal", "6"]
                )

    def test_net_commands_exit_clean(self):
        # --day is a parent-parser option, so it goes before the subcommand.
        for argv in (["net"], ["--day", "2026-08-20", "net"]):
            self.assertEqual(self.run_cli(argv), 0, argv)

    def test_netsample_exits_clean_without_a_compositor(self):
        """`ss` may be missing and Hyprland may not be running; neither is a
        reason for the bar to see a failure."""
        with mock.patch.object(engine, "run_ss", return_value=""):
            with mock.patch.object(engine, "window_apps", return_value={}):
                self.assertEqual(self.run_cli(["netsample"]), 0)

    def test_commit_drains_what_netsample_buffered(self):
        real_today = date.today().isoformat()
        engine.save_net_state({
            "sockets": {},
            "pending": {real_today: {"helium": [4000, 500]}},
            "started": True,
        })
        payload = json.dumps({"days": {real_today: {"helium": 60}}})
        self.assertEqual(self.run_cli(["commit", payload]), 0)
        entry = engine.load_store()["days"][real_today]
        self.assertEqual(entry["net"]["helium"], [4000, 500])
        self.assertEqual(entry["netTotal"], [4000, 500])
        # Drained, so the next commit cannot bill the same bytes twice.
        self.assertEqual(engine.load_net_state()["pending"], {})

    def test_commit_with_nothing_accrued_still_drains_bytes(self):
        """The service commits on a timer whether or not anything was focused,
        because data moves while the seat is idle."""
        real_today = date.today().isoformat()
        engine.save_net_state({
            "sockets": {},
            "pending": {real_today: {"steam": [900, 100]}},
            "started": True,
        })
        self.assertEqual(self.run_cli(["commit", json.dumps({"days": {}})]), 0)
        entry = engine.load_store()["days"][real_today]
        self.assertEqual(entry["net"]["steam"], [900, 100])
        self.assertEqual(entry["total"], 0)


class TestSteamNames(unittest.TestCase):
    """steam_app_1091500 is not a name anyone recognises."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = os.path.join(self.tmp.name, "Steam", "steamapps")
        os.makedirs(self.library)
        engine._STEAM_CACHE.clear()
        engine._STEAM_LIBRARIES = [self.library]

    def tearDown(self):
        engine._STEAM_CACHE.clear()
        engine._STEAM_LIBRARIES = None
        self.tmp.cleanup()

    def write_manifest(self, appid, name, extra=""):
        body = (
            '"AppState"\n{\n'
            f'\t"appid"\t\t"{appid}"\n'
            f'\t"name"\t\t"{name}"\n'
            f'{extra}'
            "}\n"
        )
        with open(os.path.join(self.library, f"appmanifest_{appid}.acf"),
                  "w", encoding="utf-8") as handle:
            handle.write(body)

    def test_installed_game_resolves_to_its_name(self):
        self.write_manifest("1091500", "Cyberpunk 2077")
        self.assertEqual(engine.pretty_name("steam_app_1091500"), "Cyberpunk 2077")

    def test_nested_name_keys_do_not_win(self):
        """A manifest's UserConfig block has its own "name"; the top-level one
        is the game."""
        self.write_manifest(
            "3321460", "Crimson Desert",
            '\t"UserConfig"\n\t{\n\t\t"name"\t\t"not the game"\n\t}\n',
        )
        self.assertEqual(engine.pretty_name("steam_app_3321460"), "Crimson Desert")

    def test_uninstalled_game_keeps_a_readable_fallback(self):
        self.assertEqual(engine.pretty_name("steam_app_999999"), "Steam app 999999")

    def test_steam_itself_is_untouched(self):
        self.assertEqual(engine.pretty_name("steam"), "Steam")

    def test_a_name_with_whitespace_is_collapsed(self):
        self.write_manifest("42", "Half   Life")
        self.assertEqual(engine.pretty_name("steam_app_42"), "Half Life")

    def test_lookup_is_cached_after_the_first_miss(self):
        self.assertEqual(engine.steam_app_name("777"), None)
        self.write_manifest("777", "Late Arrival")
        # Deliberate: a per-process cache is what keeps a snapshot from stat-ing
        # the library once per row.
        self.assertEqual(engine.steam_app_name("777"), None)

    def test_non_numeric_ids_are_refused(self):
        self.assertIsNone(engine.steam_app_name("default"))
        self.assertIsNone(engine.steam_app_name(""))

    def test_libraries_are_discovered_from_libraryfolders(self):
        engine._STEAM_LIBRARIES = None
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        root = os.path.join(home.name, ".local", "share", "Steam")
        os.makedirs(os.path.join(root, "steamapps"))
        os.makedirs(os.path.join(root, "config"))
        second = os.path.join(home.name, "games", "SteamLibrary")
        os.makedirs(os.path.join(second, "steamapps"))
        with open(os.path.join(root, "config", "libraryfolders.vdf"),
                  "w", encoding="utf-8") as handle:
            handle.write('"libraryfolders"\n{\n\t"0"\n\t{\n'
                         f'\t\t"path"\t\t"{second}"\n\t}}\n}}\n')
        with mock.patch.object(os.path, "expanduser", return_value=home.name):
            libraries = engine.steam_libraries()
        self.assertIn(os.path.realpath(os.path.join(root, "steamapps")), libraries)
        self.assertIn(os.path.realpath(os.path.join(second, "steamapps")), libraries)


SS_SAMPLE = """\
0      0      192.168.1.6:47864  172.217.70.132:443 users:(("helium",pid=53301,fd=27)) uid:1000 ino:91822 sk:101b
\t cubic wscale:8,10 rto:260 bytes_sent:1829 bytes_acked:1830 bytes_received:11913 segs_out:11
0      0      192.168.1.6:42300         8.8.8.8:853 uid:974 ino:219307 sk:3003
\t cubic wscale:8,10 bytes_sent:2061 bytes_received:2079 segs_out:7
0      0        127.0.0.1:35414        127.0.0.1:6379 users:(("redis",pid=99,fd=8)) uid:1000 ino:5555
\t cubic bytes_sent:900000 bytes_received:900000
0      0      192.168.1.6:50148   57.144.123.32:443 users:(("node",pid=1837,fd=24)) uid:1000 ino:0
\t cubic bytes_sent:20367 bytes_received:87682
"""


class TestSsParsing(unittest.TestCase):
    def test_socket_rows_carry_pid_and_counters(self):
        rows = engine.parse_ss(SS_SAMPLE)
        by_pid = {row["pid"]: row for row in rows}
        self.assertEqual(by_pid[53301]["down"], 11913)
        self.assertEqual(by_pid[53301]["up"], 1829)

    def test_loopback_is_not_data_usage(self):
        rows = engine.parse_ss(SS_SAMPLE)
        self.assertNotIn(900000, [row["down"] for row in rows])

    def test_sockets_without_an_inode_are_skipped(self):
        """No inode means no identity to diff against next sample, and diffing
        anonymous sockets would recount the same bytes on every tick."""
        rows = engine.parse_ss(SS_SAMPLE)
        self.assertNotIn(1837, [row["pid"] for row in rows])

    def test_a_socket_owned_by_another_uid_has_no_pid(self):
        rows = engine.parse_ss(SS_SAMPLE)
        orphan = [row for row in rows if row["pid"] == 0]
        self.assertEqual(len(orphan), 1)
        self.assertEqual(orphan[0]["down"], 2079)

    def test_a_leading_state_column_is_tolerated(self):
        """`ss` prints State first unless a state filter drops it."""
        text = ("ESTAB  0      0      192.168.1.6:1  1.1.1.1:443 "
                "users:((\"curl\",pid=7,fd=3)) ino:12\n"
                "\t cubic bytes_sent:10 bytes_received:20\n")
        rows = engine.parse_ss(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], 7)
        self.assertEqual(rows[0]["down"], 20)

    def test_endpoints_are_part_of_the_socket_key(self):
        """Inodes get recycled, so a reused one on a new connection must read as
        a new socket rather than a delta against a stranger's counters."""
        rows = engine.parse_ss(SS_SAMPLE)
        self.assertTrue(all(row["key"].count("|") == 2 for row in rows))

    def test_empty_input_is_not_an_error(self):
        self.assertEqual(engine.parse_ss(""), [])
        self.assertEqual(engine.parse_ss(None), [])


class TestNetDelta(unittest.TestCase):
    def rows(self, *specs):
        return [{"key": key, "pid": pid, "down": down, "up": up}
                for key, pid, down, up in specs]

    def test_first_sample_only_establishes_a_baseline(self):
        """Those counters describe traffic from before the plugin was watching."""
        sockets, apps = engine.net_delta(
            {}, self.rows(("a", 10, 5000, 100)), {10: "helium"}, True)
        self.assertEqual(apps, {})
        self.assertEqual(sockets["a"], [5000, 100])

    def test_a_new_socket_contributes_its_whole_counter(self):
        sockets, apps = engine.net_delta(
            {}, self.rows(("a", 10, 5000, 100)), {10: "helium"}, False)
        self.assertEqual(apps["helium"], [5000, 100])

    def test_only_the_difference_since_last_sample_counts(self):
        _, apps = engine.net_delta(
            {"a": [1000, 50]}, self.rows(("a", 10, 5000, 100)), {10: "helium"}, False)
        self.assertEqual(apps["helium"], [4000, 50])

    def test_a_counter_that_went_backwards_is_dropped(self):
        """A recycled inode, not lost traffic."""
        _, apps = engine.net_delta(
            {"a": [9000, 900]}, self.rows(("a", 10, 10, 5)), {10: "helium"}, False)
        self.assertEqual(apps, {})

    def test_an_implausible_jump_is_dropped(self):
        _, apps = engine.net_delta(
            {"a": [0, 0]},
            self.rows(("a", 10, engine.MAX_SOCKET_DELTA + 1, 10)),
            {10: "helium"}, False)
        self.assertEqual(apps["helium"], [0, 10])

    def test_sockets_of_one_app_are_summed(self):
        _, apps = engine.net_delta(
            {}, self.rows(("a", 10, 100, 1), ("b", 10, 200, 2)),
            {10: "helium"}, False)
        self.assertEqual(apps["helium"], [300, 3])

    def test_traffic_with_no_owning_process_lands_in_one_bucket(self):
        _, apps = engine.net_delta(
            {}, self.rows(("a", 0, 100, 1)), {}, False)
        self.assertEqual(apps, {"system": [100, 1]})

    def test_closed_sockets_are_forgotten(self):
        sockets, _ = engine.net_delta(
            {"gone": [1, 1]}, self.rows(("a", 10, 5, 5)), {10: "x"}, False)
        self.assertNotIn("gone", sockets)


class TestAppForPid(unittest.TestCase):
    def test_a_window_pid_names_itself(self):
        self.assertEqual(engine.app_for_pid(42, {42: "helium"}, {}), "helium")

    def test_a_child_process_inherits_the_window_above_it(self):
        """A browser's network process, a game's helper and Steam's web helper
        are all children of the process that owns the window."""
        tree = {5: 4, 4: 3, 3: 42}
        with mock.patch.object(engine, "proc_parent", side_effect=lambda p: tree.get(p, 0)):
            self.assertEqual(engine.app_for_pid(5, {42: "helium"}, {}), "helium")

    def test_a_process_with_no_window_falls_back_to_its_own_name(self):
        with mock.patch.object(engine, "proc_parent", return_value=1):
            with mock.patch.object(engine, "proc_comm", return_value="syncthing"):
                self.assertEqual(engine.app_for_pid(9, {}, {}), "syncthing")

    def test_an_unreadable_process_still_gets_a_bucket(self):
        with mock.patch.object(engine, "proc_parent", return_value=0):
            with mock.patch.object(engine, "proc_comm", return_value=""):
                self.assertEqual(engine.app_for_pid(9, {}, {}), "system")

    def test_a_cycle_in_the_tree_cannot_hang_the_walk(self):
        with mock.patch.object(engine, "proc_parent", side_effect=lambda p: 7 if p == 8 else 8):
            with mock.patch.object(engine, "proc_comm", return_value="loop"):
                self.assertEqual(engine.app_for_pid(8, {}, {}), "loop")


class TestNetStore(StoreTestCase):
    def test_merge_accumulates_per_app_and_per_day(self):
        store = {"version": 1, "days": {}}
        engine.merge_net(store, {"2026-08-24": {"helium": [100, 10]}})
        engine.merge_net(store, {"2026-08-24": {"helium": [50, 5], "steam": [7, 0]}})
        entry = store["days"]["2026-08-24"]
        self.assertEqual(entry["net"]["helium"], [150, 15])
        self.assertEqual(entry["net"]["steam"], [7, 0])
        self.assertEqual(entry["netTotal"], [157, 15])

    def test_merge_returns_what_it_added(self):
        store = {"version": 1, "days": {}}
        added = engine.merge_net(store, {"2026-08-24": {"helium": [100, 10]}})
        self.assertEqual(added, [100, 10])

    def test_a_day_of_only_rejected_bytes_leaves_no_entry(self):
        store = {"version": 1, "days": {}}
        engine.merge_net(store, {"not-a-day": {"helium": [1, 1]}})
        engine.merge_net(store, {"2026-08-24": {"helium": [0, 0]}})
        self.assertEqual(store["days"], {})

    def test_an_absurd_batch_is_refused(self):
        store = {"version": 1, "days": {}}
        engine.merge_net(store,
                         {"2026-08-24": {"helium": [engine.MAX_BATCH_BYTES + 1, 0]}})
        self.assertEqual(store["days"], {})

    def test_merge_does_not_invent_screen_time(self):
        store = {"version": 1, "days": {}}
        engine.merge_net(store, {"2026-08-24": {"helium": [100, 10]}})
        self.assertEqual(store["days"]["2026-08-24"]["total"], 0)

    def test_pruning_keeps_the_day_total_and_drops_the_breakdown(self):
        store = {"version": 1, "days": {}}
        old = (self.today - timedelta(days=200)).isoformat()
        engine.merge_net(store, {old: {"helium": [5000, 500]}})
        engine.prune(store, 120, self.today)
        entry = store["days"][old]
        self.assertEqual(entry["net"], {})
        self.assertEqual(entry["netTotal"], [5000, 500])

    def test_bytes_added_after_pruning_still_add_up(self):
        """Regression: recomputing netTotal from the per-app map would reset it
        to the new batch every time detail aged out."""
        store = {"version": 1, "days": {}}
        old = (self.today - timedelta(days=200)).isoformat()
        engine.merge_net(store, {old: {"helium": [5000, 500]}})
        engine.prune(store, 120, self.today)
        engine.merge_net(store, {old: {"helium": [1000, 100]}})
        self.assertEqual(store["days"][old]["netTotal"], [6000, 600])

    def test_load_store_survives_a_hand_edited_net_block(self):
        engine.save_store({"version": 1, "days": {"2026-08-24": {
            "total": 60, "apps": {"helium": 60},
            "net": {"helium": "nonsense", "steam": [-5, 3], "gone": [0, 0]},
            "netTotal": None,
        }}})
        entry = engine.load_store()["days"]["2026-08-24"]
        self.assertNotIn("helium", entry.get("net", {}))
        self.assertNotIn("gone", entry.get("net", {}))
        self.assertEqual(entry["net"]["steam"], [0, 3])
        self.assertEqual(entry["netTotal"], [0, 3])

    def test_net_state_round_trips(self):
        engine.save_net_state({
            "sockets": {"a": [1, 2]},
            "pending": {"2026-08-24": {"helium": [3, 4]}, "bad-day": {"x": [1, 1]}},
            "started": True,
        })
        state = engine.load_net_state()
        self.assertEqual(state["sockets"], {"a": [1, 2]})
        self.assertEqual(state["pending"], {"2026-08-24": {"helium": [3, 4]}})
        self.assertTrue(state["started"])

    def test_a_missing_net_state_reads_as_a_first_run(self):
        state = engine.load_net_state()
        self.assertFalse(state["started"])
        self.assertEqual(state["sockets"], {})

    def test_sample_net_buffers_into_the_pending_day(self):
        state = {"sockets": {}, "pending": {}, "started": True}
        rows = [{"key": "a", "pid": 10, "down": 400, "up": 40}]
        added = engine.sample_net(state, "2026-08-24", rows, {10: "helium"})
        self.assertEqual(added, [400, 40])
        self.assertEqual(state["pending"]["2026-08-24"]["helium"], [400, 40])
        self.assertEqual(state["sockets"]["a"], [400, 40])

    def test_the_very_first_sample_buffers_nothing(self):
        state = {"sockets": {}, "pending": {}, "started": False}
        rows = [{"key": "a", "pid": 10, "down": 400, "up": 40}]
        self.assertEqual(engine.sample_net(state, "2026-08-24", rows, {10: "helium"}),
                         [0, 0])
        self.assertEqual(state["pending"], {})
        self.assertTrue(state["started"])

    def test_drain_moves_the_buffer_into_the_store_once(self):
        engine.save_net_state({
            "sockets": {}, "started": True,
            "pending": {"2026-08-24": {"helium": [800, 80]}},
        })
        store = {"version": 1, "days": {}}
        self.assertEqual(engine.drain_net(store), [800, 80])
        self.assertEqual(engine.drain_net(store), [0, 0])
        self.assertEqual(store["days"]["2026-08-24"]["net"]["helium"], [800, 80])


class TestNetSnapshot(StoreTestCase):
    def snapshot_with_net(self, seconds, net):
        key = self.today.isoformat()
        store = {"version": 1, "days": {key: {
            "total": sum(seconds.values()), "apps": dict(seconds),
        }}}
        engine.merge_net(store, {key: net})
        return engine.build_snapshot(store, 6, True, None, self.today, key, 6, 0)

    def test_day_totals_are_reported_both_ways(self):
        snap = self.snapshot_with_net({"helium": 600}, {"helium": [2_000_000, 250_000]})
        self.assertEqual(snap["todayNet"]["down"], 2_000_000)
        self.assertEqual(snap["todayNet"]["downLabel"], "2.0 MB")
        self.assertEqual(snap["todayNet"]["upLabel"], "250 kB")
        self.assertEqual(snap["selectedNet"]["down"], 2_000_000)

    def test_a_folder_row_carries_its_own_bytes(self):
        snap = self.snapshot_with_net({"helium/YouTube": 600}, {"helium": [900, 90]})
        row = snap["todayTree"][0]
        self.assertEqual(row["app"], "helium")
        self.assertTrue(row["netKnown"])
        self.assertEqual(row["net"]["down"], 900)

    def test_detail_rows_claim_no_bytes_of_their_own(self):
        """Which tab downloaded what is not something the socket table can
        answer, and repeating the app's total on each child would read as if it
        had been counted several times."""
        snap = self.snapshot_with_net({"helium/YouTube": 600}, {"helium": [900, 90]})
        child = snap["todayTree"][0]["children"][0]
        self.assertEqual(child["name"], "YouTube")
        self.assertFalse(child["netKnown"])
        self.assertEqual(child["net"]["down"], 0)

    def test_an_app_that_only_moved_data_still_gets_a_row(self):
        """A download that finished while the screen was locked is exactly the
        thing you open this panel to find."""
        snap = self.snapshot_with_net({"helium": 600}, {"syncthing": [5000, 5000]})
        names = [row["app"] for row in snap["todayTree"]]
        self.assertIn("syncthing", names)
        row = [r for r in snap["todayTree"] if r["app"] == "syncthing"][0]
        self.assertEqual(row["seconds"], 0)
        self.assertEqual(row["label"], "0m")

    def test_apps_with_time_still_sort_above_apps_with_only_data(self):
        snap = self.snapshot_with_net({"helium": 60}, {"syncthing": [10 ** 9, 0]})
        self.assertEqual(snap["todayTree"][0]["app"], "helium")

    def test_grouped_rows_carry_bytes_too(self):
        snap = self.snapshot_with_net({"helium/YouTube": 600}, {"helium": [900, 90]})
        row = snap["todayByApp"][0]
        self.assertEqual(row["app"], "helium")
        self.assertTrue(row["netKnown"])

    def test_week_and_all_time_roll_up(self):
        snap = self.snapshot_with_net({"helium": 600}, {"helium": [1500, 150]})
        self.assertEqual(snap["weekNet"]["down"], 1500)
        self.assertEqual(snap["allTimeNet"]["down"], 1500)
        self.assertEqual(snap["rangeNet"]["down"], 1500)

    def test_net_tracked_is_false_until_something_is_measured(self):
        snap = engine.build_snapshot(
            self.store_with({self.today.isoformat(): {"helium": 60}}),
            6, True, None, self.today, None, 6, 0)
        self.assertFalse(snap["netTracked"])
        self.assertEqual(snap["todayNet"]["down"], 0)

    def test_a_day_with_only_bytes_reaches_the_all_time_total(self):
        """Days are only "tracked" when they have seconds on them; bytes have to
        be counted from every day in the store instead."""
        store = {"version": 1, "days": {}}
        engine.merge_net(store, {(self.today - timedelta(days=1)).isoformat():
                                 {"syncthing": [7000, 0]}})
        snap = engine.build_snapshot(store, 6, True, None, self.today, None, 6, 0)
        self.assertEqual(snap["allTimeNet"]["down"], 7000)
        self.assertTrue(snap["netTracked"])

    def test_week_rows_carry_their_own_day_totals(self):
        snap = self.snapshot_with_net({"helium": 600}, {"helium": [1500, 150]})
        latest = snap["weekDays"][-1]
        self.assertEqual(latest["net"]["down"], 1500)


class TestResidualTraffic(unittest.TestCase):
    """The kernel keeps no byte counters on UDP sockets, so QUIC would vanish."""

    def test_the_gap_between_the_wire_and_the_sockets_is_kept(self):
        self.assertEqual(
            engine.residual_traffic([1000, 100], [9000, 300], [2000, 50]),
            [6000, 150])

    def test_traffic_fully_accounted_for_leaves_nothing_over(self):
        self.assertEqual(
            engine.residual_traffic([0, 0], [500, 50], [500, 50]), [0, 0])

    def test_more_attributed_than_the_wire_saw_is_not_negative(self):
        self.assertEqual(
            engine.residual_traffic([0, 0], [100, 10], [900, 90]), [0, 0])

    def test_a_reboot_or_a_link_bounce_is_not_traffic(self):
        self.assertEqual(
            engine.residual_traffic([9000, 900], [10, 1], [0, 0]), [0, 0])

    def test_an_implausible_jump_is_refused(self):
        self.assertEqual(
            engine.residual_traffic([0, 0],
                                    [engine.MAX_SOCKET_DELTA + 1, 0], [0, 0]),
            [0, 0])

    def test_junk_readings_are_zero(self):
        self.assertEqual(engine.residual_traffic(None, None, None), [0, 0])

    def test_only_real_devices_are_summed(self):
        """A tunnel's bytes are also counted on the NIC that carries them, so
        adding both would double every VPN byte."""
        with mock.patch.object(engine, "physical_interfaces", return_value=["wlan0"]):
            with mock.patch("builtins.open", mock.mock_open(read_data=(
                "Inter-|   Receive\n face |bytes\n"
                "    lo: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0\n"
                " wlan0: 500 5 0 0 0 0 0 0 700 7 0 0 0 0 0 0\n"
                "  tun0: 900 9 0 0 0 0 0 0 900 9 0 0 0 0 0 0\n"
            ))):
                self.assertEqual(engine.read_interface_bytes(), [500, 700])

    def test_no_devices_at_all_reads_as_nothing(self):
        self.assertEqual(engine.read_interface_bytes([]), [0, 0])

    def test_sample_net_buckets_the_residual(self):
        state = {"sockets": {}, "pending": {}, "interfaces": [0, 0], "started": True}
        rows = [{"key": "a", "pid": 10, "down": 1000, "up": 100}]
        added = engine.sample_net(state, "2026-08-24", rows, {10: "helium"},
                                  [50000, 4000])
        bucket = state["pending"]["2026-08-24"]
        self.assertEqual(bucket["helium"], [1000, 100])
        self.assertEqual(bucket["other-traffic"], [49000, 3900])
        self.assertEqual(added, [50000, 4000])
        self.assertEqual(state["interfaces"], [50000, 4000])

    def test_the_first_sample_only_baselines_the_interfaces(self):
        state = {"sockets": {}, "pending": {}, "started": False}
        engine.sample_net(state, "2026-08-24", [], {}, [900000, 90000])
        self.assertEqual(state["pending"], {})
        self.assertEqual(state["interfaces"], [900000, 90000])

    def test_the_bucket_has_a_readable_name(self):
        self.assertEqual(engine.pretty_name("other-traffic"), "Other traffic")

    def test_steam_and_its_web_helper_do_not_share_a_name(self):
        """Two rows both called "Steam" is worse than one with a longer name."""
        self.assertNotEqual(engine.pretty_name("steam"),
                            engine.pretty_name("Steamwebhelper"))


class TestByteFormatting(unittest.TestCase):
    def test_decimal_units(self):
        self.assertEqual(engine.format_bytes(0), "0 B")
        self.assertEqual(engine.format_bytes(999), "999 B")
        self.assertEqual(engine.format_bytes(1000), "1.0 kB")
        self.assertEqual(engine.format_bytes(1500), "1.5 kB")
        self.assertEqual(engine.format_bytes(12_000), "12 kB")
        self.assertEqual(engine.format_bytes(9_400_000), "9.4 MB")
        self.assertEqual(engine.format_bytes(1_234_567_890), "1.2 GB")

    def test_junk_is_zero_rather_than_a_crash(self):
        self.assertEqual(engine.format_bytes(-5), "0 B")
        self.assertEqual(engine.format_bytes(None), "0 B")
        self.assertEqual(engine.format_bytes("nonsense"), "0 B")

    def test_pairs_are_coerced_from_anything(self):
        self.assertEqual(engine.clean_pair([5, 6]), [5, 6])
        self.assertEqual(engine.clean_pair({"d": 5, "u": 6}), [5, 6])
        self.assertEqual(engine.clean_pair({"down": 5, "up": 6}), [5, 6])
        self.assertEqual(engine.clean_pair([-1]), [0, 0])
        self.assertEqual(engine.clean_pair("nope"), [0, 0])
        self.assertEqual(engine.clean_pair(None), [0, 0])

    def test_net_summary_reads_down_first(self):
        summary = engine.net_summary([2000, 1000])
        self.assertEqual(summary["label"], "D 2.0 kB \u00b7 U 1.0 kB")
        self.assertEqual(summary["total"], 3000)


class FakeHyprSocket:
    """A stand-in for Hyprland's control socket: one request, one reply."""

    def __init__(self, payload):
        self.runtime = tempfile.TemporaryDirectory()
        instance = os.path.join(self.runtime.name, "hypr", "testsig")
        os.makedirs(instance)
        self.path = os.path.join(instance, ".socket.sock")
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(1)
        self._thread = threading.Thread(target=self._serve, args=(payload,),
                                        daemon=True)
        self._thread.start()

    def _serve(self, payload):
        try:
            conn, _ = self._server.accept()
            with conn:
                conn.recv(256)
                conn.sendall(payload)
        except OSError:
            pass

    def close(self):
        self._server.close()
        self._thread.join(timeout=2)
        self.runtime.cleanup()


class TestBoundedProducers(unittest.TestCase):
    """Nothing here controls how large an external producer's output can get,
    so every reply is read under a hard ceiling instead of being buffered
    whole and only then handed to json.loads."""

    def ask_hyprland(self, payload):
        fake = FakeHyprSocket(payload)
        try:
            env = {
                "HYPRLAND_INSTANCE_SIGNATURE": "testsig",
                "XDG_RUNTIME_DIR": fake.runtime.name,
            }
            with mock.patch.dict(os.environ, env):
                return engine.hypr_request(b"j/clients")
        finally:
            fake.close()

    def test_a_normal_socket_reply_is_parsed(self):
        clients = [{"class": "helium", "title": "x - YouTube - Helium", "pid": 42}]
        self.assertEqual(self.ask_hyprland(json.dumps(clients).encode()), clients)

    def test_an_oversized_reply_is_refused_whole(self):
        # Half a picture is worse than none: window_apps would otherwise act
        # on whichever clients happened to fit before the cut.
        with mock.patch.object(engine, "MAX_HYPR_BYTES", 64):
            self.assertIsNone(self.ask_hyprland(b"x" * 8192))

    def test_run_ss_caps_an_oversized_producer(self):
        flood = [sys.executable, "-c",
                 "import sys; sys.stdout.write('x' * 100000)"]
        with mock.patch.object(engine, "MAX_SS_BYTES", 1000):
            text = engine.run_ss(flood)
        self.assertIsInstance(text, str)
        self.assertLessEqual(len(text), 1000)

    def test_run_ss_passes_a_normal_producer_through(self):
        text = engine.run_ss([sys.executable, "-c", "print('socket row')"])
        self.assertIn("socket row", text)


class TestLock(StoreTestCase):
    def test_the_lock_is_reentrant_across_sequential_uses(self):
        with engine.Lock():
            pass
        with engine.Lock():
            pass
        self.assertTrue(os.path.exists(engine.lock_path()))

    def test_an_unwritable_data_dir_does_not_raise(self):
        os.environ["XDG_DATA_HOME"] = "/proc/nonexistent-for-tests"
        with engine.Lock():
            pass


if __name__ == "__main__":
    unittest.main()
