# Hardware checklist (first sessions on the VG-X4)

Everything below was only tested against the simulated board. Work through it
in order; each step says what to record. Use `python -m ndgcode shell`
(one connection for the whole session) unless noted.

Before every connection: **push the carriage into the far-left corner** against
both stops. Put paper on the bed before any step that draws.

## 1. Connection and identity (no motion)

- [ ] `python -m ndgcode ports`: the plotter's port is listed, and no
      other device is.
- [ ] `python -m ndgcode info`: banner `VigoWriter 1.1f`, `$1=255`, `$32=0`.

## 2. Connect, pull-off, park

- [ ] `shell`: on connect the pen moves to pen-up, then the carriage moves
      5 mm right and 5 mm toward you.
- [ ] `status`: MPos `5.000,-5.000`. The position shows `(0.0, 0.0)`.
- [ ] `quit`: the pen goes up, the carriage returns into the corner, the
      motors release (`$SLP`).

## 3. Pen

- [ ] `calibrate pen`: insert the pen at `S1000`, step up, confirm the
      lift-off point. Record the `pen_pos_up` it suggests.
- [ ] `pen cycle` a few times: the down/up timing looks clean (150 ms pause).
      If line starts blot or the pen bounces, raise `servo_settle_ms`.

## 4. Motion fault (open from 2026-11-26)

Long X runs jerked periodically on the right motor, even at `F1500`.

- [ ] `goto 0 50`, then `speed 20` and `line 290 50` (Y = 50 mm, away from
      the back edge): smooth or jerky?
- [ ] The same at `goto 0 150` / `line 290 150`.
- [ ] If it's jerky: mark the right motor's pulley and shaft with a marker
      line. Does the jerk repeat once per pulley turn (≈ 40 mm on a 20-tooth
      GT2 pulley)? Check the set screw and the belt at that spot.
- [ ] If it's still unclear: swap the two motor cables at the board. If the
      fault moves to the other side, it's the driver or its current
      setting; if it stays, it's the motor, belt or mechanics.
- [ ] Once it's smooth: a speed sweep with return-to-dot. `jog`/`line` out and
      back at increasing `speed`, tapping a dot (`pen down` / `pen up`)
      before and after. The dots must coincide. Set `feed_cap_mm_min` below
      the first speed that loses steps.

## 5. Bed size

- [ ] Jog right in steps (`jog 50 0`, then smaller) until just before the
      end of travel; `status` gives X. Same toward you for Y. Record both
      minus a 5 mm margin as `travel_x_mm` / `travel_y_mm`. The vendor
      default is 310 × 256 mm.

## 6. Bed flatness

- [ ] `calibrate bed bed_map.json` (3×3 points, paper under all of them).
      Then set `pen_height_map = "bed_map.json"` in your config.

## 7. First drawings

- [ ] `square 150 20 20`: measure all four sides (scale; Y measured 99.1 %
      before) and both diagonals (squareness). Correct `$100/$101` only if
      both sides are off consistently.
- [ ] `python -m ndgcode run examples/square_and_text.py`: the text reads
      correctly from your side (the NextDraw y-flip).
- [ ] An unchanged project script, e.g. `penplotter/oct-explorations/test_hershey.py`
      via `python -m ndgcode run`.
- [ ] `python -m ndgcode plot some.svg --preview`, then for real. Compare
      the estimated and real time and record both.

## 8. Pause, stop, resume

- [ ] Ctrl-C in the shell during a long `draw`: the motion stops (feed hold
      + reset), and the pen goes up. `status` afterwards.
- [ ] In a script: `nd.pause()` / `nd.resume()` from a second thread while
      `draw_path` runs.
- [ ] Ctrl-C during a plot with `keyboard_pause = True`: the carriage stops,
      and the pen goes up. **Check that `status` MPos still matches the real
      position** (feed hold + soft reset is assumed to keep it).
- [ ] `plot_run(True)` output → `res_plot`: it continues where it stopped.

## 9. Streaming

- [ ] `python -m ndgcode check out.gcode`, then `stream out.gcode`.
      Ctrl-C during the stream stops safely, with the pen up.
