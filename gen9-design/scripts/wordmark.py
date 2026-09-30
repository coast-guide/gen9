"""Outlines from Instrument Sans for the logo, in the mark's own 32-unit box: the 9 inside the
brackets (bold), and the word gen9 that stands beside the mark in the lockup (semibold).
Writes nine.path, word.path and word.width to the current folder; brand.py reads them."""
import pathlib
import re
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

FONT = pathlib.Path(__file__).resolve().parent.parent / "fonts" / "InstrumentSans-Variable.woff2"

def instance(weight):
    return instantiateVariableFont(TTFont(FONT), {"wght": weight, "wdth": 100})

def short(path):
    """Two decimals are a fiftieth of a pixel at 32 px: enough, and a third of the bytes."""
    return re.sub(r"-?\d+\.\d+", lambda m: f"{float(m.group(0)):.2f}".rstrip("0").rstrip("."), path)

# The 9: 13.5 units tall, centred between the brackets, its foot on y = 22.9
font = instance(700)
glyphs, name = font.getGlyphSet(), font.getBestCmap()[ord("9")]
box = BoundsPen(glyphs); glyphs[name].draw(box)
xmin, ymin, xmax, ymax = box.bounds
scale = 13.5 / (ymax - ymin)
pen = SVGPathPen(glyphs)
glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, 16 - (xmin + xmax) / 2 * scale, 9.4 + ymax * scale)))
open("nine.path", "w").write(short(pen.getCommands()))

# The word: 26 units to the em, 8 units after the mark, on the baseline a 26 px line centred on
# the mark gives it (ascent 970, descent 250: the baseline sits at 25.36)
font = instance(600)
glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
scale, baseline, x = 26 / font["head"].unitsPerEm, 25.36, 40.0
pen, box = SVGPathPen(glyphs), BoundsPen(glyphs)
for ch in "gen9":
    name = cmap[ord(ch)]
    glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
    glyphs[name].draw(TransformPen(box, (scale, 0, 0, -scale, x, baseline)))
    x += (hmtx[name][0] - 30) * scale                     # tracking -0.03 em, as the app sets its headings
open("word.path", "w").write(short(pen.getCommands()))
open("word.width", "w").write(f"{box.bounds[2]:.2f}")
print("nine", len(open("nine.path").read()), "bytes; word", len(open("word.path").read()), "bytes; lockup width", open("word.width").read())
