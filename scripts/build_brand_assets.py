"""Build code-native SVG artwork redrawn from the supplied Sistech reference.

This is a clean vector recreation, not the original raster source. Geometry,
colours and the company name/tagline are retained in reusable local assets.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'static' / 'brand'
OUT.mkdir(exist_ok=True)
DEFS = '''<defs>
<linearGradient id="blue" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#124c9c"/><stop offset=".5" stop-color="#00357c"/><stop offset="1" stop-color="#082961"/></linearGradient>
<linearGradient id="roof" x1="0" y1="0" x2="1" y2=".2"><stop stop-color="#f8c323"/><stop offset=".42" stop-color="#f2b914"/><stop offset=".72" stop-color="#164889"/><stop offset="1" stop-color="#063575"/></linearGradient>
<path id="s" d="M120 0H40C15 0 0 16 0 40s15 38 40 38h40c9 0 14 5 14 12s-5 12-14 12H4v28h80c26 0 40-16 40-40s-14-39-40-39H43c-9 0-14-5-14-12s5-11 14-11h77Z"/>
<path id="i" d="M0 0h30v130H0Z"/>
<path id="t" d="M0 0h124v28H77v102H47V28H0Z"/>
<path id="e" d="M124 0H46C17 0 0 22 0 65s17 65 46 65h78v-28H49c-11 0-17-7-19-23h94V51H30c2-16 8-23 19-23h75Z"/>
<path id="c" d="M124 0H47C17 0 0 23 0 65s17 65 47 65h77v-28H50c-14 0-20-12-20-37s6-37 20-37h74Z"/>
<path id="h" d="M0 0h30v51h64V0h30v130H94V79H30v51H0Z"/>
</defs>'''
ROOF = '''<g transform="translate(280 5) scale(1.72)"><path d="M0 90 88 8Q92 4 96 8L186 90H150L92 38 34 90Z" fill="url(#roof)"/><path d="m117 20 19-16q4-4 8 0l96 78-26 9-74-61-7 6Z" fill="url(#blue)"/></g>'''
WORD = '''<g fill="url(#blue)" transform="translate(24 183)"><use href="#s"/><use href="#i" x="146"/><use href="#s" x="198"/><use href="#t" x="344"/><use href="#e" x="490"/><use href="#c" x="636"/><use href="#h" x="782"/></g>'''
CAPTION = '''<text x="480" y="365" text-anchor="middle" fill="#62666c" font-family="Arial, sans-serif" font-weight="700" font-size="38" letter-spacing="3">CEMENT &amp; TMT PVT. LTD.</text><path d="M30 390H930" stroke="#a9adb2" stroke-width="2"/><text x="480" y="428" text-anchor="middle" fill="#62666c" font-family="Arial, sans-serif" font-weight="700" font-size="24" letter-spacing="1">SOLID FOUNDATION, STRONG FUTURE</text><g fill="#f2b914"><path d="m40 411 4 9 10 1-8 7 2 10-8-5-9 5 2-10-7-7 10-1Z"/><path d="m920 411 4 9 10 1-8 7 2 10-8-5-9 5 2-10-7-7 10-1Z"/></g>'''

def svg(viewbox, body):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" role="img" aria-labelledby="title desc"><title id="title">Sistech Cement and TMT Private Limited</title><desc id="desc">Blue Sistech wordmark with golden-yellow and blue roof peaks. Solid foundation, strong future.</desc>{DEFS}{body}</svg>'''

(OUT / 'sistech-logo.svg').write_text(svg('0 0 960 450', ROOF+WORD+CAPTION), encoding='utf-8')
(OUT / 'sistech-logo-compact.svg').write_text(svg('0 0 960 330', ROOF+WORD), encoding='utf-8')
mark = '<g transform="translate(8 10) scale(.98)"><path d="M0 90 88 8Q92 4 96 8L186 90H150L92 38 34 90Z" fill="url(#roof)"/><path d="m117 20 19-16q4-4 8 0l96 78-26 9-74-61-7 6Z" fill="url(#blue)"/></g>'
(OUT / 'sistech-mark.svg').write_text(svg('0 0 256 116', mark), encoding='utf-8')
(OUT / 'favicon.svg').write_text(svg('0 0 256 256', '<rect width="256" height="256" rx="46" fill="#fff"/>'+mark.replace('translate(8 10)', 'translate(8 78)')), encoding='utf-8')
print('Sistech vector brand assets built.')

# Optional authoring dependency only; production serves the bundled PNG.
if __name__ == '__main__':
    import sys
    if '--png' in sys.argv:
        import cairosvg
        cairosvg.svg2png(url=str(OUT / 'sistech-logo.svg'), write_to=str(OUT / 'sistech-logo-email.png'), output_width=960, output_height=450)
        print('Inline email PNG rendered from the vector logo.')
