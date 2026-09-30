"""The logo's SVGs, from the outlines wordmark.py wrote (run it first, in the same folder)."""
import math
import pathlib
OUT = pathlib.Path(__file__).resolve().parent.parent / "brand"
INK, FOG, WHITE = "#151722", "#EEF0F6", "#FFFFFF"
LAPIS, LAPIS_ON_DARK = "#3446E0", "#8C98FF"
LEAF_ON_DARK = "#DDA94A"

NINE = open("nine.path").read()
WORD = open("word.path").read()
WIDTH = math.ceil(float(open("word.width").read()))

# --- mark: the name as the part in brackets, the slot a team fills with its own work
BRACKETS = "M9.5 5.5H5.5V26.5H9.5M22.5 5.5H26.5V26.5H22.5"
def glyphs(stroke, nine):
    return (f'<path d="{BRACKETS}" fill="none" stroke="{stroke}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="{NINE}" fill="{nine}"/>')
def mark(stroke, nine):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-label="Gen9">{glyphs(stroke, nine)}</svg>\n'
# --- wordmark: the mark, then gen9 in Instrument Sans semibold
def wordmark(stroke, nine):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} 32" role="img" aria-label="Gen9">{glyphs(stroke, nine)}'
            f'<path d="{WORD}" fill="{stroke}"/></svg>\n')
def tile(rx, scale):
    """The mark in white and gold on a lapis square, scaled about the centre."""
    move = 16 * (1 - scale)
    corner = f' rx="{rx}"' if rx else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-label="Gen9">'
            f'<rect width="32" height="32"{corner} fill="{LAPIS}"/><g transform="translate({move:g} {move:g}) scale({scale:g})">'
            f'{glyphs(WHITE, LEAF_ON_DARK)}</g></svg>\n')

(OUT / "mark.svg").write_text(mark(INK, LAPIS))
(OUT / "mark-on-dark.svg").write_text(mark(FOG, LAPIS_ON_DARK))
(OUT / "wordmark.svg").write_text(wordmark(INK, LAPIS))
(OUT / "wordmark-on-dark.svg").write_text(wordmark(FOG, LAPIS_ON_DARK))
(OUT / "app-icon.svg").write_text(tile(7.5, 0.8))
# Full-bleed square for platforms that apply their own mask (iOS touch icon, maskable PWA icon):
# the mark stays inside the circle of 40% radius that every launcher keeps.
(OUT / "app-icon-maskable.svg").write_text(tile(0, 0.72))
print("lockup", WIDTH, "x 32")
print("NINE_PATH =", NINE)
print("WORD_PATH =", WORD)
