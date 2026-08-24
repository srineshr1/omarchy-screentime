#!/usr/bin/env python3
"""python3 -m unittest discover -s tests

bin/screentime has no .py suffix, so it is loaded by path.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

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

    def test_commit_adds_only_the_committed_key(self):
        store = {"version": 1, "days": {}}
        snap = engine.build_snapshot(store, 6, True, None, self.today, None, 6, 0)
        snap["committed"] = 0
        with open(self.FIXTURE, "r", encoding="utf-8") as handle:
            recorded = set(json.load(handle))
        self.assertEqual(set(snap.keys()) - recorded, {"committed"})


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


if __name__ == "__main__":
    unittest.main()
