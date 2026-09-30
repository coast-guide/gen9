"""Render the PNG sizes and favicon.ico from the app icons (CairoSVG, Pillow)."""
import io, pathlib
import cairosvg
from PIL import Image

BRAND = pathlib.Path(__file__).resolve().parent.parent / "brand"
def png(svg, size):
    return cairosvg.svg2png(url=str(BRAND / svg), output_width=size, output_height=size)

for size in (16, 32, 48, 192, 512):
    (BRAND / "png" / f"icon-{size}.png").write_bytes(png("app-icon.svg", size))
(BRAND / "png" / "icon-maskable-512.png").write_bytes(png("app-icon-maskable.svg", 512))
(BRAND / "png" / "apple-touch-icon.png").write_bytes(png("app-icon-maskable.svg", 180))
largest = Image.open(io.BytesIO(png("app-icon.svg", 48)))
largest.save(BRAND / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
