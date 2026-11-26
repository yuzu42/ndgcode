"""Plot time estimate: a small model of GRBL's motion planner.

Segments between synchronisation points (pen changes, dwells, end) are
planned with trapezoidal velocity profiles. Junction speeds follow GRBL's
junction-deviation formula, followed by a backward and a forward pass.
The result is an approximation; calibrate against real runs (SPEC 4.8).
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple


class _Seg:
    __slots__ = ("length", "vmax", "ux", "uy", "v_entry_max", "v_entry")

    def __init__(self, length: float, vmax: float, ux: float, uy: float):
        self.length = length
        self.vmax = vmax
        self.ux = ux
        self.uy = uy
        self.v_entry_max = 0.0
        self.v_entry = 0.0


class MotionEstimator:
    """Accumulates moves and dwells and returns total seconds."""

    def __init__(self, accel_mm_s2: float, junction_deviation_mm: float = 0.1):
        self.accel = max(float(accel_mm_s2), 1e-6)
        self.jd = max(float(junction_deviation_mm), 0.0)
        self.total_s = 0.0
        self._segs: List[_Seg] = []

    def set_accel(self, accel_mm_s2: float) -> None:
        self.flush()
        self.accel = max(float(accel_mm_s2), 1e-6)

    def move(self, a: Tuple[float, float], b: Tuple[float, float], feed_mm_min: float) -> None:
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L <= 1e-9:
            return
        self._segs.append(_Seg(L, max(feed_mm_min, 1e-6) / 60.0, dx / L, dy / L))

    def dwell(self, seconds: float) -> None:
        self.flush()
        self.total_s += max(0.0, float(seconds))

    def sync(self) -> None:
        self.flush()

    def flush(self) -> float:
        """Plan and time all queued segments (stop at both ends)."""
        segs = self._segs
        if not segs:
            return 0.0
        self._segs = []
        a = self.accel
        # junction limits
        prev: Optional[_Seg] = None
        for s in segs:
            if prev is None:
                s.v_entry_max = 0.0
            else:
                cos_theta = -(prev.ux * s.ux + prev.uy * s.uy)  # GRBL convention
                if cos_theta > 0.999999:
                    v = 0.0  # full reversal
                elif cos_theta < -0.999999:
                    v = min(prev.vmax, s.vmax)  # straight on
                else:
                    sin_half = math.sqrt(max(0.0, 0.5 * (1.0 - cos_theta)))
                    v = math.sqrt(a * self.jd * sin_half / max(1e-9, 1.0 - sin_half))
                    v = min(v, prev.vmax, s.vmax)
                s.v_entry_max = v
            prev = s
        # backward pass (decelerate to 0 at the end)
        v_next = 0.0
        for s in reversed(segs):
            s.v_entry = min(s.v_entry_max, math.sqrt(v_next * v_next + 2 * a * s.length))
            v_next = s.v_entry
        # forward pass
        v_prev_exit = 0.0
        t = 0.0
        for i, s in enumerate(segs):
            v0 = min(s.v_entry, v_prev_exit) if i else 0.0
            v_exit_limit = segs[i + 1].v_entry if i + 1 < len(segs) else 0.0
            v1 = min(v_exit_limit, math.sqrt(v0 * v0 + 2 * a * s.length))
            t += _trapezoid_time(s.length, v0, v1, s.vmax, a)
            v_prev_exit = v1
        self.total_s += t
        return t


def _trapezoid_time(L: float, v0: float, v1: float, vmax: float, a: float) -> float:
    vmax = max(vmax, v0, v1)
    d_acc = (vmax * vmax - v0 * v0) / (2 * a)
    d_dec = (vmax * vmax - v1 * v1) / (2 * a)
    if d_acc + d_dec <= L:
        return (vmax - v0) / a + (vmax - v1) / a + (L - d_acc - d_dec) / vmax
    # triangle: peak speed below vmax
    vp = math.sqrt(max(0.0, (2 * a * L + v0 * v0 + v1 * v1) / 2))
    return max(0.0, (vp - v0) / a) + max(0.0, (vp - v1) / a)
