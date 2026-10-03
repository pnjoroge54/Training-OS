"""
Nothing sticks out of its card or off the screen, on any tab, with every fold and row open, at 320/360/412 px.
Grid items default to min-width:auto, so a form input can silently push a two-column grid past the card edge
(the gain-plan checkpoint box did exactly that on a 360 px phone).
"""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

URL = (Path(__file__).resolve().parents[1] / 'www' / 'index.html').as_uri()
FIND = """() => {
  const out = [];
  const panel = document.querySelector('.panel.active');
  const vw = document.documentElement.clientWidth;
  panel.querySelectorAll('input, select, textarea, button, label, .info-card, .test-row, table, svg').forEach(el => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;
    if (el.closest('.table-scroll, .chart-wrap, .tabs, [style*="overflow-x"]')) return;
    const card = el.parentElement && el.parentElement.closest('.info-card, .test-row, .oraimo-form, .sess-card');
    const cr = card ? card.getBoundingClientRect() : null;
    if (r.right > vw + 0.5 || (cr && r.right > cr.right + 0.5)) {
      out.push(`${el.tagName.toLowerCase()}#${el.id || ''}.${[...el.classList].join('.')} right=${Math.round(r.right)} card=${cr ? Math.round(cr.right) : '-'} vw=${vw} :: ${(el.getAttribute('aria-label') || el.textContent || el.placeholder || '').trim().slice(0, 40)}`);
    }
  });
  return out;
}"""
SEED = """() => { const s = getStore(); const d = n => { const x = new Date(); x.setDate(x.getDate() - n); return todayISO(x); };
  s.tests = [['c_wrist',1,60],['c_wallhs',42,30],['r_jumpturn_l',270,5],['r_jumpturn_r',180,5],['r_run2400',645,5]].map(([id,v,a],i)=>({id:'s'+i,testId:id,date:d(a),value:v,at:i}));
  s.sessions = [{ id: 'w', date: d(1), day: weekdayName(d(1)), variant: 'ring', status: 'completed', sessionRPE: '7', durationMin: '31',
     exercises: [{ id: 'e', name: 'Outdoor walking', notes: 'Recorded on the watch', sets: [] }] }];
  saveStore(s); const bw = []; for (let i = 20; i >= 0; i -= 2) bw.push({ date: d(i), kg: 70 + (20 - i) * 0.03 }); setBW(bw); }"""
TABS = ['schedule', 'day', 'tests', 'classes', 'warmup', 'cooldown', 'splits', 'handstand', 'journal', 'watch', 'analysis', 'settings']


@pytest.mark.parametrize('width', [320, 360, 412])
def test_no_element_overflows_its_card(width):
    problems = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={'width': width, 'height': 800})
        p.goto(URL); p.wait_for_timeout(300); p.evaluate(SEED); p.reload(); p.wait_for_timeout(300)
        for tab in TABS:
            p.click('#settingsBtn' if tab == 'settings' else f'#tabbtn-{tab}')
            p.wait_for_timeout(100)
            p.evaluate("""() => { const pn = document.querySelector('.panel.active');
              pn.querySelectorAll('details').forEach(d => d.open = true);
              pn.querySelectorAll('.test-row, .ex-card, .proto-card, .sess-card').forEach(r => r.classList.add('open'));
              if (pn.id === 'tab-journal') openOraimoForm('w', 'j'); }""")
            p.wait_for_timeout(100)
            problems += [f'{tab}: {x}' for x in p.evaluate(FIND)]
            if tab == 'settings': p.click('#settingsBtn')
        p.click('#tabbtn-analysis')
        p.evaluate("setPref('gain', null); renderGainPlan(); document.querySelector('[data-fold=an-gain]').open = true")
        problems += [f'gain form: {x}' for x in p.evaluate(FIND)]
        b.close()
    assert not problems, '\n'.join(problems)
