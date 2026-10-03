"""
Layer-1 tests for the Health Connect integration against a fake Health plugin
(headless Chromium, no phone). The fake mirrors what the Oraimo app was seen to
share on a real phone: sleep, heart rate and steps, but no sport records.

Proves the app's own logic: read-only requests, readiness pre-fill, like-with-
like resting-HR baselines, HRmax from all readings, per-session heart rate from
the HR stream (dense and sparse), and that attached data survives edits. It
cannot prove what Oraimo writes on a given night — the in-app "What the watch
shares" list shows that.

Run:  python -m pytest -q tests/test_health_connect.py
"""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = (ROOT / 'www' / 'index.html').as_uri()

FAKE_HEALTH = r"""
(() => {
  const now = new Date();
  const bed = new Date(now); bed.setDate(bed.getDate() - 1); bed.setHours(22, 30, 0, 0);
  const wake = new Date(now); wake.setHours(6, 0, 0, 0);
  if (wake > now) { bed.setDate(bed.getDate() - 1); wake.setDate(wake.getDate() - 1); }
  const src = 'com.transsion.oraimohealth';
  const hr = [];
  const add = (t, v) => hr.push({ dataType: 'heartRate', value: v, startDate: new Date(t).toISOString(), endDate: new Date(t).toISOString(), sourceName: src });
  for (let t = bed.getTime(); t < wake.getTime(); t += 300000) add(t, 52 + Math.round(10 * Math.abs(Math.sin(t / 3e6))));
  // dense session yesterday 18:00-19:00 (every 5 s, sport mode), sparse session yesterday 07:00-08:00 (every 7 min)
  const y = new Date(now); y.setDate(y.getDate() - 1);
  const dense0 = new Date(y); dense0.setHours(18, 0, 0, 0);
  for (let i = 0; i < 720; i++) add(dense0.getTime() + i * 5000, i < 360 ? 175 : 125);
  const sparse0 = new Date(y); sparse0.setHours(7, 0, 0, 0);
  for (let i = 0; i < 9; i++) add(sparse0.getTime() + i * 420000, 150);
  const Health = {
    isAvailable: async () => ({ available: true, platform: 'android' }),
    checkAuthorization: async () => ({ readAuthorized: window.__granted || [] }),
    requestAuthorization: async (o) => { window.__req = o; window.__granted = o.read.filter(x => x !== 'restingHeartRate' && x !== 'heartRateVariability'); return { readAuthorized: window.__granted }; },
    readSamples: async ({ dataType, startDate, endDate }) => {
      const s = new Date(startDate), e = new Date(endDate);
      if (dataType === 'sleep') return { samples: [{ dataType: 'sleep', value: 450, startDate: bed.toISOString(), endDate: wake.toISOString(), sourceName: src,
        stages: [{ stage: 'light', durationMinutes: 250 }, { stage: 'deep', durationMinutes: 120 }, { stage: 'awake', durationMinutes: 30 }, { stage: 'rem', durationMinutes: 50 }] }].filter(x => new Date(x.endDate) > s) };
      if (dataType === 'heartRate') return { samples: hr.filter(x => new Date(x.startDate) >= s && new Date(x.startDate) < e) };
      return { samples: [] };
    },
    queryAggregated: async ({ dataType }) => ({ samples: dataType === 'heartRate' ? [{ value: 176 }, { value: 168 }] : [] }),
    queryWorkouts: async () => ({ workouts: [] }),     // Oraimo does not share sport records
    openHealthConnectSettings: async () => {}
  };
  window.Capacitor = { isNativePlatform: () => true, Plugins: { Health } };
})();
"""


@pytest.fixture()
def page():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context()
        ctx.add_init_script(FAKE_HEALTH)
        p = ctx.new_page()
        errors = []
        p.on('pageerror', lambda e: errors.append(str(e)))
        p.goto(URL)
        p.wait_for_timeout(400)
        yield p
        assert not errors, errors
        b.close()


