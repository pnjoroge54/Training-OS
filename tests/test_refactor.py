"""
Watch tab, Settings, cross-links from the Day plan, readiness in the Journal, paged history, exercise
times, per-exercise analysis and the statistical fixes. Headless Chromium, no phone.
"""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

URL = (Path(__file__).resolve().parents[1] / 'www' / 'index.html').as_uri()


@pytest.fixture()
def page():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={'width': 360, 'height': 780})
        errors = []
        p.on('pageerror', lambda e: errors.append(str(e)))
        p.goto(URL)
        p.wait_for_timeout(400)
        yield p
        assert not errors, errors
        b.close()


def add(p, sid, date, exercises, **extra):
    p.evaluate("""([sid, date, exercises, extra]) => { const s = getStore();
      s.sessions.push(Object.assign({ id: sid, date, day: weekdayName(date), variant: 'ring', status: 'completed', sessionRPE: '', durationMin: '',
        notes: '', exercises }, extra)); s.sessions.sort((a, b) => a.date.localeCompare(b.date)); saveStore(s); }""", [sid, date, exercises, extra])


def lift(name, sets):
    return {'id': name, 'name': name, 'custom': False, 'notes': '', 'sets': sets}


def test_tabs_watch_and_settings(page):
    tabs = page.locator('.tab').all_inner_texts()
    assert tabs.index('Watch') == tabs.index('Journal') + 1
    assert page.locator('#tab-watch #healthCard').count() == 1 and page.locator('#tab-tests #healthCard').count() == 0
    assert page.locator('#tab-journal #readinessCard').count() == 1
    page.click('#tabbtn-watch')
    assert 'CHECK ONE WORKOUT' not in page.inner_text('#tab-watch').upper()
    assert not page.locator('details[data-fold="watch-recent"]').evaluate('d => d.open')     # recent sessions start folded
    page.click('#settingsBtn')
    assert page.is_visible('#alertsCard')
    page.click('#themeSeg button[data-theme="light"]')
    assert page.evaluate("document.documentElement.dataset.theme") == 'light'
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == 'rgb(243, 243, 247)'
    page.reload(); page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.dataset.theme") == 'light'                  # applied before first paint
    page.evaluate("handleBack()")                                                             # back from Settings…
    page.click('#settingsBtn'); page.evaluate("handleBack()")
    assert page.evaluate("document.querySelector('.panel.active').id") != 'tab-settings'


def test_day_plan_links_out_and_back(page):
    today_key = page.evaluate("DAYKEY[weekdayName(todayISO())]")
    page.click('#tabbtn-day')
    assert page.input_value('#dayViewSelect') == today_key                                    # opens on today, not Monday
    flow = page.inner_text('#dayFlow')
    assert 'Readiness check' in flow and 'Warm-up' in flow and 'Cool-down' in flow and 'Log it' in flow
    page.click('#dayFlow button:has-text("Open")')                                            # first "Open" = warm-up
    assert page.evaluate("document.querySelector('.panel.active').id") == 'tab-warmup'
    assert page.is_visible('#backChip') and 'plan' in page.inner_text('#backChip')
    page.click('#backChip')
    assert page.evaluate("document.querySelector('.panel.active').id") == 'tab-day' and not page.is_visible('#backChip')
    # the Android back gesture uses the same return path
    page.click('#dayFlow button:has-text("Open") >> nth=1')                                   # cool-down
    page.evaluate("handleBack()")
    assert page.evaluate("document.querySelector('.panel.active').id") == 'tab-day'


def test_track_exercise_shows_stage_and_links(page):
    page.click('#tabbtn-day')
    page.evaluate("currentVariantKey = 'ring'; currentDayKey = Object.keys(VARIANT_CONFIG.ring.days).find(k => VARIANT_CONFIG.ring.days[k].exercises.some(e => e.name === 'Handstand Track')); renderDay()")
    card = page.locator('#exercises-day .ex-card', has_text='Handstand Track').first
    card.locator('.ex-header').click()
    t = card.inner_text()
    assert ('no stage open yet' in t or 'you are on stage' in t) and 'Stage requirements' in t
    card.locator('button:has-text("Stage requirements")').click()
    assert page.evaluate("document.querySelector('.panel.active').id") == 'tab-tests'
    assert page.locator('#track-handstand').count() == 1


def test_readiness_gate_and_yesterday_load(page):
    y = page.evaluate("(() => { const d = new Date(); d.setDate(d.getDate() - 1); return todayISO(d); })()")
    add(page, 'yy', y, [lift('Push Press', [{'reps': '5', 'weight': '40'}])], sessionRPE='7', durationMin='60')
    page.click('#tabbtn-journal')
    assert page.locator('#readyFold').evaluate('d => d.open')
    assert 'effort load 420' in page.inner_text('#readinessCard')
    assert page.is_visible('#readyGate')
    page.fill('#rd-sleep', '7'); page.click('#readinessCard button:has-text("Save today")')
    assert not page.is_visible('#readyGate') and not page.locator('#readyFold').evaluate('d => d.open')
    assert 'Green' in page.inner_text('#readySummaryPill')


