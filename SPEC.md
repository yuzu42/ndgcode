# penplot-to-gcode — SPEC

A Python package that keeps the **Bantam Tools NextDraw Python API** as the
programming interface, but drives a **VigoTec VG-X4** (GRBL G-code plotter)
instead of a NextDraw. Existing scripts should run with one changed import:

```python
# from nextdraw import NextDraw
from ndgcode import NextDraw          # working name: "ndgcode"

nd = NextDraw()
nd.interactive()
nd.options.units = 2                  # mm
nd.connect()                          # pen up, "home" = current position
nd.draw_path([[20, 20], [60, 20], [60, 60], [20, 60], [20, 20]])
nd.disconnect()
```

Goal: mirror **everything in the NextDraw API that can be mapped onto this
machine**, including functions no project script uses yet. Add comfort
features (homing, jogging, pause/resume, G-code export) where GRBL makes
them possible. Anything that can't be mapped is accepted without breaking
the script, issues one warning, and is listed in §5.

Status: draft, 2026-11-25. Machine facts in §1 were measured on the
connected VG-X4.

Environment: Python ≥ 3.9 with `pyserial`. SVG plotting also needs the
Bantam Tools NextDraw Python API (`nextdraw`), installed separately. The
development machine uses Python 3.14, pyserial 3.5 and nextdraw 1.x.

---

## 1. Hardware facts (verified 2026-11-25)

| Item | Value | Source |
|---|---|---|
| Port | `/dev/cu.usbserial-120`, 115200 baud, 8N1 | USB scan |
| Firmware | `VigoWriter 1.1f` = GRBL 1.1f fork, `[VER:1.1f.20170131:]` | `$I` |
| Build options | `[OPT:VC,15,128]`: **CoreXY in firmware** (send plain Cartesian X/Y), variable spindle PWM, 15-block planner, 128-byte RX buffer | `$I` |
| Homing / limits | `$22=0` homing off, `$21=0` hard limits off, `$20=0` soft limits off. Limit switches: unknown | `$$` |
| Travel | `$130/$131=1000` is a firmware placeholder. **Real bed size: not measured yet** | `$$` |
| Max rate | `$110/$111=5000` mm/min (≈ 83 mm/s) | `$$` |
| Acceleration | `$120/$121=400` mm/s² | `$$` |
| Idle motor hold | `$1=25`: motors **release** 25 ms after idle, so the carriage can be pushed out of position | `$$` |
| Direction invert | `$3=3` (X and Y inverted in firmware) | `$$` |
| Spindle range | `$30=1000`, `$31=0`, laser mode `$32=0` | `$$` |
| Reset on open | Opening the port resets the board (banner is printed again); MPos returns to 0,0,0 | observed |