def connect(p):
    p.click('#tabbtn-watch')
    p.click('text=Connect Health Connect')
    p.wait_for_function("HC.state === 'connected' && document.getElementById('rd-rhr').value !== ''")


def add_session(p, sid, hour, minute, mins):
    p.evaluate(f"""() => {{ const y = new Date(); y.setDate(y.getDate() - 1); const iso = todayISO(y);
      const st = parseISO(iso); st.setHours({hour}, {minute}, 0, 0);
      const s = getStore(); s.sessions.push({{ id: '{sid}', date: iso, day: weekdayName(iso), variant: 'ring', status: 'completed', sessionRPE: '7',
        durationMin: '', notes: '', startedAt: st.getTime(), endedAt: st.getTime() + {mins} * 60000,
        exercises: [{{ id: 'e', name: 'Trap-Bar Deadlift', custom: false, notes: '', sets: [{{ reps: '5', weight: '80', duration: '', rest: '', rpe: '' }}] }}] }}); saveStore(s); }}""")


def read_hr(p, sid):
    p.click('#tabbtn-journal')
    p.evaluate(f"renderHistory(); document.getElementById('sess-{sid}').classList.add('open'); openHrForm('{sid}')")
    p.click(f"#hra-j-{sid} .timer-btn")
    p.wait_for_function(f"!!(getStore().sessions.find(s => s.id === '{sid}').hr)")
    return p.evaluate(f"getStore().sessions.find(s => s.id === '{sid}').hr")


def test_requests_read_only_and_prefills_readiness(page):
    connect(page)
    assert page.evaluate("window.__req.write.length") == 0
    assert page.input_value('#rd-sleep') == '7'          # 420 min asleep; awake stage excluded
    assert page.input_value('#rd-rhr') == '52'           # no resting-HR record -> lowest HR in sleep
    assert page.evaluate("hcAutofillInfo.rhr.method") == 'sleep-min'


def test_hrmax_taken_from_all_readings_but_not_single_spikes(page):
    # The day's aggregate max is 176, but no two readings agree on it; 175 is held for half an hour.
    connect(page)
    page.wait_for_function("getPrefs().hrmaxObserved === 175")
    assert '175' in page.inner_text('#hcHrmaxLine')
    t0 = 'Date.parse("2026-09-24T18:00:00Z")'
    spike = page.evaluate(f"sustainedMax([[0,122],[60,124],[120,183],[400,125]].map(([s, v]) => ({{ startDate: new Date({t0} + s * 1000).toISOString(), value: v }})))")
    assert spike == 124           # a lone 183 between two ~122 readings is not a maximum


def test_baselines_never_mix_methods(page):
    page.evaluate("""() => { const s = getStore(); const d = n => { const x = new Date(); x.setDate(x.getDate() - n); return todayISO(x); };
      s.readiness = [1,2,3,4,5].map(n => ({ date: d(n), sleep: '7', soreness: '0', rhr: '45', pain: [], rhrMethod: 'sleep-min' }));
      saveStore(s); }""")
    r = page.evaluate("assessReadiness({ date: todayISO(), sleep: '7', soreness: '0', rhr: '60', pain: [], rhrMethod: 'manual' })")
    assert r['status'] == 'green'
    r = page.evaluate("assessReadiness({ date: todayISO(), sleep: '7', soreness: '0', rhr: '53', pain: [], rhrMethod: 'sleep-min' })")
    assert r['status'] == 'amber'


def test_edwards_trimp_dense_and_sparse(page):
    # dense: 10 min at 95% (zone 5) + 10 min at 65% (zone 2) -> 50 + 20 = 70
    t = page.evaluate("""() => { const s = [], t0 = Date.now(); for (let i = 0; i <= 1200; i += 30) s.push({ startDate: new Date(t0 + i * 1000).toISOString(), value: i < 600 ? 190 : 130 }); return hrStats(s, 200, 1200); }""")
    assert abs(t['trimp'] - 70) <= 1 and not t['estimated']
    # sparse: 4 readings at 75% over a 60-min window -> zone 3 x 60 min = 180, flagged as an estimate
    t = page.evaluate("""() => { const t0 = Date.now(); return hrStats([0, 900, 1800, 2700].map(i => ({ startDate: new Date(t0 + i * 1000).toISOString(), value: 150 })), 200, 3600); }""")
    assert t['estimated'] and t['trimp'] == 180


