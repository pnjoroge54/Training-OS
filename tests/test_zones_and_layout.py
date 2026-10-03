"""
Oraimo's workout screen: its "Heart rate range" is sometimes the workout's (upper limb training, 22 Sep: the five
times add up to the workout's 2 h 6 min) and sometimes the whole day's (outdoor walking, 24 Sep: 2 h 41 min of
zones for a 31-minute walk). The app must tell them apart. Also checks the Progress tab is folded away.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = (Path(__file__).resolve().parents[1] / 'www' / 'index.html').as_uri()


def open_page(pw):
    b = pw.chromium.launch()
    p = b.new_page(viewport={'width': 360, 'height': 780})
    errors = []
    p.on('pageerror', lambda e: errors.append(str(e)))
    p.goto(URL); p.wait_for_timeout(400)
    return b, p, errors


def add_session(p, sid, date, name):
    p.evaluate(f"""() => {{ const s = getStore(); s.sessions.push({{ id: '{sid}', date: '{date}', day: weekdayName('{date}'), variant: 'ring', status: 'completed',
      sessionRPE: '', durationMin: '', notes: '', exercises: [{{ id: 'e', name: '{name}', custom: true, notes: 'Recorded on the watch', sets: [] }}] }}); saveStore(s); }}""")


def fill_summary(p, sid, values):
    p.click('#tabbtn-journal')
    p.evaluate(f"renderHistory(); document.getElementById('sess-{sid}').classList.add('open'); openOraimoForm('{sid}')")
    for k, v in values.items():
        p.fill(f'#oz-{k}-j-{sid}', v)
    p.click(f'#hra-j-{sid} .oraimo-form .timer-btn')
    return p.evaluate(f"getStore().sessions.find(s => s.id === '{sid}')")


def test_whole_day_zones_are_filed_as_the_day_not_the_walk():
    with sync_playwright() as pw:
        b, p, errors = open_page(pw)
        p.evaluate("setPref('hrmax', 190)")
        add_session(p, 'walk', '2026-09-24', 'Outdoor walking')
        s = fill_summary(p, 'walk', {'dur': '31.13', 'avg': '122', 'max': '183', 'km': '1.32', 'steps': '1739',
                                     'lim': '0.20', 'ana': '6.40', 'aer': '17.20', 'fat': '40.50', 'warm': '1.16.50'})
        assert 'zones' not in s['hr']                                          # not the walk's zones
        assert s['hr']['estimated'] == 'average' and s['hr']['trimp'] == 62  # 122/190 = 64% -> zone 2 x 31.2 min
        assert s['distanceKm'] == 1.32 and s['steps'] == 1739 and s['durationMin'] == '31'
        day = p.evaluate("getStore().dayZones.find(d => d.date === '2026-09-24')")
        assert abs(day['total'] - 142.0) < 0.1 and day['load'] == 239            # 2 h 22 min of zones; 76.8 + 2x40.8 + 3x17.3 + 4x6.7 + 5x0.3
        card = p.inner_text('#sess-walk')
        assert '1.32 km' in card and "23'39\"/km" in card and '55 steps/min' in card and 'average heart rate' in card
        assert 'the day' in p.inner_text('#toast')
        assert not errors, errors
        b.close()


def test_workout_zones_still_count_for_the_workout():
    with sync_playwright() as pw:
        b, p, errors = open_page(pw)
        add_session(p, 'up', '2026-09-22', 'Upper limb training')
        s = fill_summary(p, 'up', {'dur': '2.06.35', 'avg': '122', 'max': '162', 'warm': '15.30', 'fat': '1.19.20', 'aer': '28.40', 'ana': '2.40', 'lim': '0'})
        assert s['hr']['trimp'] == 271 and s['hr']['estimated'] is False and s['hr']['zones']['fat'] == 79.33
        assert p.evaluate("getStore().dayZones.length") == 0
        assert not errors, errors
        b.close()


def test_daily_load_keeps_the_fuller_day_and_survives_backup():
    with sync_playwright() as pw:
        b, p, errors = open_page(pw)
        p.evaluate("""() => { const s = getStore();
          saveDayZones(s, todayISO(), { warm: 60, fat: 20, aer: 5, ana: 0, lim: 0 }, 'oraimo-workout-screen');
          saveDayZones(s, todayISO(), { warm: 30, fat: 10, aer: 0, ana: 0, lim: 0 }, 'oraimo-workout-screen');   // earlier, smaller reading
          saveStore(s); }""")
        assert p.evaluate("getStore().dayZones[0].load") == 115
        assert 'dayZones' in p.evaluate("Object.keys(fullBackupObject())")
        p.click('#tabbtn-analysis')
        t = p.inner_text('#analysis-load')
        assert '115' in t and 'not a rest day' in t
        assert not errors, errors
        b.close()


def test_progress_tab_folded_into_tests_and_journal():
    with sync_playwright() as pw:
        b, p, errors = open_page(pw)
        assert p.locator('#tabbtn-progress').count() == 0 and p.locator('#tab-progress').count() == 0
        assert p.locator('#tab-settings #alertsCard').count() == 1                 # alerts live in Settings
        assert p.locator('#tab-journal .quick-rest').count() == 1                  # rest timer next to the log
        p.click('#tabbtn-journal'); p.click('.quick-rest button:has-text("45s")'); p.wait_for_timeout(1200)
        assert p.inner_text('#timerDisplay') != '—:——'
        t = p.evaluate("TESTS.find(t => t.id === 'c_hipdrive').protocol")
        assert 'knee height' in t and 'Count a rep' in t and 'spot' in t
        assert not errors, errors
        b.close()
