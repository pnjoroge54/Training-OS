"""Glute Bridges appear in the Warm-up tab (Monday and Thursday drills, shared by every program);
their reference video must be the YouTube Short chosen for it."""
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = (Path(__file__).resolve().parents[1] / 'www' / 'index.html').as_uri()
WANTED = 'https://www.youtube.com/shorts/GI5BtRDTuyc'


def test_glute_bridges_point_to_the_chosen_short():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page()
        errors = []
        p.on('pageerror', lambda e: errors.append(str(e)))
        p.goto(URL)
        p.wait_for_timeout(400)
        found = p.evaluate("""() => { const out = [];
          Object.entries(warmupSpecific).forEach(([day, drills]) => drills.forEach(ex => {
            if (ex.name === 'Glute Bridges') out.push({ day, video: ex.video, html: mediaSlotHTML(ex.name, ex.video) }); }));
          return out; }""")
        assert sorted(f['day'] for f in found) == ['mon', 'thu'], found
        for f in found:
            assert f['video'] == WANTED, f
            assert f'href="{WANTED}"' in f['html'] and 'Reference video' in f['html']
        assert not errors, errors
        b.close()