def test_history_pages_and_times(page):
    for i in range(23):
        add(page, f's{i}', f'2026-08-{i + 1:02d}', [lift('Pull-ups', [{'reps': '8', 'at': 0}])])
    add(page, 'timed', '2026-09-01', [lift('Push Press', [{'reps': '5', 'weight': '40', 'at': 1756746000000}, {'reps': '5', 'weight': '40', 'at': 1756746300000}])],
        startedAt=1756745400000, endedAt=1756749000000)
    page.click('#tabbtn-journal')
    assert page.locator('#journalHistory .sess-card').count() == 10 and page.inner_text('#histRange') == '1–10 of 24'
    page.click('#histNext'); page.click('#histNext')
    assert page.locator('#journalHistory .sess-card').count() == 4 and page.is_disabled('#histNext')
    page.select_option('#histSize', '5')
    assert page.locator('#journalHistory .sess-card').count() == 5 and page.inner_text('#histRange') == '1–5 of 24'
    head = page.inner_text('#sess-timed .sess-head')
    assert '–' in head and ':' in head                                                         # start–end clock time
    page.evaluate("toggleSession('timed')")
    assert page.locator('#sess-timed .ex-time').count() == 1


def test_manual_start_time_for_a_backfilled_session(page):
    page.click('#tabbtn-journal')
    page.fill('#j-date', '2026-09-20'); page.dispatch_event('#j-date', 'change')
    page.fill('#j-start', '18:30'); page.dispatch_event('#j-start', 'change')
    page.fill('#j-duration', '45')
    page.click('text=+ Add Exercise')
    page.fill('.ex-block .set-row [data-field=reps]', '10')
    page.evaluate("document.querySelectorAll('.ex-block .set-row').forEach(r => setRowDone(r, true)); autosaveNow()")
    s = page.evaluate("getStore().sessions.find(s => s.date === '2026-09-20')")
    d = page.evaluate("(ms => [new Date(ms).getHours(), new Date(ms).getMinutes()])", s['startedAt'])
    assert d == [18, 30] and s['endedAt'] - s['startedAt'] == 45 * 60000
    assert 'at' not in s['exercises'][0]['sets'][0]                                             # no fake clock time on backfill


def test_exercise_analysis_is_per_exercise_and_skips_watch_modes(page):
    add(page, 'a', '2026-09-01', [lift('Push Press', [{'reps': '5', 'weight': '40'}]), lift('Hollow Body Hold', [{'duration': '30'}])], variant='ring')
    add(page, 'b', '2026-09-03', [lift('Push Press', [{'reps': '5', 'weight': '42.5'}])], variant='mass')
    add(page, 'w', '2026-09-04', [{'id': 'w', 'name': 'Upper limb training', 'custom': True, 'notes': 'Recorded on the watch', 'sets': [{'duration': '7200'}]}])
    page.click('#tabbtn-analysis')
    opts = page.locator('#analysisExSelect option').all_inner_texts()
    assert 'Push Press (2)' in opts and not any('Upper limb' in o for o in opts)               # both programs, no watch mode
    page.select_option('#analysisExSelect', 'Hollow Body Hold')
    assert page.locator('#metricToggle button').all_inner_texts() == ['Longest Hold', 'Time Under Tension']
    page.select_option('#analysisExSelect', 'Push Press')
    assert 'Est. 1RM' in page.locator('#metricToggle button').all_inner_texts()
    page.evaluate("setVariantFilter('mass')")
    assert 'Push Press (2)' in page.locator('#analysisExSelect option').all_inner_texts()      # program chips don't split an exercise


def test_statistics_fixes(page):
    assert page.evaluate("tCrit975(2)") == 4.303 and page.evaluate("tCrit975(1000)") == 1.96
    # 4 points with |t| between 2 and 4.3 were called significant before
    tr = page.evaluate("olsTrend([{x:0,y:100},{x:7,y:104},{x:14,y:102},{x:21,y:109}])")
    assert 2 <= abs(tr['t']) < 4.303 and tr['significant'] is False
    assert page.evaluate("epley1RM(60, 20)") == 0 and round(page.evaluate("epley1RM(100, 5)"), 2) == 116.67


def test_retest_due_after_six_weeks(page):
    page.evaluate("""() => { const s = getStore(); const t = TESTS.find(t => t.battery === 'ring');
      const d = new Date(); d.setDate(d.getDate() - 43);
      s.tests = [{ id: 'r', testId: t.id, date: todayISO(d), value: 10, at: 0 }]; saveStore(s); }""")
    page.click('#tabbtn-tests')
    assert '1 due' in page.inner_text('#batteriesCard')


def test_joint_rotations_detailed_and_reversible(page):
    page.click('#tabbtn-warmup')
    joints = page.locator('#jointRotations .joint-list summary').all_inner_texts()
    assert len(joints) == 10 and joints[0].startswith('Fingers') and joints[-1].startswith('Toes')
    page.click('#jointRotations button:has-text("Toes first")')
    assert page.locator('#jointRotations .joint-list summary').first.inner_text().startswith('Toes')
    assert 'For ' in page.inner_text('#warmupDayNote')


def test_folds_remember_state(page):
    page.click('#tabbtn-handstand')
    folds = page.locator('#tab-handstand details.fold')
    assert folds.count() >= 8
    closed = folds.nth(2)
    assert not closed.evaluate('d => d.open')
    closed.locator('summary').click()
    page.reload(); page.wait_for_timeout(300)
    assert page.locator('#tab-handstand details.fold').nth(2).evaluate('d => d.open')
