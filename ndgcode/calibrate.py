"""Guided calibration: pen-up height and the bed height map.

Both routines talk to the user through ``ask``/``say`` callables (``input`` and
``print`` by default), so they work in a terminal and can be tested.

Procedure (as worked out on the VG-X4, SPEC 1):
the pen goes fully down (pen-down position), you insert it so the tip rests
on the paper, then the pen is lifted in steps until you say it is off the
paper. The pen-up value is that point plus a safety margin.
"""

from __future__ import annotations

import json
from typing import Callable, List, Optional

from .geometry import HeightMap


def _yes(ans: str) -> bool:
    return ans.strip().lower() in ("y", "yes", "j", "ja", "t", "touching", "")


def find_lift_off(nd, ask: Callable[[str], str], say: Callable[[str], None],
                  start: Optional[float] = None, step: float = 50) -> Optional[float]:
    """Lift from ``start`` (pen-down S) in steps; return the first S that is off the paper."""
    m = nd.machine
    st = m.st
    s = st.s_down if start is None else start
    direction = 1 if st.s_full_up > st.s_full_down else -1
    limit = st.s_full_up
    while True:
        s = s + direction * step
        if (direction < 0 and s < limit) or (direction > 0 and s > limit):
            say("Reached the top of the servo range without leaving the paper.")
            return None
        m.servo(s)
        nd.block()
        ans = ask(f"S{s:.0f}: is the tip still touching the paper? [Y/n/q] ")
        if ans.strip().lower() == "q":
            return None
        if not _yes(ans):
            return s


def calibrate_pen(nd, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                  margin: float = 100, step: float = 50) -> Optional[float]:
    """Find a pen-up height. Returns the new ``pen_pos_up`` (percent) or None."""
    m = nd.machine
    st = m.st
    say(f"Moving the servo to the pen-down position (S{st.s_down:.0f}).")
    m.servo(st.s_down)
    nd.block()
    ask("Insert the pen so its tip just rests on the paper, then press Enter. ")
    off = find_lift_off(nd, ask, say, step=step)
    if off is None:
        m.raise_pen()
        return None
    direction = 1 if st.s_full_up > st.s_full_down else -1
    s_up = off + direction * margin
    while True:
        m.servo(s_up)
        nd.block()
        ans = ask(f"Pen-up candidate S{s_up:.0f}. Higher (+), lower (-) or OK [Enter]? ").strip()
        if ans == "+":
            s_up += direction * 25
        elif ans == "-":
            s_up -= direction * 25
        else:
            break
    pct = round(st.s_to_height(s_up), 1)
    say(f"Leaves the paper at S{off:.0f}. Pen up = S{s_up:.0f} = pen_pos_up {pct}.")
    say(f"Add to your config:  pen_pos_up = {pct}")
    nd.options.pen_pos_up = pct
    nd.update()
    m.raise_pen()
    return pct


def grid(width: float, height: float, nx: int, ny: int, margin: float = 10.0) -> (List[float], List[float]):
    def axis(span, n):
        if n == 1:
            return [span / 2]
        lo, hi = margin, max(margin, span - margin)
        return [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    return axis(width, nx), axis(height, ny)


def calibrate_bed(nd, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                  nx: int = 3, ny: int = 3, margin_s: float = 100, step: float = 50,
                  save_to: Optional[str] = None) -> Optional[HeightMap]:
    """Measure the lift-off point on a grid; returns (and applies) a HeightMap.

    The pen must already be inserted at the pen-down position (see
    :func:`calibrate_pen`). Between points the pen travels fully raised.
    """
    m = nd.machine
    st = m.st
    xs, ys = grid(st.bounds.width, st.bounds.height, nx, ny)
    direction = 1 if st.s_full_up > st.s_full_down else -1
    rows: List[List[float]] = []
    say(f"Measuring {nx}x{ny} points. Put paper under all of them.")
    for j, y in enumerate(ys):
        row = []
        for i, x in enumerate(xs):
            m.servo(st.s_full_up)                 # fully up for the move
            m._g1((x, y), st.feed_up)
            m.servo(st.s_down)
            nd.block()
            say(f"Point {j * nx + i + 1}/{nx * ny} at x={x:.0f} y={y:.0f} mm.")
            off = find_lift_off(nd, ask, say, step=step)
            if off is None:
                m.servo(st.s_full_up)
                return None
            row.append(off + direction * margin_s)
        rows.append(row)
    m.servo(st.s_full_up)
    hm = HeightMap(xs, ys, rows)
    nd.params.pen_height_map = hm.to_dict()
    nd.update()
    if save_to:
        with open(save_to, "w", encoding="utf-8") as fh:
            json.dump(hm.to_dict(), fh, indent=2)
        say(f"Saved to {save_to}. Add to your config:  pen_height_map = {save_to!r}")
    m.walk_home()
    return hm
