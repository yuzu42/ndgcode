"""A NextDraw script that runs unchanged on the VG-X4 through ndgcode.

    python examples/square_and_text.py               # plot (carriage at Home)
    python -m ndgcode run examples/square_and_text.py --export out.gcode
    python -m ndgcode run examples/square_and_text.py --fake

The text part needs the HersheyFonts package and is skipped without it.
"""

import math

from ndgcode import NextDraw

nd = NextDraw()
nd.interactive()
nd.options.units = 2            # mm
nd.options.speed_pendown = 15

if not nd.connect():
    quit()

# 50 mm calibration square: measure it to check scale and squareness
nd.draw_path([[20, 20], [70, 20], [70, 70], [20, 70], [20, 20]])

# a circle inside it
cx, cy, r = 45, 45, 20
nd.draw_path([[cx + r * math.cos(t / 90 * math.pi), cy + r * math.sin(t / 90 * math.pi)]
              for t in range(181)])

try:
    from HersheyFonts import HersheyFonts
except ImportError:
    HersheyFonts = None

if HersheyFonts is not None:
    font = HersheyFonts()
    font.load_default_font("futural")
    font.normalize_rendering(8)
    font.render_options.yofs = 90
    baseline = font.render_options.yofs + font.render_options.base_line
    for stroke in font.strokes_for_text("ndgcode"):
        # glyphs come y-up; this machine's +y points toward the operator
        nd.draw_path([[x + 20, 2 * baseline - y] for x, y in stroke])

nd.moveto(0, 0)
nd.disconnect()
print(f"pen-down {nd.distance_pendown:.3f} m, {nd.pen_lifts} pen lifts, "
      f"estimated {nd.time_estimate:.0f} s")
