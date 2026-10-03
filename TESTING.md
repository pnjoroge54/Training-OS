# Testing native alerts

One question drives everything here:

> **When I lock my phone during a rest, does the alert sound on time?**

"It worked when I tried it" is not an answer. The job of the tests is to try to
make the claim fail, under the conditions most likely to break it, with the pass
rule written down before any data comes in.

## What could make the claim false

| Failure mode | Where it comes from | Caught by |
|---|---|---|
| App schedules the wrong time, forgets to cancel, or a slow schedule lands after a cancel | Our JavaScript | Layer 1 |
| Missing notification or exact-alarm permission | Android 12–14 permission model | Layer 1 (fallback logic), Layer 2 step 0 |
| Doze delays or rate-limits "allow while idle" alarms | Android power management | Layer 2, C4–C6 |
| Battery saver, standby buckets, the phone maker's own app killer | Android and OEM skins | Layer 2, C7–C9 |
| Alarms wiped by force-stop or reboot | Android by design | Layer 2, C10–C11 |

Android's documentation says `setExactAndAllowWhileIdle()` (what the plugin
uses) may be throttled in Doze, and the stated limits differ between pages and
Android versions. Treat that as a **hypothesis about your phone**, not a fact.
C6 exists to measure it.

---

## Layer 1: automated, no phone (run after every change)

```bash
pip install pytest playwright
python -m playwright install chromium
python -m pytest -q tests/
```

19 tests. `tests/test_native_bridge.py` loads the real `www/index.html`
against a fake Capacitor runtime whose `schedule()` is deliberately slower than
`cancel()`, with random jitter. It checks:

- rest alert = timer end; +15 moves it; Skip cancels it
- 8× start-then-stop leaves **no** pending alarm (the race test)
- hold-target alert at start + target; stopping cancels it and starts rest
- exactly one alert source: no in-page beep while the system owns alerts
- denied notifications or denied exact alarms → in-page beep, warning, and no
  scheduling (an exact-less schedule would open a settings screen mid-workout)
- screen kept awake during a live session, released on Finish
- exports go through Filesystem + Share (WebViews ignore download links)
- a running rest is re-armed after reload (covers force-stop and reopen)
- the console log format parses with the device tool
- in a plain browser, all native code is inert

`tests/test_alerttest.py` checks the device tool's parser, pairing logic
(reschedule, cancel, miss, orphan) and statistics.

Layer 1 proves the app **asks** Android for the right thing. Only Layer 2 can
show that Android **delivers** it.

---

## Layer 2: on the device

### Setup

1. Phone: Settings → About → tap Build number 7× → Developer options → USB debugging on.
2. `adb devices` shows the phone.
3. `npm run run` installs the debug build. It must be a debug build, because
   console logs reach logcat only in debug builds.
4. In the app, open ⚙ Settings → Alerts. It must read: Notifications **granted**,
   Exact alarms **granted**, Alerts by **System alarm**. If not, tap *Allow alerts*.
5. Phone battery settings: set Training OS to *Unrestricted* / *Don't optimise*.
   Run the matrix **both before and after** this change if you want to know what it
   buys you. See dontkillmyapp.com for your phone maker.

### Step 0: validate the instruments

Before trusting any number, prove the measuring tool sees both ends:

```bash
python tools/alerttest.py condition reset
python tools/alerttest.py watch --condition C0-instrument --out tools/results/c0.csv
```

Tap **Test alert in 60 s** with the screen on. You should see `app: scheduled …`
immediately and `delivered id=11xx +0.xx s` a minute later.

If `delivered` never appears, the log formats on your phone differ from what
the tool expects. Check them directly:

```bash
adb logcat -b events | grep notification_enqueue
adb logcat | grep "\[ALERT\]"
```

Then adjust `ENQUEUE_RE` / `ALERT_RE` in `tools/alerttest.py` and rerun
`tests/test_alerttest.py`. **Don't skip this step.** A tool that silently records
nothing looks exactly like a phone that never fails.

### Pre-registered criteria

Write the date here before collecting data, and don't edit the criteria afterwards:

- Date fixed: ____________
- A condition **passes** if: no misses, no orphans, p95 latency ≤ **2 s**, and
  no single alert more than **10 s** late.
- n = **10 trials per condition** (20 for C6). The sample size is fixed in
  advance: no stopping early because it "looks fine".