def test_session_hr_from_stream_dense(page):
    add_session(page, 'sd', 18, 0, 60)
    connect(page)
    page.fill('#hc-hrmax', '190'); page.dispatch_event('#hc-hrmax', 'change')
    hr = read_hr(page, 'sd')
    assert hr['n'] == 720 and not hr['estimated']
    assert hr['max'] == 175 and 145 <= hr['avg'] <= 155
    assert hr['trimp'] > 100


def test_session_hr_from_stream_sparse_is_flagged(page):
    add_session(page, 'ss', 7, 0, 60)
    connect(page)
    hr = read_hr(page, 'ss')
    assert hr['n'] == 9 and hr['estimated']
    assert 'estimated' in page.inner_text('#sess-ss').lower() or '(est.)' in page.inner_text('#sess-ss')


def test_hr_and_times_survive_edit(page):
    add_session(page, 'sx', 18, 0, 60)
    connect(page)
    read_hr(page, 'sx')
    page.evaluate("showTab('journal'); editSession('sx')")
    page.fill('#j-notes', 'edited')
    page.wait_for_timeout(900)
    s = page.evaluate("getStore().sessions.find(s => s.id === 'sx')")
    assert s['notes'] == 'edited' and s['hr']['n'] == 720 and s['startedAt']


def test_inert_in_plain_browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page()
        p.goto(URL)
        p.wait_for_timeout(300)
        p.click('#tabbtn-watch')
        assert p.evaluate("HC.state") == 'web'
        assert 'Android app' in p.inner_text('#healthCard')
        b.close()


def test_oraimo_summary_gives_exact_load(page):
    # Patrick's real "Upper limb training", 22 Sep: 15:30 / 1:19:20 / 28:40 / 2:40 / 0:00 in the five zones
    add_session(page, 'up', 14, 50, 127)
    page.click('#tabbtn-journal')
    page.evaluate("renderHistory(); document.getElementById('sess-up').classList.add('open'); openOraimoForm('up')")
    for k, v in [('avg', '122'), ('max', '162'), ('warm', '15:30'), ('fat', '1:19:20'), ('aer', '28:40'), ('ana', '2:40'), ('lim', '0:00')]:
        page.fill(f'#oz-{k}-j-up', v)
    page.click('#hra-j-up .oraimo-form .timer-btn')
    hr = page.evaluate("getStore().sessions.find(s => s.id === 'up').hr")
    # 15.5*1 + 79.33*2 + 28.67*3 + 2.67*4 + 0*5 = 270.8
    assert hr['trimp'] == 271 and hr['avg'] == 122 and hr['minutes'] == 126.2 and hr['source'] == 'oraimo-summary'
    assert 'fat 79' in page.inner_text('#sess-up')


def test_oraimo_summary_works_without_health_connect():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page()
        p.goto(URL)
        p.wait_for_timeout(300)
        add_session(p, 'lo', 14, 13, 64)
        p.click('#tabbtn-journal')
        p.evaluate("renderHistory(); document.getElementById('sess-lo').classList.add('open')")
        assert 'Read from Health Connect' not in p.inner_text('#sess-lo')
        p.evaluate("openOraimoForm('lo')")
        for k, v in [('warm', '42:00'), ('fat', '13:50'), ('aer', '5:50'), ('ana', '1:20')]:
            p.fill(f'#oz-{k}-j-lo', v)
        p.click('#hra-j-lo .oraimo-form .timer-btn')
        assert p.evaluate("getStore().sessions.find(s => s.id === 'lo').hr.trimp") == 93   # 42 + 27.67 + 17.5 + 5.33 = 92.5
        b.close()


