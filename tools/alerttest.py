#!/usr/bin/env python3
"""
alerttest.py — measure how late (or whether) Training OS alerts are delivered.

The app logs every schedule/cancel to the WebView console as

    [ALERT] scheduled {"id": 1100, "at": 1758710460000, "kind": "test"}
    [ALERT] cancelled {"id": 1100}

which debug builds forward to logcat (tag Capacitor/Console). Android logs
every posted notification to the *events* buffer as

    notification_enqueue: [uid,pid,<package>,<id>,<tag>,<userid>,<notification>,<status>]

Both carry device-clock timestamps under ``logcat -v epoch``, so

    latency = time(notification_enqueue) - scheduled "at"

is measured on one clock. No host/device clock synchronisation is needed.

Outcomes per scheduled alert
    delivered  posted after its scheduled time (latency recorded)
    missed     not posted within --timeout seconds of its scheduled time
    orphan     posted for an id that was cancelled or never scheduled while
               watching — the signature of a schedule/cancel race

Subcommands
    watch      stream logcat and append outcomes to a CSV, labelled by condition
    condition  put the device into a test condition with adb (and reset it)
    status     show Doze state and this app's pending alarms
    summary    per-condition statistics and pass/fail against criteria

Example
    python tools/alerttest.py condition reset
    python tools/alerttest.py watch --condition C4-deep-idle --out tools/results/run1.csv
    (in the app: Progress -> Alerts -> "Test alert in 60 s", then within 30 s:)
    python tools/alerttest.py condition deep-idle
    ...
    python tools/alerttest.py summary tools/results/run1.csv

Dependencies: Python 3.9+, adb on PATH. No third-party packages.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

PACKAGE = 'com.pnjoroge.trainingos'
APP_ID_RANGE = range(1000, 1200)          # ids the app uses (rest 1001, hold 1002, tests 1100-1199)

EPOCH_RE = re.compile(r'^\s*(\d{9,11}\.\d{3})\s')
ALERT_RE = re.compile(r'\[ALERT\] (scheduled|cancelled|skipped|error|received) (\{.*\})\s*$')
ENQUEUE_RE = re.compile(r'notification_enqueue: \[\d+,\d+,([^,\]]+),(-?\d+),')

CSV_FIELDS = ['condition', 'id', 'kind', 'scheduled_at_ms', 'posted_at_ms', 'latency_ms', 'outcome']


# --------------------------------------------------------------------------- parsing
@dataclass
class Pending:
    id: int
    at_ms: int
    kind: str


@dataclass
class Tracker:
    """Pairs schedule/cancel console logs with notification_enqueue events."""
    condition: str
    package: str = PACKAGE
    timeout_ms: int = 120_000
    pending: Dict[int, Pending] = field(default_factory=dict)
    last_device_ms: int = 0

    def feed(self, line: str) -> List[dict]:
        """Consume one logcat line; return any finalised outcome rows."""
        m = EPOCH_RE.match(line)
        if not m:
            return []
        t_ms = int(round(float(m.group(1)) * 1000))
        self.last_device_ms = max(self.last_device_ms, t_ms)
        rows: List[dict] = []

        a = ALERT_RE.search(line)
        if a:
            event, payload = a.group(1), a.group(2)
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return rows
            aid = data.get('id')
            if isinstance(aid, int):
                if event == 'scheduled' and isinstance(data.get('at'), (int, float)):
                    # A reschedule of the same id supersedes the old alarm.
                    self.pending[aid] = Pending(aid, int(data['at']), str(data.get('kind', '')))
                elif event in ('cancelled', 'skipped'):
                    self.pending.pop(aid, None)
            return rows + self.expire()

        e = ENQUEUE_RE.search(line)
        if e and e.group(1) == self.package:
            nid = int(e.group(2))
            if nid in APP_ID_RANGE:
                p = self.pending.pop(nid, None)
                if p is None:
                    rows.append(self._row(nid, '', None, t_ms, 'orphan'))
                else:
                    rows.append(self._row(nid, p.kind, p.at_ms, t_ms, 'delivered'))
        return rows + self.expire()

    def expire(self, now_ms: Optional[int] = None) -> List[dict]:
        """Declare alerts missed once they are timeout_ms past due."""
        now = now_ms if now_ms is not None else self.last_device_ms
        rows = []
        for aid, p in list(self.pending.items()):
            if now - p.at_ms > self.timeout_ms:
                rows.append(self._row(aid, p.kind, p.at_ms, None, 'missed'))
                del self.pending[aid]
        return rows

    def _row(self, aid, kind, at_ms, posted_ms, outcome) -> dict:
        latency = posted_ms - at_ms if (posted_ms is not None and at_ms is not None) else ''
        return {'condition': self.condition, 'id': aid, 'kind': kind,
                'scheduled_at_ms': at_ms if at_ms is not None else '',
                'posted_at_ms': posted_ms if posted_ms is not None else '',
                'latency_ms': latency, 'outcome': outcome}


# --------------------------------------------------------------------------- statistics
def percentile(sorted_vals: List[float], q: float) -> float:
    """Nearest-rank percentile (conservative for small n: p95 of 10 values is the max)."""
    if not sorted_vals:
        return float('nan')
    k = max(1, math.ceil(q / 100 * len(sorted_vals)))
    return sorted_vals[k - 1]


def binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def failure_upper_bound(failures: int, n: int, conf: float = 0.95) -> float:
    """
    One-sided exact (Clopper–Pearson) upper bound on the failure rate.
    With 0 failures this is 1 - (1-conf)**(1/n) ≈ 3/n — the "rule of three".
    """
    if n == 0:
        return 1.0
    if failures >= n:
        return 1.0
    alpha = 1 - conf
    lo, hi = failures / n, 1.0
    for _ in range(60):                     # bisection on P(X <= k | p) = alpha
        mid = (lo + hi) / 2
        if binom_cdf(failures, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def summarise(rows: Iterable[dict], max_p95_ms: float, max_late_ms: float) -> List[dict]:
    by: Dict[str, List[dict]] = {}
    for r in rows:
        by.setdefault(r['condition'], []).append(r)
    out = []
    for cond, rs in sorted(by.items()):
        delivered = [r for r in rs if r['outcome'] == 'delivered']
        missed = sum(r['outcome'] == 'missed' for r in rs)
        orphans = sum(r['outcome'] == 'orphan' for r in rs)
        lat = sorted(float(r['latency_ms']) for r in delivered if r['latency_ms'] != '')
        n = len(delivered) + missed
        late = sum(v > max_late_ms for v in lat)
        failures = missed + late
        p50, p95 = percentile(lat, 50), percentile(lat, 95)
        passed = (n > 0 and missed == 0 and orphans == 0 and late == 0 and not (p95 > max_p95_ms))
        out.append({
            'condition': cond, 'n': n, 'delivered': len(delivered), 'missed': missed,
            'orphans': orphans, 'p50_s': p50 / 1000, 'p95_s': p95 / 1000,
            'max_s': (lat[-1] / 1000) if lat else float('nan'),
            'fail_rate_ub95': failure_upper_bound(failures, n), 'pass': passed,
        })
    return out


# --------------------------------------------------------------------------- adb
def adb(*args: str, check: bool = False) -> str:
    cmd = ['adb', *args]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        sys.exit('adb not found: install Android platform-tools and add it to PATH.')
    if check and res.returncode != 0:
        sys.exit(f'{" ".join(cmd)} failed:\n{res.stderr}')
    return (res.stdout or '') + (res.stderr or '')


CONDITIONS = {
    # name: (description, list of adb shell commands). {pkg} is substituted.
    'reset': ('Back to normal: exit Doze, restore battery/standby, wake screen', [
        'dumpsys deviceidle unforce', 'dumpsys battery reset', 'settings put global low_power 0',
        'am set-standby-bucket {pkg} active', 'input keyevent KEYCODE_WAKEUP']),
    'locked': ('Screen off (as if the phone were locked on the floor)', [
        'input keyevent KEYCODE_SLEEP']),
    'light-idle': ('Screen off, unplugged, forced into light Doze', [
        'dumpsys battery unplug', 'input keyevent KEYCODE_SLEEP', 'dumpsys deviceidle force-idle light']),
    'deep-idle': ('Screen off, unplugged, forced into deep Doze (worst case)', [
        'dumpsys battery unplug', 'input keyevent KEYCODE_SLEEP', 'dumpsys deviceidle force-idle deep']),
    'battery-saver': ('Battery saver on, screen off', [
        'dumpsys battery unplug', 'settings put global low_power 1', 'input keyevent KEYCODE_SLEEP']),
    'standby-rare': ('App placed in the "rare" standby bucket, screen off', [
        'am set-standby-bucket {pkg} rare', 'input keyevent KEYCODE_SLEEP']),
    'kill-bg': ('Process killed while in background (press Home first)', [
        'am kill {pkg}']),
    'force-stop': ('Force-stop: Android cancels the app\'s alarms by design', [
        'am force-stop {pkg}']),
}


def cmd_condition(args):
    name = args.name
    if name not in CONDITIONS:
        sys.exit(f'Unknown condition. Choose from: {", ".join(CONDITIONS)}')
    desc, cmds = CONDITIONS[name]
    print(f'{name}: {desc}')
    for c in cmds:
        c = c.format(pkg=args.package)
        print(f'  adb shell {c}')
        out = adb('shell', *c.split()).strip()
        if out:
            print('    ' + out.replace('\n', '\n    '))


def cmd_status(args):
    print('Deep idle :', adb('shell', 'dumpsys', 'deviceidle', 'get', 'deep').strip())
    print('Light idle:', adb('shell', 'dumpsys', 'deviceidle', 'get', 'light').strip())
    print('Screen    :', 'on' if 'mWakefulness=Awake' in adb('shell', 'dumpsys', 'power') else 'off/dozing')
    alarms = [l for l in adb('shell', 'dumpsys', 'alarm').splitlines() if args.package in l]
    print(f'Alarm lines mentioning {args.package}: {len(alarms)}')
    for l in alarms[:10]:
        print('  ' + l.strip())


def cmd_watch(args):
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    new_file = not os.path.exists(args.out)
    tracker = Tracker(args.condition, args.package, int(args.timeout * 1000))
    adb('logcat', '-c')                       # start from a clean buffer
    proc = subprocess.Popen(['adb', 'logcat', '-v', 'epoch', '-b', 'main', '-b', 'events'],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding='utf-8', errors='replace')
    counts = {'delivered': 0, 'missed': 0, 'orphan': 0}
    print(f'Watching [{args.condition}] — schedule alerts in the app now. Ctrl-C to stop.')
    with open(args.out, 'a', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if new_file:
            w.writeheader()
        try:
            for line in proc.stdout:
                if '[ALERT] scheduled' in line or '[ALERT] cancelled' in line:
                    print('  app:', line.split('[ALERT]', 1)[1].strip())
                for row in tracker.feed(line):
                    w.writerow(row); fh.flush()
                    counts[row['outcome']] += 1
                    lat = f"{int(row['latency_ms']) / 1000:+.2f} s" if row['latency_ms'] != '' else ''
                    print(f"  {row['outcome']:9s} id={row['id']} {lat}")
        except KeyboardInterrupt:
            pass
        finally:
            proc.terminate()
            # Anything still pending at stop time is reported, not silently dropped.
            if tracker.pending:
                print(f'  {len(tracker.pending)} alert(s) still pending — not recorded; '
                      f'wait past their time + timeout before stopping.')
    print(f"Recorded: {counts}")


def cmd_summary(args):
    with open(args.csv, newline='') as fh:
        rows = list(csv.DictReader(fh))
    res = summarise(rows, args.max_p95 * 1000, args.max_late * 1000)
    hdr = f"{'condition':18s} {'n':>3s} {'miss':>4s} {'orph':>4s} {'p50 s':>6s} {'p95 s':>6s} {'max s':>6s} {'fail≤':>6s}  verdict"
    print(hdr); print('-' * len(hdr))
    for r in res:
        print(f"{r['condition']:18s} {r['n']:3d} {r['missed']:4d} {r['orphans']:4d} "
              f"{r['p50_s']:6.2f} {r['p95_s']:6.2f} {r['max_s']:6.2f} {r['fail_rate_ub95']:6.0%}  "
              f"{'PASS' if r['pass'] else 'FAIL'}")
    print(f"\nCriteria: no misses, no orphans, p95 ≤ {args.max_p95:g} s, every alert ≤ {args.max_late:g} s late.")
    print("fail≤ = 95% upper bound on the true failure rate given n trials (exact binomial).")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--package', default=PACKAGE)
    sub = ap.add_subparsers(dest='cmd', required=True)

    w = sub.add_parser('watch'); w.add_argument('--condition', required=True)
    w.add_argument('--out', default='tools/results/results.csv')
    w.add_argument('--timeout', type=float, default=120, help='seconds past due before an alert counts as missed')
    w.set_defaults(fn=cmd_watch)

    c = sub.add_parser('condition'); c.add_argument('name'); c.set_defaults(fn=cmd_condition)
    s = sub.add_parser('status'); s.set_defaults(fn=cmd_status)

    m = sub.add_parser('summary'); m.add_argument('csv')
    m.add_argument('--max-p95', type=float, default=2.0, help='seconds')
    m.add_argument('--max-late', type=float, default=10.0, help='seconds')
    m.set_defaults(fn=cmd_summary)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == '__main__':
    main()