- If a condition fails, fix, then **rerun the whole matrix**, not just the
  failing row. A fix for one condition can break another.

### Condition matrix

For each trial: start `watch` with the condition label, tap the test button in
the app, then run the `condition` command within about 20 s (screen-off
conditions need the alarm to be armed *before* the phone goes idle). Wait until
the alert is due + 30 s. Then `condition reset` before the next trial.

| ID | Condition | How | Hypothesis |
|---|---|---|---|
| C1 | App open, screen on | nothing | ≤ 1 s |
| C2 | App in background, screen on | press Home | ≤ 1 s |
| C3 | Screen off | `condition locked` | ≤ 2 s |
| C4 | Light Doze | `condition light-idle` | ≤ 2 s |
| C5 | Deep Doze, single alert | `condition deep-idle` | ≤ 2 s |
| C6 | Deep Doze, **burst 5 × 60 s** | Burst button, then `condition deep-idle` | **Most likely to fail** (idle-alarm rate limits) |
| C7 | Battery saver | `condition battery-saver` | ≤ 2 s |
| C8 | App in "rare" standby bucket | `condition standby-rare` | ≤ 2 s |
| C9 | Process killed in background | Home, then `condition kill-bg` | ≤ 2 s (alarm belongs to the OS) |
| C10 | Force-stop | `condition force-stop` | **Expected miss** (Android clears alarms). Reopen the app: the log must show the rest re-armed |
| C11 | Reboot mid-rest | 3-min test alert, `adb reboot` | Plugin's boot receiver restores it; note latency |
| C12 | Do Not Disturb on | toggle DND manually | Posted but silent. This is a behaviour to know, not a pass/fail |

Use the burst button for C6 to create the realistic worst case: several
alarms due while the phone sits idle.

> `force-idle light|deep` is the documented shell syntax on recent Android.
> If your phone rejects it, `adb shell dumpsys deviceidle force-idle` (deep)
> still works. Confirm the state with `python tools/alerttest.py status`.

### Analyse

```bash
python tools/alerttest.py summary tools/results/run1.csv
```

```
condition            n miss orph  p50 s  p95 s  max s  fail≤  verdict
C1-foreground       10    0    0   0.21   0.40   0.40    26%  PASS
```

The `fail≤` column is the one people skip. With **0 failures in n trials**, the
exact 95% upper bound on the true failure rate is about **3/n** (the "rule of
three"):

| Trials, 0 failures | You can claim failure rate below |
|---|---|
| 10 | 26% |
| 20 | 14% |
| 60 | 5% |

So 10/10 doesn't mean "never fails". It means "fails less than about 1 time in 4,
with 95% confidence". That's enough to catch a broken setup. It's not enough to
promise reliability. Report the bound alongside the PASS.

### Real-world check (R1)

After the matrix passes, train normally for a week with the phone locked
between sets. Note in session notes any rest where the alert didn't come. Lab
conditions (forced Doze) and real ones (phone face-down on the gym floor, other
apps running) differ, and this is the out-of-sample test.

### Decision rule (fixed in advance)

- **Any failure in C1–C5, C7–C9** → bug or setup problem. Fix it before anything else.
- **Only C6 fails** → Doze rate limits are real on this phone. In order of effort:
  1. Rely on the keep-awake that runs during a live session. A screen that's on
     never enters Doze. Measure R1 with the screen left on.
  2. Replace the plugin's alarm with a small custom plugin using
     `AlarmManager.setAlarmClock()`, which Android exempts from idle throttling
     (it shows an alarm icon in the status bar).
  3. A foreground service for the duration of a live session.
- Record which option you chose and rerun the full matrix.

### Record for each run

Phone model, Android version, app commit, battery-optimisation setting, date.
Commit the CSVs in `tools/results/` so results can be compared after changes.

---

## Known limitations

- The plugin schedules against the **wall clock** (`AlarmManager.RTC_WAKEUP`),
  as does the in-app countdown. If the phone's clock is changed mid-rest, both
  shift together.
- Notification channels can't be changed after creation. To change the alert
  sound or importance, rename `CHANNEL` in `index.html` (for example to `-v2`)
  and reinstall.
- Debug logging (`loggingBehavior: "debug"`) is what makes Layer 2 possible.
  Don't turn it off in the build you test.
