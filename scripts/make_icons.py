"""Render Training OS launcher icons and splash screens from one SVG mark (run once; output is committed).

Mark: a boxing glove inside a ring. The ring is the roda, the circle capoeira is played in; the glove is
the boxing. Ember gradient (the app's own amber to pink) on the app's near-black.
Adaptive icon: 108 dp canvas, everything inside the 66 dp safe zone (radius 33) so any launcher mask shows it whole.
"""
import asyncio, struct
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'resources' / 'android'
BG, BG_SPLASH = '#15152A', '#0A0A0F'
AMBER, PINK, CUFF, INK = '#F5A623', '#E8417A', '#EFE9DD', '#15152A'

def mark():
    return f'''<defs>
        <linearGradient id="ember" x1="0.1" y1="0" x2="0.9" y2="1"><stop offset="0" stop-color="{AMBER}"/><stop offset="1" stop-color="{PINK}"/></linearGradient>
        <linearGradient id="ring" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{AMBER}"/><stop offset="1" stop-color="{PINK}"/></linearGradient>
      </defs>
      <circle cx="54" cy="54" r="31.5" fill="none" stroke="url(#ring)" stroke-width="3.2" stroke-linecap="round" stroke-dasharray="172 26" transform="rotate(-18 54 54)"/>
      <!-- glove: body, thumb, knuckle seam, cuff (turned to punch toward the upper right) -->
      <g transform="rotate(48 54 54)">
      <path d="M37 60 C31 49 33 37 45 33.5 C57 30 68 34 70.5 45 C72 52 70 58 66 61.5 L66 65 L40 65 Z" fill="url(#ember)"/>
      <path d="M38.5 57 C32 55.5 30.5 47 36 43.5 C41 41 46 45.5 45.5 51 C45 55.5 42 58 38.5 57 Z" fill="{PINK}"/>
      <path d="M38.5 57 C32 55.5 30.5 47 36 43.5 C41 41 46 45.5 45.5 51 C45 55.5 42 58 38.5 57 Z" fill="none" stroke="{INK}" stroke-opacity="0.35" stroke-width="1.4"/>
      <path d="M52 36.5 C60 36 65.5 40.5 66.5 47.5" fill="none" stroke="{INK}" stroke-opacity="0.28" stroke-width="1.6" stroke-linecap="round"/>
      <rect x="39" y="65" width="28" height="11" rx="3.2" fill="{CUFF}"/>
      <line x1="39" y1="69.2" x2="67" y2="69.2" stroke="{INK}" stroke-opacity="0.22" stroke-width="1.2"/>
      </g>'''

def svg(size, bg=None, shape='square', scale=1.0):
    bgel = ''
    if bg and shape == 'square': bgel = f'<rect width="108" height="108" rx="24" fill="{bg}"/>'
    if bg and shape == 'circle': bgel = f'<circle cx="54" cy="54" r="54" fill="{bg}"/>'
    t = f'translate(54 54) scale({scale}) translate(-54 -54)'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 108 108">{bgel}<g transform="{t}">{mark()}</g></svg>'

DENS = {'mdpi': 1, 'hdpi': 1.5, 'xhdpi': 2, 'xxhdpi': 3, 'xxxhdpi': 4}

async def shot(page, markup, w, h, path):
    await page.set_viewport_size({'width': w, 'height': h})
    await page.set_content(f'<html><body style="margin:0;background:transparent">{markup}</body></html>')
    path.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(path), omit_background=True, clip={'x': 0, 'y': 0, 'width': w, 'height': h})

async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(); p = await b.new_page()
        for d, k in DENS.items():
            leg, fg = int(48 * k), int(108 * k)
            base = OUT / 'res' / f'mipmap-{d}'
            await shot(p, svg(leg, BG, 'square', scale=1.3), leg, leg, base / 'ic_launcher.png')
            await shot(p, svg(leg, BG, 'circle', scale=1.3), leg, leg, base / 'ic_launcher_round.png')
            await shot(p, svg(fg, None), fg, fg, base / 'ic_launcher_foreground.png')
        res = ROOT / 'android' / 'app' / 'src' / 'main' / 'res'
        for f in sorted(res.glob('drawable*/splash.png')):
            head = open(f, 'rb').read(24); w, h = struct.unpack('>II', head[16:24])
            m = int(min(w, h) * 0.36)
            await shot(p, f'<div style="width:{w}px;height:{h}px;background:{BG_SPLASH};display:grid;place-items:center">{svg(m, None)}</div>', w, h, OUT / 'res' / f.parent.name / 'splash.png')
        await shot(p, '<div style="display:flex;gap:16px;padding:16px;background:#3a3a44">' + svg(192, BG, 'square', scale=1.3) + svg(192, BG, 'circle', scale=1.3) +
                   f'<div style="width:192px;height:192px;background:{BG};border-radius:40px;overflow:hidden">{svg(192, None)}</div>' + svg(48, BG, 'square', scale=1.3) + '</div>', 830, 224, OUT / 'preview.png')
        await b.close()
    (OUT / 'res' / 'values').mkdir(parents=True, exist_ok=True)
    (OUT / 'res' / 'values' / 'ic_launcher_background.xml').write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_launcher_background">{BG}</color>\n</resources>\n')
    print('icons and splash written to', OUT)

asyncio.run(main())

