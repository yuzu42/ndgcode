"""Planner: NextDraw-style motion -> G-code lines on a backend.

The :class:`Machine` owns the session state (turtle and physical position,
pen state, origin) and emits G-code through a backend. It knows nothing about
the NextDraw API surface; :mod:`ndgcode.api` maps that onto it.
"""

from __future__ import annotations

import json
import math
import os
from typing import Callable, List, Optional, Sequence

from . import conf
from .backends import Backend
from .estimate import MotionEstimator
from .geometry import Bounds, Frame, HeightMap, Point, dist, path_length, simplify


class Settings:
    """Resolved, validated values from ``options`` and ``params``."""

    def __init__(self, options, params, warn: Callable[[str], None] = lambda m: None):
        o, p = options, params
        hf, ha = conf.HANDLING.get(int(o.handling), conf.HANDLING[1])
        if int(o.handling) not in conf.HANDLING:
            warn(f"handling={o.handling!r} is not 1-4; using 1")
        cap = float(p.feed_cap_mm_min)
        maxf = float(p.max_feed_mm_min)
        self.feed_cap = cap
        self.feed_down = min(_pct(o.speed_pendown, 1, 110, "speed_pendown", warn) / 100 * maxf * hf, cap)
        self.feed_up = min(_pct(o.speed_penup, 1, 110, "speed_penup", warn) / 100 * maxf * hf, cap)
        self.accel = min(_pct(o.accel, 1, 100, "accel", warn) / 100 * float(p.max_accel_mm_s2) * ha,
                         float(p.max_accel_mm_s2))
        self.s_full_down = float(p.s_full_down)
        self.s_full_up = float(p.s_full_up)
        self.s_down = self.height_to_s(_pct(o.pen_pos_down, 0, 100, "pen_pos_down", warn))
        self.s_up = self.height_to_s(_pct(o.pen_pos_up, 0, 100, "pen_pos_up", warn))
        if _lift(self.s_down, self) >= _lift(float(p.s_contact), self):
            warn(f"pen_pos_down={o.pen_pos_down} maps to S{self.s_down:.0f}, which is at or above the "
                 f"height where the tip leaves the paper (S{p.s_contact}); the pen may not draw")
        if _lift(self.s_up, self) <= _lift(self.s_down, self):
            warn("pen_pos_up is not above pen_pos_down")
        self.rate_raise = _pct(o.pen_rate_raise, 1, 100, "pen_rate_raise", warn)
        self.rate_lower = _pct(o.pen_rate_lower, 1, 100, "pen_rate_lower", warn)
        self.rate_jump = float(p.pen_rate_jump)
        self.sweep_ms = float(p.servo_sweep_ms)
        self.settle_up_s = (float(p.servo_settle_ms) + float(p.pen_delay_up)) / 1000.0
        self.settle_down_s = (float(p.servo_settle_ms) + float(p.pen_delay_down)) / 1000.0
        self.bounds = Bounds(float(p.travel_x_mm), float(p.travel_y_mm),
                             float(p.bounds_tolerance) * 25.4)
        self.frame = Frame(p.flip_x, p.flip_y, p.swap_xy)
        self.auto_clip_lift = bool(p.auto_clip_lift)
        self.simplify_mm = float(p.simplify_mm)
        self.decimals = int(p.decimals)
        self.pull_off = float(p.pull_off_mm)
        self.pull_off_feed = min(float(p.pull_off_feed_mm_min), cap)
        self.jog_feed = min(float(p.jog_feed_mm_min), cap)
        self.junction_deviation = float(p.junction_deviation_mm)
        self.height_map = _load_height_map(p.pen_height_map)

    def height_to_s(self, h: float) -> float:
        return self.s_full_down + (self.s_full_up - self.s_full_down) * h / 100.0

    def s_to_height(self, s: float) -> float:
        span = self.s_full_up - self.s_full_down
        return (s - self.s_full_down) * 100.0 / span if span else 0.0


def _lift(s: float, st: Settings) -> float:
    """Monotonic 'how high' measure for an S value (larger = higher)."""
    return st.s_to_height(s)


def _pct(v, lo, hi, name, warn) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, not {v!r}")
    if f < lo or f > hi:
        warn(f"{name}={v} is outside {lo}-{hi}; clamped")
        f = min(max(f, lo), hi)
    return f


def _load_height_map(v) -> Optional[HeightMap]:
    if v is None:
        return None
    if isinstance(v, HeightMap):
        return v
    if isinstance(v, (str, os.PathLike)):
        with open(v, encoding="utf-8") as fh:
            v = json.load(fh)
    return HeightMap.from_dict(v)


