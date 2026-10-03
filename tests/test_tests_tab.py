"""
Tests tab redesign (tracks by skill / batteries by test day, "Up next"), time entry without a colon key,
left/right pairs, sequential stages, Kurz's structure-test verdicts, the saved handstand ladder, and the
day plan no longer repeating warm-up and cool-down routines. Headless Chromium, no phone.
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


def seed(p, results):
    """results: list of (testId, value, days_ago)."""
    p.evaluate("""(rs) => { const s = getStore(); s.tests = rs.map(([id, v, ago], i) => { const d = new Date(); d.setDate(d.getDate() - ago);
      return { id: 'x' + i, testId: id, date: todayISO(d), value: v, at: i }; }); saveStore(s); }""", results)


def test_time_is_typed_as_minutes_and_seconds(page):
    # The phone's number pad has no ':' key; "10" + "45" must be 10:45, and a sprint needs only seconds.
    page.click('#tabbtn-tests')
    page.click('#testsViewSeg button[data-view=days]')
    page.click('#battery-ring > summary')
    page.click('#tr-b-ring-r_run2400 .test-head')
    page.fill('#tvm-b-ring-r_run2400', '10'); page.fill('#tv-b-ring-r_run2400', '45')
    page.click('#tr-b-ring-r_run2400 .test-entry button')
    assert page.evaluate("latestResult('r_run2400').value") == 645
    page.click('#tr-b-ring-r_sprint60 .test-head')
    page.fill('#tv-b-ring-r_sprint60', '8,45')          # a comma decimal still works
    page.click('#tr-b-ring-r_sprint60 .test-entry button')
    assert page.evaluate("latestResult('r_sprint60').value") == 8.45
    page.click('#tr-b-ring-r_run300 .test-head')
    page.fill('#tvm-b-ring-r_run300', '1'); page.fill('#tv-b-ring-r_run300', '75')   # 75 s with minutes typed is a typo
    page.click('#tr-b-ring-r_run300 .test-entry button')
    assert page.evaluate("latestResult('r_run300')") is None


def test_left_right_pair_is_one_row(page):
    page.click('#tabbtn-tests')
    page.click('#testsViewSeg button[data-view=days]')
    page.click('#battery-ring > summary')
    assert page.locator('#tr-b-ring-r_jumpturn_l').count() == 0 and page.locator('#tr-b-ring-r_jumpturn').count() == 1
    page.click('#tr-b-ring-r_jumpturn .test-head')
    page.fill('#tv-b-ring-r_jumpturn_l', '270'); page.fill('#tv-b-ring-r_jumpturn_r', '180')
    page.click('#tr-b-ring-r_jumpturn .test-entry button')
    head = page.inner_text('#tr-b-ring-r_jumpturn .test-head')
    assert 'L 270°' in head and 'R 180°' in head
    assert 'differ by 33%' in page.inner_text('#tr-b-ring-r_jumpturn')


def test_stages_open_in_order(page):
    # Stage 3's tests passed but stage 1's not: the old "highest stage met" called this stage 3.
    seed(page, [('c_freehs', 20, 0)])
    assert page.evaluate("stageStatus(TRACKS.find(t => t.id === 'handstand'))") == -1
    seed(page, [('c_wrist', 1, 0), ('c_freehs', 20, 0)])
    assert page.evaluate("stageStatus(TRACKS.find(t => t.id === 'handstand'))") == 0
    # lower-is-better thresholds are compared the right way round
    assert page.evaluate("(() => { const s = getStore(); s.tests = [{ id: 'a', testId: 'r_sprint60', date: todayISO(), value: 7.9, at: 0 }]; saveStore(s); return [reqMet(['r_sprint60', 8.0]), reqMet(['r_sprint60', 7.5])]; })()") == [True, False]


def test_up_next_opens_the_test_where_it_counts(page):
    page.click('#tabbtn-tests')
    nxt = page.inner_text('#testsNext')
    assert 'Wrist extension check' in nxt and 'opens stage 1' in nxt
    page.click('#testsNext li:has-text("Wrist extension") button')
    row = page.locator('#tr-t-handstand-c_wrist')
    assert row.is_visible() and 'open' in row.get_attribute('class')
    row.locator('.test-input button:has-text("Pass")').click()
    assert 'Opened' in page.inner_text('#toast')
    assert 'Wrist extension check' not in page.inner_text('#testsNext')


def test_failed_structure_test_closes_isometrics_without_nagging(page):
    seed(page, [('f_sidechair', 0, 3), ('f_isotrial', 1, 3)])
    page.click('#tabbtn-tests')
    page.click('#track-sidesplit > summary')
    t = page.inner_text('#track-sidesplit')
    assert 'closed by the structure test' in t and 'safe maximum' in t
    assert page.evaluate("stageStatus(TRACKS.find(t => t.id === 'sidesplit'))") == 1      # strength base still open
    assert 'Side-split structure test' not in page.inner_text('#testsNext')


def test_iliopsoas_counts_and_isometric_trial_retest(page):
    seed(page, [('f_lunge180', 1, 0)])
    assert page.evaluate("stageStatus(TRACKS.find(t => t.id === 'frontsplit'))") == 0       # psoas not tested yet
    seed(page, [('f_lunge180', 1, 0), ('f_psoas', 0, 0), ('f_isotrial', 0, 11)])
    assert page.evaluate("stageStatus(TRACKS.find(t => t.id === 'frontsplit'))") == 1
    assert page.evaluate("retestDue(TEST_BY_ID.f_isotrial)") is True                      # Kurz: retry in 1–2 weeks
    assert page.evaluate("retestDue(TEST_BY_ID.f_lunge180)") is False                     # structure: once
    page.click('#tabbtn-tests')
    page.click('#track-frontsplit > summary')
    assert 'hip flexors are short' in page.inner_text('#track-frontsplit')


def test_day_plan_does_not_repeat_routines(page):
    page.click('#tabbtn-day')
    page.evaluate("currentVariantKey = 'ring'; changeDayView('tue')")
    names = page.locator('#exercises-day .ex-name').all_inner_texts()
    assert not {'Morning Dynamic Stretch', 'Wrist Prep', 'Relaxed Stretching'} & set(names)
    assert 'Morning dynamic stretch' in page.inner_text('#dayFlow')
    page.click('#dayFlow li:has-text("Morning dynamic") button')
    assert page.locator('#morningRoutine .ex-name').all_inner_texts() == ['Morning Dynamic Stretch']
    assert page.locator('#exercises-warmup-specific .ex-name').first.inner_text() == 'Wrist Prep'
    page.click('#tabbtn-journal')
    page.evaluate("document.getElementById('j-variant').value = 'ring'; document.getElementById('j-day').value = 'Tuesday'; renderQuickAdd()")
    assert 'Wrist Prep' not in page.inner_text('#quickAddBar') and 'Handstand Track' in page.inner_text('#quickAddBar')


def test_handstand_ladder_is_saved_and_fed_by_tests(page):
    seed(page, [('c_wallhs', 60, 0)])
    page.click('#tabbtn-handstand')
    page.evaluate("document.querySelectorAll('#tab-handstand details.fold').forEach(d => d.open = true)")
    rungs = page.locator('.ladder-rung')
    assert rungs.nth(1).locator('.check-box').get_attribute('aria-checked') == 'true'
    assert 'From your chest-to-wall handstand test' in rungs.nth(1).inner_text()
    page.evaluate("document.querySelectorAll('.ladder-rung .check-box')[0].click()")
    page.reload(); page.wait_for_timeout(300)
    assert page.locator('.ladder-rung').nth(0).locator('.check-box').get_attribute('aria-checked') == 'true'
    assert '01' in page.evaluate("Object.keys(fullBackupObject().ladder)")


def test_restoring_the_automatic_backup_keeps_tests_and_readiness(page):
    seed(page, [('c_wrist', 1, 0)])
    page.evaluate("""() => { const s = getStore(); s.readiness = [{ date: todayISO(), sleep: '8', soreness: '0', rhr: '', pain: [], status: 'green' }];
      s.ladder = { '01': todayISO() }; saveStore(s);
      localStorage.setItem(KEYS.backupAuto, JSON.stringify(fullBackupObject()));
      saveStore(emptyStore()); }""")
    page.on('dialog', lambda d: d.accept())
    page.evaluate("restoreBackup()")
    s = page.evaluate("getStore()")
    assert len(s['tests']) == 1 and len(s['readiness']) == 1 and s['ladder'] == {'01': page.evaluate('todayISO()')}
