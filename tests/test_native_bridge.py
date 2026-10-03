"""
Layer-1 tests: the app's alert/keep-awake/export logic against a *fake*
Capacitor runtime, in headless Chromium. No phone needed.

What this layer can prove: the app asks the OS for the right thing at the right
time (ids, timestamps, cancel order, fallbacks). What it cannot prove: that the
OS then delivers on time — that is layer 2 (device tests, TESTING.md).

Run:  python -m pytest -q tests/test_native_bridge.py
Needs: pip install pytest playwright && python -m playwright install chromium
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = (ROOT / 'www' / 'index.html').as_uri()
sys.path.insert(0, str(ROOT / 'tools'))
import alerttest as at  # noqa: E402

FAKE_CAPACITOR = """
(() => {
  const opts = Object.assign({ display: 'granted', exact: 'granted', grantOnRequest: false }, window.__fakeOpts || {});
  const calls = window.__calls = [];
  const pending = window.__pending = {};
  const delay = ms => new Promise(r => setTimeout(r, ms));
  const rec = (plugin, method, args) =>
    calls.push({ plugin, method, args: args === undefined ? null : JSON.parse(JSON.stringify(args)), t: Date.now() });
  const LN = {
    async createChannel(c) { rec('LN', 'createChannel', c); await delay(20); },
    async checkPermissions() { return { display: opts.display }; },
    async requestPermissions() { rec('LN', 'requestPermissions'); if (opts.grantOnRequest) opts.display = 'granted'; return { display: opts.display }; },
    async checkExactNotificationSetting() { return { exact_alarm: opts.exact }; },
    async changeExactNotificationSetting() { rec('LN', 'changeExactNotificationSetting'); return { exact_alarm: opts.exact }; },
    // Deliberately slower than cancel, with jitter: exposes schedule/cancel races.
    async schedule(o) {
      await delay(40 + Math.random() * 60);
      rec('LN', 'schedule', o);
      o.notifications.forEach(n => { pending[n.id] = new Date(n.schedule.at).getTime(); });
      return { notifications: o.notifications.map(n => ({ id: n.id })) };
    },
    async cancel(o) { await delay(5); rec('LN', 'cancel', o); o.notifications.forEach(n => { delete pending[n.id]; }); },
    addListener(ev, fn) { window.__lnListener = fn; return Promise.resolve({ remove() {} }); },
  };
  const KeepAwake = { async keepAwake() { rec('KA', 'keepAwake'); }, async allowSleep() { rec('KA', 'allowSleep'); } };
  const Filesystem = { async writeFile(o) { rec('FS', 'writeFile', { path: o.path, directory: o.directory, encoding: o.encoding, bytes: (o.data || '').length }); return { uri: 'file:///data/cache/' + o.path }; } };
  const Share = { async share(o) { rec('Share', 'share', o); return {}; } };
  window.Capacitor = { isNativePlatform: () => true, isPluginAvailable: () => true,
                       Plugins: { LocalNotifications: LN, KeepAwake, Filesystem, Share } };
})();
"""


# Note: page.evaluate() *calls* a string that evaluates to a function, so the
# spy is installed inside a function body rather than as a bare assignment.
SPY_BEEP = "() => { window.__beeps = 0; window.beep = () => { window.__beeps++; }; }"


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def open_app(browser, **fake_opts):
    ctx = browser.new_context(viewport={'width': 390, 'height': 844})
    ctx.add_init_script(f"window.__fakeOpts = {json.dumps(fake_opts)};")
    ctx.add_init_script(FAKE_CAPACITOR)
    page = ctx.new_page()
    page.errors = []
    page.console_lines = []
    page.on('pageerror', lambda e: page.errors.append(str(e)))
    page.on('console', lambda m: page.console_lines.append(m.text))
    page.goto(URL)
    page.wait_for_function("NativeAlerts.state.ready === true || NativeAlerts.state.warning !== ''")
    page.evaluate(SPY_BEEP)
    return ctx, page


def settle(page, ms=600):
    page.evaluate("NativeAlerts.flush()")
    page.wait_for_timeout(ms)


def pending(page):
    return {int(k): v for k, v in page.evaluate("window.__pending").items()}


def calls(page, method=None):
    cs = page.evaluate("window.__calls")
    return [c for c in cs if method is None or c['method'] == method]


SEED_READINESS = """() => { const s = getStore(); s.readiness = (s.readiness || []).concat([{ date: todayISO(), sleep: '7.5', soreness: '0', rhr: '55', pain: [], head: false, status: 'green' }]); saveStore(s); }"""


def start_live_set(page, ready=True):
    """Open the journal, add an exercise and tick its first set (auto-rest on)."""
    if ready:
        page.evaluate(SEED_READINESS)          # today's check is required before a session is recorded
    page.click('#tabbtn-journal')
    page.click('text=+ Add Exercise')
    page.fill('.ex-block .set-row [data-field=reps]', '10')
    page.click('.ex-block .set-row .set-done')      # typing ticked it; this unticks…
    page.click('.ex-block .set-row .set-done')      # …and this re-ticks, starting rest


# ----------------------------------------------------------------------------- tests
def test_channel_created_with_sound_and_high_importance(browser):
    ctx, page = open_app(browser)
    ch = calls(page, 'createChannel')[0]['args']
    assert ch['sound'] == 'rest_over.wav' and ch['importance'] == 5 and ch['vibration'] is True
    assert page.evaluate("NativeAlerts.owns()") is True
    assert page.errors == []
    ctx.close()


def test_rest_alert_follows_timer_plus15_and_skip(browser):
    ctx, page = open_app(browser)
    start_live_set(page)
    settle(page)
    end_at = page.evaluate("timerEndAt")
    assert abs(pending(page)[1001] - end_at) < 5
    page.click('.lb-rest button:has-text("+15")')
    settle(page)
    assert pending(page)[1001] - end_at == 15_000
    page.click('.lb-rest button:has-text("Skip")')
    settle(page)
    assert 1001 not in pending(page)
    ctx.close()


def test_no_orphan_alert_when_stopped_immediately(browser):
    """schedule() is slower than cancel(); the queue must preserve call order."""
    ctx, page = open_app(browser)
    for _ in range(8):
        page.evaluate("startTimer(60); stopTimer();")
    page.evaluate("startTimer(60); startTimer(90);")
    settle(page, 1500)
    p = pending(page)
    assert list(p) == [1001]
    assert abs(p[1001] - page.evaluate("timerEndAt")) < 5
    page.evaluate("stopTimer()")
    settle(page, 800)
    assert pending(page) == {}
    ctx.close()


def test_hold_target_alert_and_auto_rest(browser):
    ctx, page = open_app(browser)
    # Today's readiness check comes first; without it the session is held back (tested in test_zones_and_layout).
    page.evaluate("""() => { const s = getStore(); s.readiness = [{ date: todayISO(), sleep: '8', soreness: '0', rhr: '', pain: [], head: false, status: 'green' }]; saveStore(s); }""")
    page.click('#tabbtn-journal')
    page.click('text=+ Add Exercise')
    page.evaluate("""() => { const r = document.querySelector('.ex-block .set-row');
        r.querySelector('[data-field=duration]').value = '30'; setRowDone(r, false); }""")
    page.click('.ex-block .set-row .set-sw-btn')
    settle(page)
    run_at = page.evaluate("Number(document.querySelector('.set-row.running').dataset.runAt)")
    assert abs(pending(page)[1002] - (run_at + 30_000)) < 5
    page.click('.ex-block .set-row .set-sw-btn')           # stop early
    settle(page)
    p = pending(page)
    assert 1002 not in p and 1001 in p                     # hold cancelled, rest started
    ctx.close()


def test_single_alert_source_in_foreground(browser):
    ctx, page = open_app(browser)
    page.evaluate("startTimer(2)")
    page.wait_for_timeout(3000)
    assert page.evaluate("window.__beeps") == 0           # the system notification sounds instead
    ctx.close()


def test_denied_notifications_fall_back_to_beep(browser):
    ctx, page = open_app(browser, display='denied')
    assert calls(page, 'requestPermissions'), 'first launch should ask once'
    page.evaluate(SPY_BEEP)
    page.evaluate("startTimer(2)")
    page.wait_for_timeout(3000)
    assert calls(page, 'schedule') == []
    assert page.evaluate("window.__beeps") == 1
    page.click('#settingsBtn')
    assert page.is_visible('#alertAllowBtn') and page.is_visible('#alertWarning')
    ctx.close()


def test_exact_alarm_denied_never_schedules(browser):
    """Scheduling without exact access would pop a settings screen mid-workout."""
    ctx, page = open_app(browser, exact='denied')
    page.evaluate("startTimer(30)")
    settle(page)
    assert calls(page, 'schedule') == []
    page.click('#settingsBtn')
    page.click('#alertAllowBtn')
    settle(page, 300)
    assert calls(page, 'changeExactNotificationSetting')
    ctx.close()


def test_keep_awake_follows_live_session(browser):
    ctx, page = open_app(browser)
    start_live_set(page)
    page.wait_for_timeout(600)
    assert calls(page, 'keepAwake')
    page.click('.lb-rest button:has-text("Skip")')
    page.click('#saveBtn')                                   # Finish session
    page.wait_for_timeout(600)
    assert calls(page, 'allowSleep')
    ctx.close()


def test_export_goes_through_share_sheet(browser):
    ctx, page = open_app(browser)
    start_live_set(page)                              # something to export
    page.wait_for_timeout(700)
    page.evaluate("exportJournal()")
    page.wait_for_timeout(500)
    w = calls(page, 'writeFile')[0]['args']
    s = calls(page, 'share')[0]['args']
    assert w['directory'] == 'CACHE' and w['encoding'] == 'utf8' and w['path'].endswith('.json')
    assert s['files'] == ['file:///data/cache/' + w['path']]
    ctx.close()


def test_rest_rearmed_after_reload(browser):
    """Force-stop clears alarms; reopening the app must re-arm a running rest."""
    ctx, page = open_app(browser)
    page.evaluate("startTimer(120)")
    settle(page)
    end_at = page.evaluate("timerEndAt")
    page.reload()                                   # fresh page, fresh fake: no pending alarms
    page.wait_for_function("NativeAlerts.state.ready === true")
    settle(page, 800)
    rest = [c for c in calls(page, 'schedule') if c['args']['notifications'][0]['id'] == 1001]
    assert rest, 'running rest was not re-armed'
    assert rest[-1]['args']['notifications'][0]['extra']['scheduledAt'] == end_at
    assert pending(page).get(1001) == end_at
    ctx.close()


def test_test_alert_buttons_and_log_format_match_tool(browser):
    ctx, page = open_app(browser)
    page.click('#settingsBtn')
    page.click('text=Burst: 5 × 60 s')
    settle(page, 1000)
    ids = sorted(pending(page))
    assert len(ids) == 5 and all(1100 <= i <= 1199 for i in ids)
    # The console lines the app emits must parse with the device tool's regex.
    tr = at.Tracker('fmt')
    now = time.time()
    for text in page.console_lines:
        tr.feed(f"{now:.3f}  1 1 I Capacitor/Console: File: x - Line 1 - Msg: {text}")
    assert sorted(tr.pending) == ids
    page.click('text=Cancel tests')
    settle(page, 800)
    assert pending(page) == {}
    ctx.close()


def test_browser_mode_is_inert(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    errs = []
    page.on('pageerror', lambda e: errs.append(str(e)))
    page.on('console', lambda m: m.type == 'error' and errs.append(m.text))
    page.goto(URL)
    page.wait_for_timeout(500)
    assert page.evaluate("NativeAlerts.state.native") is False
    assert page.evaluate("document.querySelector('script[src=\"vendor/capacitor.js\"]')") is None
    page.click('#settingsBtn')
    assert 'Browser' in page.inner_text('#alertStatus')
    assert errs == []
    ctx.close()


def test_session_waits_for_todays_readiness_check(browser):
    """Sets ticked before the day's readiness check stay in the form and are recorded once it is saved."""
    ctx, page = open_app(browser)
    start_live_set(page, ready=False)
    page.wait_for_timeout(900)
    assert page.evaluate("getStore().sessions.length") == 0
    assert page.is_visible('#readyGate')
    page.click('#saveBtn')                                   # Finish points to the check instead of "nothing logged"
    assert 'readiness' in page.inner_text('#toast').lower()
    page.fill('#rd-sleep', '7'); page.fill('#rd-rhr', '55')
    page.click('#readinessCard button:has-text("Save today")')
    page.wait_for_timeout(600)
    s = page.evaluate("getStore().sessions")
    assert len(s) == 1 and s[0]['exercises'][0]['sets'][0]['at'] > 0      # recorded, with the set's clock time
    assert not page.is_visible('#readyGate')
    ctx.close()
