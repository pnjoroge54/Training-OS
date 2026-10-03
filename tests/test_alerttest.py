"""Unit tests for tools/alerttest.py — parser, pairing logic and statistics.

Log lines are synthetic but follow the formats the tool expects; the first real
device run is the check that the formats match (see TESTING.md, step 0).
"""
import math
import re
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import alerttest as at  # noqa: E402

PKG = at.PACKAGE


T0 = 1_758_710_000          # realistic epoch seconds; test times are offsets from it


def ms(t):
    return (T0 + t) * 1000


def console(t, msg):
    msg = re.sub(r'"at":(\d+)', lambda m: f'"at":{int(ms(int(m.group(1)) / 1000))}', msg)
    t += T0
    return f"{t:.3f}  4321  4321 I Capacitor/Console: File: https://localhost/ - Line 2001 - Msg: {msg}"


def enqueue(t, nid, pkg=PKG):
    t += T0
    return (f"{t:.3f}  1000  1200 I notification_enqueue: "
            f"[10234,4321,{pkg},{nid},NULL,0,Notification(channel=training-alerts-v1 shortcut=null flags=0x10),0]")


def feed_all(tr, lines):
    rows = []
    for l in lines:
        rows += tr.feed(l)
    return rows


def test_delivered_latency_on_device_clock():
    tr = at.Tracker('C1')
    rows = feed_all(tr, [
        console(1000.000, '[ALERT] scheduled {"id":1100,"at":1060000,"kind":"test"}'),
        enqueue(1060.450, 1100),
    ])
    assert len(rows) == 1
    assert rows[0]['outcome'] == 'delivered' and rows[0]['latency_ms'] == 450


def test_reschedule_supersedes_and_cancel_is_not_a_miss():
    tr = at.Tracker('C1', timeout_ms=5000)
    rows = feed_all(tr, [
        console(1000.0, '[ALERT] scheduled {"id":1001,"at":1060000,"kind":"rest"}'),
        console(1010.0, '[ALERT] scheduled {"id":1001,"at":1075000,"kind":"rest"}'),   # +15 s
        enqueue(1075.2, 1001),
        console(1100.0, '[ALERT] scheduled {"id":1001,"at":1160000,"kind":"rest"}'),
        console(1105.0, '[ALERT] cancelled {"id":1001}'),                              # Skip
        console(1300.0, 'unrelated line to advance the clock'),
    ])
    assert [r['outcome'] for r in rows] == ['delivered']
    assert rows[0]['latency_ms'] == 200


def test_missed_after_timeout():
    tr = at.Tracker('C4', timeout_ms=120_000)
    rows = feed_all(tr, [
        console(1000.0, '[ALERT] scheduled {"id":1101,"at":1060000,"kind":"test"}'),
        console(1179.0, 'tick'),
        console(1181.0, 'tick'),
    ])
    assert [r['outcome'] for r in rows] == ['missed']


def test_orphan_when_cancelled_alert_still_fires():
    tr = at.Tracker('C1')
    rows = feed_all(tr, [
        console(1000.0, '[ALERT] scheduled {"id":1001,"at":1060000,"kind":"rest"}'),
        console(1001.0, '[ALERT] cancelled {"id":1001}'),
        enqueue(1060.1, 1001),
    ])
    assert [r['outcome'] for r in rows] == ['orphan']


def test_other_packages_and_ids_ignored():
    tr = at.Tracker('C1')
    rows = feed_all(tr, [
        console(1000.0, '[ALERT] scheduled {"id":1100,"at":1060000,"kind":"test"}'),
        enqueue(1060.0, 1100, pkg='com.whatsapp'),
        enqueue(1060.0, 42),
    ])
    assert rows == [] and 1100 in tr.pending


def test_rule_of_three_bound():
    # 0 failures in n trials: exact one-sided 95% bound = 1 - 0.05**(1/n) ≈ 3/n
    for n in (10, 20, 60):
        ub = at.failure_upper_bound(0, n)
        assert math.isclose(ub, 1 - 0.05 ** (1 / n), rel_tol=1e-6)
        assert abs(ub - 3 / n) < 0.05


def test_summary_verdicts():
    rows = [{'condition': 'A', 'outcome': 'delivered', 'latency_ms': str(v)} for v in (100, 200, 300)]
    rows += [{'condition': 'B', 'outcome': 'delivered', 'latency_ms': '500'},
             {'condition': 'B', 'outcome': 'missed', 'latency_ms': ''}]
    rows += [{'condition': 'C', 'outcome': 'delivered', 'latency_ms': '15000'}]
    res = {r['condition']: r for r in at.summarise(rows, 2000, 10000)}
    assert res['A']['pass'] and res['A']['n'] == 3 and res['A']['p95_s'] == 0.3
    assert not res['B']['pass'] and res['B']['missed'] == 1
    assert not res['C']['pass']            # one alert 15 s late breaks the max-late rule
