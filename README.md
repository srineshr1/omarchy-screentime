# Screen Time

Daily screen time for the [Omarchy](https://omarchy.org) bar, drawn as a
GitHub-style grid of daily boxes. Digital Wellbeing / Apple Screen Time, in
your top bar, entirely local.

The bar shows today's total next to a strip of the last seven days. Clicking it
opens a panel with the per-app breakdown, six months of daily boxes, and the
last seven days as bars.

```
 Screen Time                        1 day under limit   ⟳
 16m
 5H 43M LEFT TODAY
 ────────────────────────────────────────────────────────
 TODAY                                               16m
 Ghostty                                             13m
 Helium                                               2m
 Spotify                                             21s

 Mar – Aug 2026                     16m · 16m/day   ‹  ›
       Mar        Apr      May      Jun      Jul     Aug
 Mon  ▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▪
 Wed  ▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫
 Fri  ▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫
 less ▫▪▪▪▪▪ more · over 6h
```

## Why another screen time plugin

Two others exist (`agx.screen-time`, `omasot`) and both are good. This one is
built around a different picture: **a contribution grid, one box per day**, the
thing you already read at a glance on a GitHub profile. Boxes are shaded
against a daily limit you set, and a day that went over it turns your theme's
urgent colour instead of getting "greener". More is not better here.

## Features

| | |
| --- | --- |
| **Daily boxes** | Six months of days as a GitHub-style grid. Cells resize to fit the panel, so the newest day is never clipped. |
| **Shaded against a limit** | Intensity is a share of your daily limit, and over-limit days go urgent-red. Optional `traffic` palette ramps green → amber → red. |
| **Click any day** | Opens that day's per-app breakdown. Click again, or press `t`, to go back to today. |
| **Per-app breakdown** | Today's apps with share bars, resolved to real names from `.desktop` entries (`com.mitchellh.ghostty` → Ghostty). |
| **Limit tracking** | A progress bar toward the limit, time left, and a streak of consecutive days under it. |
| **Last 7 days** | Clickable bars, today emphasised, zero days shown as a faint rule rather than a stub. |
| **Idle-aware** | The clock stops after `idleTimeoutSec` with no input, and when no window has focus. A bare desktop is not usage. |
| **Video still counts** | Idle inhibitors are respected, so a two-hour film with no keypresses is still two hours of screen time. |
| **Screensaver never counts** | The screensaver holds an idle inhibitor while it runs, so it is excluded by app id, along with lock screens. |
| **Suspend-safe** | A sample that arrives far later than its interval is treated as a gap and dropped, not billed as hours. A backwards clock step is dropped too. |
| **Midnight-safe** | A session running through midnight splits across both days by wall clock, not by whenever the next commit happens. |
| **Bar modes** | Right-click cycles boxes + time → time only → icon only. Remembered on the widget entry. |
| **Terminal CLI** | `screentime today`, `week`, `year` render in the terminal, including an ASCII heatmap. |
| **Local only** | One JSON file. No network, no telemetry, no accounts. |

## Install

```bash
omarchy plugin add https://github.com/srineshr1/omarchy-screentime.git
omarchy plugin enable io.github.ricky.screentime
```

Needs Omarchy Quattro (the Quickshell shell) and `python3`, which Omarchy
already ships. A Nerd Font provides the glyph.

## Using it

| Action | What happens |
| --- | --- |
| Left click | Open / close the panel |
| Middle click | Commit buffered time and refresh now |
| Right click | Cycle the bar display mode |
| Click a box | Show that day's per-app breakdown |
| `t` | Back to today |
| `a` | Expand / collapse the full app list |
| `[` `]` | Move the window one month back / forward |
| `r` | Refresh |
| `Esc` | Close |

Summon it from a keybind:

```bash
omarchy-shell io.github.ricky.screentime toggle
```

## Settings

Configurable from _Setup > Plugins_, or inline on the widget's entry in
`~/.config/omarchy/shell.json`.

| Key | Default | What it does |
| --- | --- | --- |
| `dailyGoalHours` | `6` | Your daily limit. Box shading and the over-limit colour scale against it. |
| `historyMonths` | `6` | Months of daily boxes in the panel. Fewer months means bigger boxes. |
| `idleTimeoutSec` | `120` | No input for this long stops the clock. |
| `barMode` | `strip` | `strip` (boxes + time), `total` (time only), `icon` (glyph only). |
| `gridPalette` | `accent` | `accent` for GitHub's single hue, `traffic` for green → amber → red. |
| `weekStartsMonday` | `true` | Set `false` for Sunday-first rows, like GitHub. |
| `ignoredApps` | `""` | Extra app ids never to count, comma-separated. A trailing `*` matches a prefix (`steam_app_*`). Screensaver and lock screens are always ignored. |
| `detailRetentionDays` | `120` | How long per-app detail is kept. Daily totals are kept forever. |

## How time is counted

The service watches the compositor's focused toplevel and accrues seconds
against that app. Elapsed time is credited to the app that held focus *during*
the interval, not whatever is focused when the timer fires, so switching
windows attributes cleanly.

Nothing accrues while the seat is idle, while no window has focus, or while the
focused surface is a screensaver or lock screen.

Seconds accumulate in memory and are handed to `bin/screentime commit` every
60 seconds, on wake from idle, at midnight, and on shutdown. That helper is the
only writer and it writes atomically via a temp file and `rename`, so a crash
costs at most one 60-second batch and can never leave a torn store.

Known limit: idle is detected by timeout, so up to `idleTimeoutSec` of the
period after you walk away is still counted. Lower it if that bothers you.

## Data

One file:

```
$XDG_DATA_HOME/omarchy-screentime/history.json
```

```json
{
  "version": 1,
  "days": {
    "2026-08-24": {
      "total": 16200,
      "apps": { "com.mitchellh.ghostty": 9000, "firefox": 7200 }
    }
  }
}
```

Integers, seconds. `total` is authoritative and survives pruning, so a day
whose per-app detail has aged out still reports its total. `screentime path`
prints the location; delete the file to reset everything.

Not under `$XDG_DATA_HOME/omarchy/` on purpose: on a real install that is a
symlink to the root-owned `/usr/share/omarchy`.

## CLI

The same engine the widget uses, from a terminal:

```bash
screentime today          # today's total and per-app breakdown
screentime week           # last 7 days as bars
screentime year [YYYY]    # ASCII contribution heatmap
screentime json           # raw store
screentime prune          # drop aged per-app detail
screentime path           # where the store lives
```

Live state over IPC:

```bash
omarchy-shell screentime status   # JSON: current app, counting, pending, window
omarchy-shell screentime today    # "4h 30m"
omarchy-shell screentime flush    # commit buffered time now
```

## Development

```bash
node --test "tests/*.test.mjs"          # pure JS logic (Model.js, Tracker.js)
python3 -m unittest discover -s tests   # the data engine
omarchy plugin validate .               # same checks the shell runs at load
```

`lib/Tracker.js` holds the accrual rules as pure functions — idle handling,
suspend gaps, midnight splits, ignore rules — so what counts as screen time is
testable without a compositor. `lib/Model.js` is presentation-only. Both are
QML JS modules loaded under plain node by the test harness.

Note that saving a file only hot-reloads when the plugin directory is a real
directory under `~/.config/omarchy/plugins/`. If you symlink a working copy in,
restart the shell to pick up changes:

```bash
omarchy-restart-shell
```

## License

MIT
