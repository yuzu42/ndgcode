# ndgcode

The [Bantam Tools NextDraw](https://bantam.tools/nd_py) Python API for **GRBL
G-code pen plotters**, developed on a **VigoTec VG-X4**. Existing NextDraw
scripts run with one changed import:

```python
# from nextdraw import NextDraw
from ndgcode import NextDraw

nd = NextDraw()
nd.interactive()
nd.options.units = 2                 # mm
if not nd.connect():                 # pen up, pull off from the Home corner
    quit()
nd.draw_path([[20, 20], [60, 20], [60, 60], [20, 60], [20, 20]])
nd.disconnect()                      # pen up, park in the corner, motors off
```

Everything in the NextDraw API that can be mapped onto a GRBL machine is
mirrored: the interactive functions, all general options, the plot context
with its modes and utility commands, the variables and the `errors` object.
[SPEC.md](SPEC.md) lists every item and how it maps.

> **Status:** 0.1 development version. Everything is tested against a
> simulated GRBL board (82 unit tests). Validation on the real machine is
> still pending: see [docs/hardware-checklist.md](docs/hardware-checklist.md).
> Values marked *provisional* (bed size, speed cap) are cautious
> placeholders until they're measured.

## Install

Python ≥ 3.9 and `pyserial`:

```sh
pip install -e .            # from this folder
```

SVG plotting (`plot_setup` / `plot_run`) additionally needs the NextDraw
Python API itself. ndgcode uses it to digest SVGs (layers, path ordering,
hidden-line removal) without any NextDraw hardware. Download and install it
from Bantam Tools; it is not redistributed here.

## Before the first plot (VG-X4)

1. **Home corner:** push the carriage into the **far-left corner** (left motor
   side, seen from where you stand) against both stops *before every
   connection*. The board resets whenever the USB port opens, so the position
   where you connect is (0, 0). `connect()` then pulls off 5 mm from the stops
   (the carriage rubs on them otherwise) and calls that point (0, 0).
2. **Frame:** NextDraw coordinates: +x to your right, +y **toward you**.
3. **Pen:** the pen is down at servo `S1000`. Move the servo there
   (`ndgcode shell`, then `pen down`), insert the pen so the tip rests on the
   paper, and calibrate the lift with `calibrate pen`.
4. `disconnect()` parks the carriage back in the corner, so the next session
   starts at Home again.

## Command line

```sh
python -m ndgcode ports                 # CH340 ports (never touches other USB devices)
python -m ndgcode info                  # firmware + key settings, no motion
python -m ndgcode shell                 # interactive: pen, jog, goto, square, calibrate, ...
python -m ndgcode run my_script.py      # run a NextDraw script unchanged on the plotter
python -m ndgcode run my_script.py --export out.gcode   # ... or write G-code instead
python -m ndgcode plot drawing.svg --preview            # time estimate, no hardware
python -m ndgcode plot drawing.svg --layer 2
python -m ndgcode stream file.gcode     # stream existing G-code (Ctrl-C stops safely)
python -m ndgcode check file.gcode      # validate in GRBL check mode, nothing moves
```

Add `--fake` to `info`, `shell`, `run`, `plot`, `stream` or `check` to use the
built-in simulated board.

## Choosing the output

`nd.options.port` selects where G-code goes:

| value | output |
|---|---|
| `None` (default) | auto-detect the plotter's CH340 USB serial port |
| `"/dev/cu.usbserial-120"`, `"COM3"` | that serial port |
| a nickname | the plotter with that `$I=` name (see `write_name`) |
| `"file:out.gcode"` | write a G-code file (start with the carriage at Home) |
| `"null:"` | nothing; distances and `time_estimate` only |
| `"fake:"` | the simulated GRBL board |

## Beyond NextDraw

Interactive extras: `status()`, `grbl_settings()`, `gcode(line)`, `home()`,
`set_home()`, `walk_home()`, `jog()` / `jog_cancel()`, `pause()` / `resume()`,
`stop()`, `feed_override(percent)`. The guided calibration is in
`ndgcode.calibrate`, including a **bed height map**: the VG-X4 bed is not
flat, and the map adapts the pen-up height per position.

## Configuration

`nd.load_config("my_conf.py")` reads a file in the format of
`nextdraw_conf.py` (plain assignments). Option names go to `nd.options`,
everything else to `nd.params`. See
[ndgcode_conf.example.py](ndgcode_conf.example.py) for the machine
parameters (bed size, pen servo values, speed cap, homing).

## Differences from NextDraw

- Every connection resets the GRBL board, so the position isn't kept
  between `connect()` calls or `plot_run()` calls. Plot-context utilities
  like `walk_home` / `set_home` can't work across runs. Use one interactive
  session (or `ndgcode shell`) instead.
- GRBL plans acceleration itself. `accel` writes `$120/$121` (only when the
  value changes) and `handling` scales speed and acceleration
  approximately.
- `pen_rate_raise/lower` below 75 are emulated by stepping the servo.
- `usb_command` / `usb_query` take G-code and `$` commands, not EBB
  commands.
- Not applicable: `model`, `penlift`, `bootload` and voltage checks. These
  are accepted with a warning.

## Tests

```sh
python -m unittest discover -s tests -t .
```

The plot-context tests run when the `nextdraw` library is installed and are
skipped otherwise.

## Safety notes for the VG-X4

- The vendor ships a laser firmware next to the writer firmware. **Never
  flash the laser firmware** onto a pen plotter.
- `$1=255` (motors stay energized) is recommended, so the carriage can't be
  pushed out of position. ndgcode warns if it's not set.
- The speed is capped at 1000 mm/min (`feed_cap_mm_min`) until the long-run
  stalling seen on the test machine is resolved.

## License

[MIT](LICENSE). This covers ndgcode itself only. The NextDraw Python API
(needed for SVG plotting) has its own license from Bantam Tools and is not
included here.

NextDraw is a trademark of Bantam Tools; this project is not affiliated with
Bantam Tools or VigoTec.