def test_watch_card_lists_sessions_without_health_connect():
    """The heart-rate tools must be reachable from the Watch card itself, even in a plain browser."""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page()
        errors = []
        p.on('pageerror', lambda e: errors.append(str(e)))
        p.goto(URL)
        p.wait_for_timeout(300)
        add_session(p, 'ww', 18, 0, 60)
        p.click('#tabbtn-watch')
        p.click('#tab-watch details[data-fold="watch-recent"] > summary')     # recent sessions start folded
        assert 'Trap-Bar Deadlift' in p.inner_text('#hcSessions')
        p.click('#hra-w-ww button')                       # Enter Oraimo summary, in the Watch card
        p.fill('#oz-fat-w-ww', '30:00')
        p.click('#hra-w-ww .oraimo-form .timer-btn')
        assert p.evaluate("getStore().sessions.find(s => s.id === 'ww').hr.trimp") == 60
        assert 'load 60' in p.inner_text('#hcSessions')
        assert not errors, errors
        b.close()


def test_log_an_oraimo_workout_not_in_the_journal():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page()
        p.goto(URL)
        p.wait_for_timeout(300)
        p.click('#tabbtn-watch')
        p.click('#hcNewWrap summary')
        p.fill('#hcn-date', '2026-09-22'); p.fill('#hcn-time', '14:50')
        p.click('#hcn-picker button:has-text("More modes")')
        p.click('#hcn-picker .mode-chip:has-text("Upper limb training")')
        p.click('#hcNewWrap .timer-btn')
        sid = p.evaluate("getStore().sessions.find(s => s.date === '2026-09-22').id")
        for k, v in [('avg', '122'), ('max', '162'), ('warm', '15:30'), ('fat', '1:19:20'), ('aer', '28:40'), ('ana', '2:40')]:
            p.fill(f'#oz-{k}-w-{sid}', v)
        p.click(f'#hra-w-{sid} .oraimo-form .timer-btn')
        s = p.evaluate(f"getStore().sessions.find(s => s.id === '{sid}')")
        assert s['hr']['trimp'] == 271 and s['exercises'][0]['name'] == 'Upper limb training'
        assert s['exercises'][0]['sets'][0]['duration'] == str(126 * 60)       # one timed set so it counts in analysis
        assert s['startedAt']
        p.click('#tabbtn-journal')
        assert 'load 271' in p.inner_text('#tab-journal')
        b.close()


def test_zone_times_typed_on_the_number_pad():
    """The phone's number pad has no ':' — dots (or commas) must work and be shown back as m:ss."""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page()
        p.goto(URL)
        p.wait_for_timeout(300)
        assert p.evaluate("[parseClock('1.19.20'), parseClock('5.50'), parseClock('5,50'), parseClock('42'), parseClock('5.75'), parseClock('abc')]") == [4760, 350, 350, 42, None, None]
        add_session(p, 'nd', 14, 50, 127)
        p.click('#tabbtn-journal')
        p.evaluate("renderHistory(); document.getElementById('sess-nd').classList.add('open'); openOraimoForm('nd')")
        for k, v in [('warm', '15.30'), ('fat', '1.19.20'), ('aer', '28.40'), ('ana', '2.40')]:
            p.fill(f'#oz-{k}-j-nd', v)
        p.click('#oz-avg-j-nd')                       # blur the last field
        assert p.input_value('#oz-fat-j-nd') == '1:19:20'
        p.click('#hra-j-nd .oraimo-form .timer-btn')
        assert p.evaluate("getStore().sessions.find(s => s.id === 'nd').hr.trimp") == 271
        b.close()


