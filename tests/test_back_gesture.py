"""
Android back gesture (edge swipe). Capacitor finishes the activity on back unless
the App plugin has a 'backButton' listener; this checks the app installs one and
that back goes to the Schedule tab before leaving the app.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = (Path(__file__).resolve().parents[1] / 'www' / 'index.html').as_uri()
FAKE = r"""
window.__back = null; window.__minimized = 0;
window.Capacitor = { isNativePlatform: () => true, Plugins: {
  App: { addListener: (ev, cb) => { if (ev === 'backButton') window.__back = cb; return Promise.resolve({ remove() {} }); },
         minimizeApp: async () => { window.__minimized++; }, exitApp: async () => {} } } };
"""


def test_back_returns_to_schedule_before_leaving():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context()
        ctx.add_init_script(FAKE)
        p = ctx.new_page()
        errors = []
        p.on('pageerror', lambda e: errors.append(str(e)))
        p.goto(URL)
        p.wait_for_timeout(500)
        assert p.evaluate("typeof window.__back") == 'function'
        p.click('#tabbtn-journal')
        p.evaluate("window.__back({ canGoBack: false })")
        assert p.evaluate("document.querySelector('.tab.active').id") == 'tabbtn-schedule'
        assert p.evaluate("window.__minimized") == 0
        p.evaluate("window.__back({ canGoBack: false })")
        p.wait_for_timeout(100)
        assert p.evaluate("window.__minimized") == 1
        assert not errors, errors
        b.close()