| Idle motor hold (now) | **`$1=255`** since 2026-11-25: motors stay energized | written, verified |
| Axes | GRBL **+X = operator's right** (same as NextDraw); GRBL **+Y = away from operator** (opposite) → `flip_y` | jog test |
| Scale | X: 100 mm commanded ≈ 100.0 mm drawn. Y: 100 → 99.1 mm (within dot/ruler error; re-check with a 150 mm square) | dot test |
| Limit switches | none visible | inspection |
| `$SLP`, `$C` | both supported | tested |
| Reset on open | unavoidable in software (macOS re-enables `hupcl`; pyserial DTR/RTS low doesn't help) | tested |
| Edge friction | Right at the back stop, X moves rub and stall. **5 mm pull-off** off the stop runs smoothly at `F2000` | tested |
| **Motion fault (open)** | A 300 mm X run at `F1500` along Y = −5 jerked **periodically**. A jog at `F3000` lost ~160 of 300 mm (growling = stall). Short 50 mm moves at X ≈ 140–190 were smooth at `F2000`; single-motor diagonals were smooth up to ~1980 mm/min motor speed. The jerks are felt on the right motor. Unresolved, see §8. | tests |
| Bed flatness | The tip leaves the paper at `S800` mid-bed but only at `S550`–`S600` about 90 mm toward the operator (`S550` ≈ 2 mm clearance there). **The bed is not flat**; the pen is firmly mounted. | pen test |

### Pen lift: servo on spindle PWM (verified step by step)

| Command | Pen |
|---|---|
| `M5` (also the state after power-on or reset) | fully up, the rest position |
| `M3 S100` | nearly up |
| `M3 S500` | about halfway |
| `M3 S700` | **pen up for travel (chosen default)** |
| `M3 S800` | just barely off the paper |
| `M3 S850` … `S950` | still touching |
| `M3 S1000` | fully down: **pen-down and pen-insertion position** |
| `G0 Z…` | no effect on the pen |

- The pen moves roughly in proportion to S; higher S means lower.
- Calibration procedure (user's choice): move to `S1000`, insert the pen so
  the tip rests on the paper, then configure only the lift height in software.
- **Servo pause** after a pen move (`G4` dwell, tapping in place): 0.5, 0.25,
  0.15 and 0.1 s were complete; 0.05 s was too fast.
  **Default: 0.15 s.** Re-check on real strokes.

---

## 2. Architecture

```
user script ── NextDraw-compatible facade (ndgcode.NextDraw)
                 │  options / params / errors / variables, turtle + machine state
                 ▼
               Planner: units → mm, frame mapping, travel-bounds clipping,
                 │      pen state, speed/feed, time & distance accounting
                 ▼
               G-code emitter (pure, deterministic, unit-testable)
                 │
                 ├── SerialBackend: GRBL streaming, ok/error handling,
                 │                  status polling, real-time commands
                 ├── FileBackend:   write .gcode (dry run / export)
                 └── NullBackend:   preview / estimate only
Plot context:  SVG ──(real nextdraw library, digest=2 → "Plob")──▶ polylines ──▶ Planner
```

Design decisions:

1. **API-level facade, not EBB-protocol translation.** The NextDraw driver's
   low-level step and timing commands are specific to the NextDraw's belt
   layout and acceleration. Translating them would produce thousands of tiny
   moves that stutter in GRBL's 15-block planner. GRBL plans acceleration itself.
2. **ndsim does not apply** to this machine: it imitates the NextDraw's EBB
   board. Scripts that keep to the shared API still run against ndsim, which
   gives a geometry preview in NextDraw dimensions only.
3. **The emitter is pure:** the same calls always give byte-identical G-code.
   This allows golden-file tests without hardware.
4. **The plot context reuses NextDraw's own SVG pipeline** (Plob digest; §4.2).
   Layers, reordering, hidden-line removal, auto-rotate, clipping and path
   joining are not re-implemented.

---

## 3. Coordinate frame, homing, travel bounds

**Frame.** Use NextDraw's frame so existing scripts and the Hershey y-flip in
`_learnings/nextdraw-hershey-basics.md` keep working:

- origin (0, 0) is the Home corner, top left as seen by the operator;
- +x points to the operator's right;
- +y points from the far edge toward the operator.

The planner maps this frame onto GRBL axes with a configurable `axis_map`:
`flip_x`, `flip_y`, `swap_xy`. The values are not known yet and get measured
in milestone M1.

**Homing strategies** (`params.homing_strategy`):

| Strategy | Behavior on `connect()` | When |
|---|---|---|
| `"manual"` (default) | Assumes the carriage was pushed into the Home corner against both stops. The port reset makes MPos 0,0. The wrapper then **pulls off** `params.pull_off_mm` (5) in +x and +y at `F500`, so it doesn't rub on the stops (§1), and defines **that** point as (0,0). Same as NextDraw's manual homing (`options.homing = False`). | No limit switches (VG-X4) |
| `"switches"` | Sends `$H`, then applies a configured offset from the switch corner to (0,0). Needs `$22=1`, `$23`, `$27` on the board. | Only if switches exist |

Comfort features around homing:

- `params.park_on_disconnect = True` (default): `disconnect()` raises the pen
  and returns the carriage to (0,0), so the next session starts at Home.
- `set_home()` makes the current position the new (0,0), tracked by the
  wrapper as a work offset. `walk_home()` returns there.
- Jogging (`jog()`, CLI) moves the carriage to the corner or a paper mark
  before `set_home()`.

**Travel bounds.** These are `params.travel_x_mm` and `params.travel_y_mm`
(to be measured, M1). Clipping copies NextDraw:

- Movement is clipped at the bounds.
- `turtle_pos()` keeps the requested position; `current_pos()` reports the
  clipped one.
- `draw_path()` splits a path into separate pen-down pieces where it leaves
  and re-enters the bounds.
- `params.auto_clip_lift` controls pen lifts at the bounds for single moves.
- `walk_*` commands are not clipped, as in NextDraw.
- GRBL soft limits stay off unless homing switches exist.

---

## 4. API mirror

Status legend:

- **Full**: same behavior.
- **Own**: same behavior, different mechanism.
- **Approx**: approximated, with the difference noted.
- **N/A**: accepted, warns once, no effect.

### 4.1 Interactive functions

| Function | Status | Mapping |
|---|---|---|
| `interactive()` | Full | Initialize state and options |
| `connect()` | Own | Open port (or `options.port`, or auto-detect), wait for banner, preamble `G21 G90 G17 G94`, pen up, homing strategy, `connected=True`. Returns `bool`. |
| `disconnect()` | Own | `block()`, pen up, optional park, `M5`, close port |
| `update()` | Full | Apply options: feeds, pen S values, acceleration (§4.3) |
| `goto(x, y)` / `go(dx, dy)` | Full | `G1 X Y F…`, with feed set by the current pen state; no pen change |
| `moveto` / `move` | Full | Pen up if needed, `G1 … F{penup}` |
| `lineto` / `line` | Full | Pen down if needed, `G1 … F{pendown}` |
| `penup()` / `pendown()` | Full | `M3 S{up/down}` + `G4 P{dwell}` (§6) |
| `draw_path(vertices)` | Full | Clip → pen up → move to start → pen down → path → pen up |
| `delay(ms)` | Full | `G4 P{ms/1000}`, queued like motion |
| `block()` | Own | Wait until every line is acknowledged and status is `Idle` |
| `current_pos()` / `turtle_pos()` | Full | Tracked by the wrapper; optional check against `?` WPos |
| `current_pen()` / `turtle_pen()` | Full | Tracked |
| `usb_command(cmd)` / `usb_query(q)` | Own | Pass raw **G-code / `$`** lines; the query returns the response lines. NextDraw's EBB syntax does not apply. Same "advanced, use with care" warning. |
| `load_config(path)` | Full | Python config file in the `nextdraw_conf.py` format plus our params (§7) |

### 4.2 Plot functions

| Function | Status | Mapping |
|---|---|---|
| `plot_setup(svg=None)` | Own | Keep the SVG; create an internal real `nextdraw.NextDraw` with bounds set to the VG-X4 |
| `plot_run(output=False)` | Own | Run the selected `mode`. For plotting: get the Plob from the real library (`digest=2`, no hardware), read its polylines, stream them through the planner. `output=True` returns SVG with our resume data embedded. |

**Verified 2026-11-25** (conda env `studio4`, no NextDraw connected,
`errors.code` 0): `digest=2` returns a Plob without hardware. Format:

- one `<g inkscape:groupmode="layer">` per layer, with its **original label
  kept**: `"1 outlines +S30 +H20"`, `"!2 circle"`;
- inside each layer, `<polyline points="x,y …">` in **inches** from the page
  origin. Rects and circles become vertex lists; transforms are applied.
- an `<nd:plotdata … model="8" …>` metadata element.

Consequences:

- The layer codes (`+S` speed, `+H` height, `!` pause before the layer) are
  not applied by the digest. We parse them from the label and apply them
  ourselves.
- The digest is built with the bounds of NextDraw model 8. Pass the VG-X4
  travel through `params`/overrides so clipping and auto-rotate use the right
  bed.
- Fallback if this ever breaks: vpype for parsing and sorting, without
  hidden-line removal.

### 4.3 General options (both contexts)

| Option | Default here | Status | Mapping |
|---|---|---|---|
| `speed_pendown` (1–100) | 25 | Full | F = % × `params.max_feed_mm_min` (5000) → 1250 mm/min |
| `speed_penup` (1–100) | 75 | Full | Pen-up moves use `G1` with F = %, not `G0`, so this option applies → 3750 mm/min |
| `accel` (1–100) | 75 | Approx | % × `params.max_accel_mm_s2` (400) written to `$120/$121`. GRBL stores these permanently in EEPROM, so the wrapper writes only when the value changed. `params.write_accel=False` disables writing. |
| `pen_pos_down` (0–100 %) | **0** | Full | S = 10 · (100 − h) → `S1000` |
| `pen_pos_up` (0–100 %) | **30** | Full | → `S700` |
| `pen_rate_lower` / `pen_rate_raise` | 50 / 75 | Approx | The servo jumps straight to its target. Rates below 100 are emulated by stepping S in increments with short dwells. Rates ≥ about 75 jump directly. |
| `handling` (1–4) | 1 | Approx | Presets that scale feeds, accel and `$11` junction deviation. Mode 4 (constant speed) uses maximum accel; GRBL cannot turn acceleration off. |
| `model` | — | N/A | Only one machine. Bounds come from params (§3). |
| `penlift` | — | N/A | |
| `homing` | True | Own | Runs the configured homing strategy (§3); `False` means manual |
| `port` | None | Own | Serial path, or None to auto-detect (scan serial ports and probe for a `Grbl`/`VigoWriter` banner). Matches the nickname from `read_name`. A `"file:out.gcode"` value selects the FileBackend. |
| `port_config` (0–2) | 0 | Approx | 0/1: first matching machine; 2: only the named one |
| `units` (interactive: 0 in, 1 cm, 2 mm) | 0 | Full | Converted to mm internally; G-code is always `G21` |

> ⚠️ NextDraw's default pen heights (up 60, down 40) mean S400/S600 here.
> **S600 does not touch the paper**, so the defaults are changed to 30/0.
> Scripts that set NextDraw-tuned pen heights explicitly would draw nothing;
> the wrapper warns if `pen_pos_down` maps to an S value below
> `params.s_contact` (800).

### 4.4 Plot-context modes (`options.mode`)

| Mode | Status | Mapping |
|---|---|---|
| `plot` | Own | Plob → stream |
| `layers` (+ `layer`) | Own | The Plob already filters to the selected layers |
| `cycle` | Full | Pen down, dwell, pen up |
| `align` | Approx | Pen up, motors off (see `disable_xy`) |
| `find_home` | Own | Homing strategy; `"manual"` only reports that no switches exist |
| `utility` (+ `utility_cmd`) | — | §4.5 |
| `sysinfo` | Own | Port, `$I` firmware/build options, key `$$` values |
| `version` | Full | Package version |
| `res_plot` | Own | Resume from progress stored in the output SVG of a paused `plot_run(True)` |
| `offset_start` | Own | Start at the distance `dist` along the plot |

### 4.5 Utility commands (`options.utility_cmd`)

| Command | Status | Mapping |
|---|---|---|
| `lower_pen` / `raise_pen` / `toggle` | Full | Pen S + dwell |
| `walk_x` / `walk_y` (in), `walk_mmx` / `walk_mmy` (mm) | Full | `$J=G91 G21 X… F…` (jog; unclipped like NextDraw) |
| `walk_home` | Full | Pen up, move to (0,0) |
| `set_home` | Own | Current position becomes (0,0) (wrapper offset, optional `G92`) |
| `enable_xy` / `disable_xy` | Approx | GRBL has no direct motor on/off command. Disable via `$SLP` (if VigoWriter supports it; wake needs a reset) or by relying on `$1`. Position counts as lost afterwards. |
| `res_read` / `res_adj_in` / `res_adj_mm` | Own | Read or adjust our resume data (`res_dist`) |
| `strip_data` | Own | Remove our resume and preview data from the SVG |
| `list_names` | Own | Probe serial ports; `name_list` = nicknames or ports |
| `read_name` / `write_name` | Own | `$I` / `$I=<name>` (stored on the board) |
| `bootload` | N/A | Firmware flashing is out of scope |

### 4.6 Plot-context options

| Option | Status | Notes |
|---|---|---|
| `dist` | Full | For walk commands, `offset_start`, `res_adj_*` |
| `layer` | Full | Via Plob |
| `copies` (0 = continuous), `page_delay` | Own | Loop; between copies: pen up, park, wait (interruptible) |
| `auto_rotate`, `reordering`, `random_start`, `hiding` | Full | Done by the NextDraw library while building the Plob |
| `preview` | Own | No hardware. Computes estimates; optional rendered layer in the output SVG. |
| `rendering` | Own | Controls the preview render (pen-down only / pen-up too) |
| `report_time` | Own | Prints `time_elapsed`, `distance_pendown`, `distance_total`, `pen_lifts` |
| `digest` | Full | Passes the Plob through |
| `webhook`, `webhook_url` | Own | HTTP POST when the plot ends or pauses (stdlib `urllib`) |

### 4.7 Additional `params`

| Param | Status | Notes |
|---|---|---|
| `pen_delay_up` / `pen_delay_down` (ms) | Full | Added on top of the servo pause `params.servo_settle_ms` (150) |
| `auto_clip_lift` | Full | §3 |
| `clip_to_page`, `min_gap` | Full | Via Plob |
| `report_lifts`, `bounds_tolerance`, `preview_paths`, `preview_color_up/down` | Full | |
| `skip_voltage_check`, `use_b3_out`, `native_res_factor`, `max_step_rate`, `speed_lim_xy_*`, `button_interval`, `check_updates` | N/A | Replaced by the VG-X4 params in §7 |
| `servo_timeout` | N/A | `M5` lifts the pen fully here instead of switching the servo off, so the behavior differs |

### 4.8 Variables

| Variable | Status | Source |
|---|---|---|
| `version_string` | Full | Package version |
| `connected` | Full | |
| `fw_version_string` | Own | `$I` (`VigoWriter 1.1f` / `[VER:…]`) |
| `nickname` | Own | `$I=` user string |
| `time_elapsed` | Full | Measured |
| `time_estimate` | Approx | Own trapezoidal motion model plus pen dwells. GRBL's planner differs slightly; calibrate against real runs. |
| `distance_pendown`, `distance_total`, `pen_lifts` | Full | Computed |
| `res_dist`, `name_list` | Own | §4.5 |

### 4.9 Errors (`errors.*`, `errors.code`, `keyboard_pause`)

| Code | Condition here |
|---|---|
| 101 connect | Port missing, no banner, or not GRBL |
| 102 button | No pause button on the VG-X4. Raised when GRBL enters `Hold` from an external feed-hold or our own `pause()`. |
| 103 keyboard | `keyboard_pause=True`: Ctrl+C → feed hold `!`, stop streaming, pen up, keep resume data |
| 104 disconnect | Serial error, or an unexpected banner (the board reset mid-job) |
| 105 power | Approx: an unexpected reset banner or alarm after power loss |
| 106 homing | `$H` failed (`ALARM:` 6–9) |

GRBL `error:N` and `ALARM:N` responses are mapped to Python exceptions with
the GRBL message text. The `errors.*` flags decide whether they raise or
only set `errors.code`.

---

## 5. Comfort features beyond NextDraw

Interactive extensions, all optional:

| Call | G-code / GRBL |
|---|---|
| `home()` | Homing strategy |
| `set_home()` / `walk_home()` | §3, also available outside plot mode |
| `jog(dx, dy, feed=None)` / `jog_cancel()` | `$J=…` / real-time `0x85` |
| `pause()` / `resume()` | `!` / `~`, with pen handling |
| `stop()` | Soft reset `0x18`, then `M5`. Emergency; position counts as lost. |
| `feed_override(percent)` | Real-time `0x90`–`0x9D` (10 % / 1 % steps) |
| `status()` | `?` → state, MPos/WPos, buffer, feed, override |
| `grbl_settings()` | `$$` parsed into a dict, read-only |
| `gcode(line)` | Alias of `usb_command` |
| `export(path)` / `port="file:…"` | Write the job as `.gcode` instead of streaming |
| `check(path)` | Run a `.gcode` file in `$C` check mode, which validates without motion |
| `calibrate_pen()` | Guided step-through like §1 (S1000 → insert pen → lift in steps → save `pen_pos_up`) |
| `params.simplify_mm` | Merge nearly collinear tiny segments before streaming (default 0.01 mm) to save serial bandwidth |
| `params.pen_height_map` + `calibrate_bed()` | **Bed compensation.** The bed isn't flat (§1), so a guided routine measures the touch point at a grid of points (e.g. 3×3). Pen-up S is then interpolated per position (bilinear), so travel clears the paper everywhere without lifting needlessly high. Pen-down stays `S1000` (pen inserted at that position). |

CLI (`python -m ndgcode …`):

- `plot file.svg [--layer N] [--preview]`
- `stream file.gcode`, `export file.svg out.gcode`
- `pen up|down|cycle`, `calibrate pen`
- `jog`, `home`, `set-home`, `walk-home`
- `status`, `settings`, `name [new]`, `ports`

---

## 6. G-code dialect

```gcode
; connect
G21 G90 G17 G94          ; mm, absolute, XY plane, units/min
M3 S700                  ; pen up (pen_pos_up=30)
G4 P0.15                 ; servo pause (servo_settle_ms + pen_delay_up)
; draw_path
G1 X20 Y20 F3750         ; pen-up travel (speed_penup 75 %)
M3 S1000                 ; pen down (pen_pos_down=0)
G4 P0.15
G1 X60 Y20 F1250         ; pen-down (speed_pendown 25 %)
G1 X60 Y60
...
M3 S700
G4 P0.15
; disconnect
G1 X0 Y0 F3750           ; park (park_on_disconnect)
M5                       ; rest = fully up
```

- Coordinates are GRBL machine coordinates after `axis_map`. Numbers use at
  most 3 decimals, trailing zeros removed. `F` is sent only when it changes.
- Pen changes and `G4` empty GRBL's planner (laser mode off). Motion stops
  at each pen change, so the pen never moves mid-stroke.
- **Streaming:** the character-counting protocol keeps at most 128 bytes
  unacknowledged. `params.stream_mode = "simple"` sends one line per `ok`.
  Status `?` is polled at about 5 Hz in a reader thread.
- **Startup:** wait for the banner after the port reset. When the board
  reports `ALARM`, the wrapper does not send `$X` on its own; it raises
  error 101 with a hint.

---

## 7. Configuration (`ndgcode_conf.py`, loadable via `load_config`)

```python
# machine
port = None                 # None = auto-detect
baud = 115200
max_feed_mm_min = 5000      # = $110/$111
max_accel_mm_s2 = 400       # = $120/$121 at accel=100
write_accel = True          # write $120/$121 only on change
travel_x_mm = 305           # PROVISIONAL: vendor range 310 − 5 mm pull-off; verify by measurement
travel_y_mm = 251           # PROVISIONAL: vendor range 256 − 5 mm pull-off
axis_map = dict(flip_x=False, flip_y=True, swap_xy=False)    # measured 2026-11-25
homing_strategy = "manual"  # "manual" | "switches"
pull_off_mm = 5             # off both stops after manual homing (edge friction)
park_on_disconnect = True
sleep_on_disconnect = True  # $SLP after parking ($1=255 keeps motors on otherwise)
feed_cap_mm_min = 1000      # PROVISIONAL hard cap until the motion fault is solved
pen_height_map = None       # optional XY → S offset grid (bed not flat), see §5
# pen (calibrated 2026-11-25)
s_full_down = 1000          # pen_pos 0 %
s_full_up = 0               # pen_pos 100 %
s_contact = 800             # tip leaves paper here; warn if pen_pos_down maps above this height
pen_pos_down = 0            # → S1000
pen_pos_up = 45             # → S550; S700 (30 %) dragged on the low part of the bed
servo_settle_ms = 150       # measured minimum 100
pen_delay_down = 0
pen_delay_up = 0
# NextDraw-compatible defaults
speed_pendown = 25
speed_penup = 75
accel = 75
units = 0                   # inches, as in NextDraw
auto_clip_lift = True
simplify_mm = 0.01
stream_mode = "count"       # "count" | "simple"
```

---

## 8. Open questions / measurements

1. **Frame resolved** (`flip_y`). **Bed size still open:** a measurement
   attempt was blocked by the motion fault (item 9).
2. ~~Limit switches~~ **resolved: none visible.** Default `homing_strategy =
   "manual"`. The `"switches"` strategy stays in the spec for a future
   hardware upgrade but has low priority.
3. ~~`$1`~~ **resolved:** set to **`$1=255`** on 2026-11-25 (approved).
   Motors stay energized while connected. `disconnect()` ends with `$SLP`
   (motors off) after parking.
   - **Decision:** Home corner = far left, as in NextDraw (§3).
   - **Decision:** `accel` writes `$120/$121` only when the value differs
     from the board.
4. ~~`$SLP`~~ **resolved:** supported (`[MSG:Sleeping]`, state `Sleep`).
   Waking needs a reset, which happens anyway on the next connect.
   `$C` check mode also works (`[MSG:Enabled]` … `Disabled` + reset).
5. ~~Plob path~~ **resolved** (§4.2). Bed size goes in through
   `params.overrides = {"travel_x": …, "travel_y": …}` (inches); the digest
   clips to it. `mode="layers"` filters, and `reordering`/`hiding` run
   offline.
6. **Servo pause on real strokes:** keep 150 ms or lower it toward 100 ms.
7. **Usable drawing speed and acceleration** for clean lines (feed sweep test).
8. ~~Reset on open~~ **resolved: unavoidable in software.** `-hupcl` is
   reset by macOS on every open, and pyserial with DTR/RTS low before open
   still resets. So:
   - every `connect()` starts with MPos 0,0,0 and the pen at rest (up);
   - "connect = carriage is at Home" is the rule, and `park_on_disconnect`
     matters;
   - one script should keep one connection.

   The only way out would be hardware: a capacitor between the board's RESET
   pin and GND, which is out of scope. Other offsets (`$#`) are all zero, and
   no startup lines (`$N`) are stored.

9. **Motion fault (hardware):** periodic jerking and stalls on long X runs,
   felt on the right motor (§1). Next machine session:
   - **Where:** run the same long X move at Y = −50 and Y = −150. If it's
     smooth there, the cause is position-dependent (back edge, gantry).
   - **Period:** mark the motor pulleys and check whether the jerks repeat
     once per pulley turn (≈ 40 mm on a 20-tooth GT2 pulley). That would
     mean a pulley set screw, an eccentric pulley, or a belt damaged at one
     spot.
   - **Motor vs. driver:** swap the two motor cables at the board. If the
     fault moves to the other side, it's the driver or its current setting;
     if it stays, it's the motor, belt or mechanics.
   - **Speed:** a sweep with return-to-dot tests, to set `feed_cap_mm_min`
     and possibly a lower acceleration (`$120/$121`).
10. **Bed flatness map:** touch point at a 3×3 grid (feeds
    `pen_height_map`), once the motion is reliable.
11. ~~Vendor Windows software~~ **resolved** (2026-11-26, read as data,
    nothing run). Package `VigoWriter_v2.2_for_writer_control/`:
    - **Firmware:** `VigoWriterFirmware_Write_v2.01.hex` reports exactly what
      the board reports (`VigoWriter 1.1f`, `[VER:1.1f.20170131:`), so the
      board already runs the vendor's writer firmware; no update needed. Its
      help line lists `$SLP $C $X $H $J=`. **Never flash**
      `VigoFirmware_X4_For_Laser_v1.1.hex`; per the vendor note it's for
      laser engraving with VigoEngraver.
    - **Program:** VigoWriter v2.2 is a Qt app based on the open-source GRBL
      controller Candle. It uses the same commands as this spec: pen
      `M3 S<n>`, "Pen Reset" `M3 S0`, jog `$J=G91 X…`/`Y…`, back to origin
      `G0 X0 Y0`, reset on port open. Device setup writes `$100/$101`,
      `$110/$111`, `$120/$121`, `$30=1000`, `$32=0`, `$3=0…3`.
    - **Homing:** no switch homing. Origin handling is manual ("Set as
      Origin", "Back to Origin", `Set_Home_Auto`, origin-corner choice),
      which confirms our manual approach.
    - **Default bed size** (`setting_machine_range_max_x/y`, fallback in
      code): **310 × 256 mm (X × Y)**. Used as the provisional travel
      (minus pull-off) until it's measured.
    - Pen-height and speed defaults were not extracted (UI widget defaults;
      not needed, we calibrated our own).

## 9. Milestones

**Status 2026-11-26:** M0–M5 are implemented and tested against the
simulated board (82 unit tests). What's still open needs the machine: the
"Done when" checks marked *(hw)* below, the measurements in §8, and
[docs/hardware-checklist.md](docs/hardware-checklist.md).

Decisions made during implementation:

- `pen_pos_up` defaults to 45 % (S550), because S700 dragged on the low
  part of the bed.
- A provisional hard speed cap of 1000 mm/min (`feed_cap_mm_min`).
- Pen-up moves use `G1` at `speed_penup`, so the cap applies to them too.
- `options.port` also accepts `file:`, `null:`, `fake:`, a nickname, or a
  backend object.
- `ndgcode run` executes unchanged NextDraw scripts, and scripts that import
  `ndgcode` directly.
- Plot-context utilities that need a remembered position (`walk_home`,
  `set_home`) only warn, because each connection resets the board.

| # | Scope | Done when |
|---|---|---|
| M0 | Planner + emitter + FileBackend, unit tests (golden G-code) | Square, sine and Hershey text produce the expected `.gcode` — ✅ |
| M1 | SerialBackend (streaming, status, errors); measure bed and frame | Square drawn at the right place and orientation — code ✅, *(hw)* pending |
| M2 | Full interactive API mirror (§4.1, §4.3, §4.8, §4.9) | `oct-explorations` and `plottext` scripts run unchanged apart from the import — ✅ (`test_hershey.py` exports 51 strokes, no edits) |
| M3 | Comfort: homing strategies, jog, pause/resume, park, `calibrate_pen`, CLI | Session can start, pause, resume and end without manual G-code — code ✅, *(hw)* pending |
| M4 | Plot context via Plob: `plot`, `layers`, `cycle`, utilities, `copies` | An SVG with 2 layers plots reordered, layer by layer — ✅ (export / simulated board) |
| M5 | `preview`, estimates, `res_plot`, `report_time`, `webhook` | Paused plot resumes — ✅; estimate within ±10 % of real time — *(hw)* pending |

## 10. Testing

- **Unit:** the emitter and planner against golden `.gcode` files, covering
  clipping cases, units, pen sequencing and feed changes.
- **Fake GRBL:** a small in-process serial stand-in. It answers
  `ok`/`error`, models the 128-byte buffer and `?`, and tracks position.
  Used for streaming, pause/resume and error-path tests without hardware.
- **Hardware smoke test** (manual): pen cycle, a 50 mm square, Hershey text
  line, jog plus set-home, pause/resume mid-path.
- **Compatibility:** run project scripts against both `nextdraw`+ndsim and
  `ndgcode`+FileBackend, then compare the resulting geometry.