class Machine:
    """Session state plus G-code emission for one connection."""

    def __init__(self, settings: Settings, backend: Backend,
                 warn: Callable[[str], None] = lambda m: None):
        self.st = settings
        self.be = backend
        self.warn = warn
        self.origin_m: Point = (0.0, 0.0)   # machine coords of the NextDraw origin
        self.pos: Point = (0.0, 0.0)        # physical position, NextDraw frame (mm)
        self.turtle: Point = (0.0, 0.0)     # requested position, NextDraw frame (mm)
        self.pen_up: bool = True            # physical
        self.turtle_pen_up: bool = True
        self.s_now: Optional[float] = None  # None = M5 rest (fully up)
        self._f_last: Optional[float] = None
        self.dist_down = 0.0
        self.dist_total = 0.0
        self.pen_lifts = 0
        self.est = MotionEstimator(settings.accel, settings.junction_deviation)

    # ------------------------------------------------------------ settings
    def apply_settings(self, settings: Settings) -> None:
        # New pen heights take effect on the next pen movement, as in NextDraw.
        self.st = settings
        self.est.set_accel(settings.accel)
        self._f_last = None

    # ------------------------------------------------------------ emission
    def _fmt(self, v: float) -> str:
        s = f"{v:.{self.st.decimals}f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s

    def emit(self, line: str) -> None:
        self.be.send(line)

    def to_machine(self, p: Point) -> Point:
        vx, vy = self.st.frame.vec_to_machine(p[0], p[1])
        return self.origin_m[0] + vx, self.origin_m[1] + vy

    def from_machine(self, X: float, Y: float) -> Point:
        return self.st.frame.vec_from_machine(X - self.origin_m[0], Y - self.origin_m[1])

    def _g1(self, p: Point, feed: float) -> None:
        if dist(self.pos, p) <= 10 ** (-self.st.decimals) / 2:
            return
        X, Y = self.to_machine(p)
        line = f"G1 X{self._fmt(X)} Y{self._fmt(Y)}"
        if self._f_last is None or not math.isclose(feed, self._f_last):
            line += f" F{self._fmt(feed)}"
            self._f_last = feed
        self.emit(line)
        d = dist(self.pos, p)
        self.dist_total += d
        if not self.pen_up:
            self.dist_down += d
        self.est.move(self.pos, p, feed)
        self.pos = p

    def dwell(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self.emit(f"G4 P{self._fmt(seconds)}")
        self.est.dwell(seconds)

    def _servo(self, s: float) -> None:
        s = round(s)
        self.emit(f"M3 S{s}")
        self.est.sync()
        self.s_now = s

    # ------------------------------------------------------------ session
    def start(self, home: bool = True, strategy: str = "manual") -> None:
        """Preamble, pen up, homing. Assumes the carriage is at the Home corner."""
        self.emit("G21 G90 G17 G94")
        self.s_now = None
        self.pen_up = True
        self._pen(up=True, force=True)
        self.origin_m = (0.0, 0.0)
        self.pos = (0.0, 0.0)
        if home:
            if strategy == "switches":
                self.be.wait_idle()
                self.be.query("$H", timeout=120.0)
                off = (0.0, 0.0)
            elif strategy == "manual":
                off = (self.st.pull_off, self.st.pull_off)
            else:
                raise ValueError(f"unknown homing_strategy {strategy!r}")
            if off != (0.0, 0.0):
                self._g1(off, self.st.pull_off_feed)
            self.origin_m = self.to_machine(off)
        self.pos = (0.0, 0.0)
        self.turtle = (0.0, 0.0)
        self.turtle_pen_up = True
        self.est.sync()

    def finish(self, park: bool = True) -> None:
        """Pen up, optionally return to the Home corner, servo to rest."""
        self._pen(up=True)
        if park:
            corner = self.from_machine(0.0, 0.0)
            self._g1(corner, self.st.feed_up)
        self.emit("M5")
        self.s_now = None
        self.est.sync()

    # ------------------------------------------------------------ pen
    def _s_up_here(self, p: Optional[Point] = None) -> float:
        hm = self.st.height_map
        if hm is None:
            return self.st.s_up
        q = self.pos if p is None else p
        return hm.s_up_at(q[0], q[1])

    def _pen(self, up: bool, force: bool = False) -> None:
        target = self._s_up_here() if up else self.st.s_down
        if not force and self.pen_up == up and self.s_now is not None and math.isclose(self.s_now, round(target)):
            return
        rate = self.st.rate_raise if up else self.st.rate_lower
        if self.s_now is not None and rate < self.st.rate_jump:
            delta = abs(target - self.s_now)
            span = abs(self.st.s_full_up - self.st.s_full_down) or 1.0
            dur_ms = self.st.sweep_ms * delta / span * self.st.rate_jump / rate
            k = max(1, int(math.ceil(dur_ms / 25.0)))
            s0 = self.s_now
            for i in range(1, k + 1):
                self._servo(s0 + (target - s0) * i / k)
                if i < k:
                    self.dwell(dur_ms / k / 1000.0)
        else:
            self._servo(target)
        if up and not self.pen_up:
            self.pen_lifts += 1
        self.pen_up = up
        self.dwell(self.st.settle_up_s if up else self.st.settle_down_s)

    def servo(self, s: float, settle: bool = True) -> None:
        """Set a raw servo value (calibration). The pen counts as up afterwards
        unless ``s`` is the pen-down value."""
        self._servo(s)
        self.pen_up = not math.isclose(round(s), round(self.st.s_down))
        if settle:
            self.dwell(self.st.settle_up_s)

    def raise_pen(self) -> None:
        self._pen(up=True)

    def lower_pen(self) -> None:
        self._pen(up=False)

    # ------------------------------------------------------------ motion
    def travel(self, p: Point) -> None:
        """Pen-up move to p (already inside bounds)."""
        if not self.pen_up:
            self._pen(up=True)
        hm = self.st.height_map
        if hm is not None and self.s_now is not None:
            mid = ((self.pos[0] + p[0]) / 2, (self.pos[1] + p[1]) / 2)
            need = max((self._s_up_here(q) for q in (self.pos, mid, p)),
                       key=lambda s: _lift(s, self.st))
            if _lift(need, self.st) > _lift(self.s_now, self.st) + 1e-9:
                self._servo(need)
                self.dwell(self.st.settle_up_s)
        self._g1(p, self.st.feed_up)

    def draw(self, p: Point) -> None:
        """Pen-down move to p (already inside bounds)."""
        if self.pen_up:
            self._pen(up=False)
        self._g1(p, self.st.feed_down)

    def penup(self) -> None:
        self.turtle_pen_up = True
        self._pen(up=True)

    def pendown(self) -> None:
        self.turtle_pen_up = False
        # Never put the pen down outside the bed, or where the carriage is not
        # at the turtle position (clipped); the next goto() lowers it on entry.
        if self.st.bounds.contains(self.turtle) and self._close(self.pos, self.turtle):
            self._pen(up=False)

    @staticmethod
    def _close(a: Point, b: Point, eps: float = 1e-6) -> bool:
        return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps

    def goto(self, target: Point) -> None:
        """NextDraw goto(): move with the current turtle pen state."""
        a, b = self.turtle, target
        self.turtle = b
        bounds = self.st.bounds
        if self.turtle_pen_up:
            self.travel(bounds.clamp(b))
            return
        if not self.st.auto_clip_lift:
            self.draw(bounds.clamp(b))
            return
        seg = bounds.clip_segment(a, b)
        if seg is None:
            self.travel(bounds.clamp(b))
            return
        pa, pb = seg
        if not self._close(self.pos, pa):
            self.travel(pa)
        self.draw(pb)
        if not self._close(pb, b):
            self._pen(up=True)   # left the bed: lift until it re-enters

    def draw_path(self, vertices: Sequence[Point]) -> None:
        """NextDraw draw_path(): clip, then draw each in-bounds piece."""
        pts = list(vertices)
        if len(pts) < 2:
            raise ValueError("draw_path needs at least two vertices")
        self.turtle_pen_up = True
        self._pen(up=True)
        for piece in self.st.bounds.clip_path(pts):
            piece = simplify(piece, self.st.simplify_mm) if self.st.simplify_mm > 0 else piece
            if len(piece) < 2:
                piece = [piece[0], piece[0]]
            self.travel(piece[0])
            self._pen(up=False)
            for q in piece[1:]:
                self._g1(q, self.st.feed_down)
            self._pen(up=True)
        self.turtle = pts[-1]
        self.turtle_pen_up = True

    def jog(self, dx: float, dy: float, feed: Optional[float] = None) -> None:
        """Unclipped relative jog in the NextDraw frame (walk_* utilities)."""
        self._pen(up=True)
        vx, vy = self.st.frame.vec_to_machine(dx, dy)
        f = self.st.jog_feed if feed is None else min(float(feed), self.st.feed_cap)
        self.emit(f"$J=G91 G21 X{self._fmt(vx)} Y{self._fmt(vy)} F{self._fmt(f)}")
        p = (self.pos[0] + dx, self.pos[1] + dy)
        self.est.move(self.pos, p, f)
        self.dist_total += dist(self.pos, p)
        self.pos = p
        self.turtle = p

    def set_home(self) -> None:
        """Make the current physical position the new (0, 0)."""
        self.origin_m = self.to_machine(self.pos)
        self.pos = (0.0, 0.0)
        self.turtle = (0.0, 0.0)

    def walk_home(self) -> None:
        self.turtle_pen_up = True
        self.travel((0.0, 0.0))
        self.turtle = (0.0, 0.0)

    # ------------------------------------------------------------ stats
    @property
    def time_estimate_s(self) -> float:
        self.est.sync()
        return self.est.total_s


def stroke_lengths(strokes: List[List[Point]]) -> float:
    return sum(path_length(s) for s in strokes)
