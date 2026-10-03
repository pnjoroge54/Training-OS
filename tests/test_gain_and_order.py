"""
Gain plan card (rate against 0.25–0.5% of body weight per week), start times on watch workouts, and journal
entries in time order within a day. Headless Chromium, no phone.
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


def weigh(p, kg_per_week, start=70.0, every=2, days=20):
    """Noise-free weigh-ins every `every` days over the last `days` days."""
    p.evaluate("""([r, s, e, n]) => { const bw = []; for (let i = n; i >= 0; i -= e) { const d = new Date(); d.setDate(d.getDate() - i);
        bw.push({ date: todayISO(d), kg: Math.round((s + (n - i) * r / 7) * 100) / 100 }); } setBW(bw); }""", [kg_per_week, start, every, days])


def start_plan(p, target='80', check='75'):
    p.click('#tabbtn-analysis')
    p.fill('#gain-target', target); p.fill('#gain-check', check)
    p.click('#analysis-gain button:has-text("Start plan")')


@pytest.mark.parametrize('rate, verdict', [(0.05, 'Below the band'), (0.25, 'On pace'), (0.8, 'Above the band'), (-0.3, 'Losing weight')])
def test_verdict_follows_the_rate(page, rate, verdict):
    weigh(page, rate)
    start_plan(page)
    assert verdict in page.inner_text('#analysis-gain')
    got = page.evaluate("weightTrend(getBW()).perWeek")
    assert abs(got - rate) < 0.01


def test_needs_enough_weigh_ins(page):
    weigh(page, 0.3, every=4, days=8)                 # 3 readings over 8 days
    start_plan(page)
    assert 'Not enough weigh-ins yet' in page.inner_text('#analysis-gain')


def test_plan_rejects_a_target_below_current_weight_and_flags_the_checkpoint(page):
    weigh(page, 0.3, start=74.0)
    start_plan(page, target='72')
    assert 'this plan is for gaining' in page.inner_text('#toast')
    page.fill('#gain-target', '80'); page.fill('#gain-check', '75')
    page.click('#analysis-gain button:has-text("Start plan")')
    weigh(page, 0.3, start=75.2)
    page.evaluate("renderGainPlan()")
    assert 'Checkpoint 75 kg reached' in page.inner_text('#analysis-gain')


def test_watch_workout_needs_a_start_time_and_lands_in_order(page):
    page.evaluate("""() => { const s = getStore(); const t = (h) => { const d = parseISO(todayISO()); d.setHours(h, 0, 0, 0); return d.getTime(); };
      s.readiness = [{ date: todayISO(), sleep: '8', soreness: '0', rhr: '', pain: [], status: 'green' }];
      s.sessions = [
        { id: 'a', date: todayISO(), day: weekdayName(todayISO()), variant: 'ring', status: 'completed', startedAt: t(6), exercises: [{ id: 'e1', name: 'Boxing Class (Coach)', sets: [{ reps: '', weight: '', duration: '3600' }] }] },
        { id: 'b', date: todayISO(), day: weekdayName(todayISO()), variant: 'ring', status: 'completed', startedAt: t(19), exercises: [{ id: 'e2', name: 'Push Press', sets: [{ reps: '5', weight: '40' }] }] }];
      saveStore(s); }""")
    page.click('#tabbtn-watch')
    page.click('#hcNewWrap summary')
    page.click('#hcn-picker button:has-text("Outdoor walking")')
    page.click('#hcNewWrap button:has-text("Create")')
    assert 'start time' in page.inner_text('#toast')
    page.fill('#hcn-time', '12:30')
    page.click('#hcNewWrap button:has-text("Create")')
    order = page.evaluate("getStore().sessions.map(s => sessionExercises(s)[0].name)")
    assert order == ['Boxing Class (Coach)', 'Outdoor walking', 'Push Press']
    page.click('#tabbtn-journal')
    cards = page.evaluate("[...document.querySelectorAll('#journalHistory .sess-card')].map(c => c.textContent)")
    assert 'Push Press' in cards[0] and 'Outdoor walking' in cards[1] and 'Boxing' in cards[2]    # newest first
    assert '12:30' in cards[1]


def test_summary_form_sets_start_and_stamps_the_exercise(page):
    page.evaluate("""() => { const s = getStore(); s.sessions = [{ id: 'w', date: '2026-09-22', day: 'Tuesday', variant: 'ring', status: 'completed',
        exercises: [{ id: 'e', name: 'Outdoor walking', notes: 'Recorded on the watch', sets: [] }] }]; saveStore(s); }""")
    page.click('#tabbtn-journal')
    assert 'no time' in page.inner_text('#journalHistory')
    page.evaluate("openOraimoForm('w', 'j')")
    page.evaluate("document.getElementById('oz-start-j-w').value = '17:05'; document.getElementById('oz-dur-j-w').value = '31.13'")
    page.evaluate("saveOraimoSummary('w', 'j')")
    s = page.evaluate("getStore().sessions[0]")
    assert page.evaluate("clockHM(getStore().sessions[0].startedAt)") == '17:05'
    assert s['endedAt'] - s['startedAt'] == (31 * 60 + 13) * 1000
    assert s['exercises'][0]['sets'][0]['at'] == s['startedAt']
    assert '17:05' in page.inner_text('#journalHistory')