def test_oraimo_sport_names_and_old_spelling():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page()
        p.goto(URL)
        p.wait_for_timeout(300)
        modes = p.evaluate("ORAIMO_MODES")
        added = ['Outdoor running', 'Treadmill', 'Flexibility training', 'Boxing', 'Martial arts', 'Push-ups', 'Core training',
                 'Rope skipping', 'Free sparring', 'Aerobics', 'Basketball']
        earlier = ['Outdoor walking', 'Mixed aerobic', 'Strength Training', 'Stretch', 'Lower limb training', 'Upper limb training', 'Climbing machine']
        assert sorted(modes) == sorted(added + earlier) and len(modes) == len(set(modes)) == 18
        assert modes[0] == 'Outdoor walking'
        # modes the app logs natively come after every mode it doesn't, and each maps to exercises the app really has
        native = p.evaluate("Object.keys(ORAIMO_NATIVE)")
        first_native = min(modes.index(m) for m in native)
        assert all(modes.index(m) < first_native for m in modes if m not in native)
        app_ex = set(p.evaluate("[...new Set(Object.values(getAllExerciseNamesByVariant()).flat())]"))
        for mode, exs in p.evaluate("ORAIMO_NATIVE").items():
            assert exs and all(e in app_ex for e in exs), (mode, exs)
        assert p.evaluate("[...document.createRange().createContextualFragment(`<datalist>${ORAIMO_MODES.map(m => `<option value='${m}'>`).join('')}</datalist>`).querySelectorAll('option')].length") == 18
        p.evaluate("""() => { const s = getStore(); s.sessions.push({ id: 'st', date: '2026-09-23', day: 'Wednesday', variant: 'ring', status: 'completed',
          exercises: [{ id: 'e', name: 'Strength training', custom: true, notes: '', sets: [] }] }); s.customExercises.push('Strength training'); saveStore(s); }""")
        s = p.evaluate("getStore()")
        assert s['sessions'][-1]['exercises'][0]['name'] == 'Strength Training'
        assert 'Strength Training' in s['customExercises'] and 'Strength training' not in s['customExercises']
        b.close()


def test_watch_mode_picker_is_buttons_not_a_popup_list():
    """The mode list used to be a <datalist>: on Android its pop-up covered the keyboard and closed itself.
    Now: six likely modes as buttons, the rest one tap away, and a text box only when asked for."""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={'width': 360, 'height': 780})
        errors = []
        p.on('pageerror', lambda e: errors.append(str(e)))
        p.goto(URL); p.wait_for_timeout(300)
        p.click('#tabbtn-watch'); p.click('#hcNewWrap summary')
        assert p.locator('datalist').count() == 0 and p.locator('#hcn-other').count() == 0      # no pop-up list, no keyboard
        chips = lambda: p.locator('#hcn-picker .mode-chip').all_inner_texts()
        assert chips() == ['Outdoor walking', 'Treadmill', 'Rope skipping', 'Basketball', 'Free sparring', 'Outdoor running']
        p.click('#hcn-picker button:has-text("More modes (12)")')
        assert chips() == p.evaluate("ORAIMO_MODES")                                             # same order as the list
        # the six quick modes already cover every mode the app doesn't log, so only the second heading appears
        assert 'ACTIVITIES THE APP ALSO LOGS' in p.inner_text('#hcn-picker').upper() and 'OTHER ACTIVITIES' not in p.inner_text('#hcn-picker').upper()
        p.click('#hcn-picker .mode-chip:has-text("Boxing")')
        assert p.locator('#hcNewWrap').evaluate('d => d.open')                                    # picking doesn't close the form
        assert 'Selected: Boxing' in p.inner_text('#hcn-picker')
        p.click('#hcn-picker button:has-text("Another mode")')
        p.fill('#hcn-other', 'Swimming')
        p.fill('#hcn-time', '07:30')
        p.click('#hcNewWrap .timer-btn')
        assert p.evaluate("getStore().sessions.slice(-1)[0].exercises[0].name") == 'Swimming'
        # modes used before move to the front next time
        p.evaluate("""() => { const s = getStore(); s.sessions.push({ id: 'r1', date: todayISO(), day: 'x', variant: 'ring', status: 'completed',
          exercises: [{ id: 'e', name: 'Lower limb training', custom: true, notes: 'Recorded on the watch', sets: [] }] }); saveStore(s); renderHealthSessions(); }""")
        p.click('#hcNewWrap summary')                                  # the form folds away after creating a workout
        assert p.locator('#hcn-picker .mode-chip').first.inner_text() == 'Lower limb training'
        assert not errors, errors
        b.close()
