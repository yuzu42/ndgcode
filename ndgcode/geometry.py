"""Geometry helpers: units, bounds clipping, path simplification, frame mapping.

All internal coordinates are millimetres in the *NextDraw frame*: origin at the
Home corner (far left, seen from the operator), +x to the operator's right,
+y toward the operator.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

#: NextDraw ``options.units``: 0 = inches, 1 = centimetres, 2 = millimetres.
UNIT_TO_MM = {0: 25.4, 1: 10.0, 2: 1.0}


def unit_scale(units: int) -> float:
    """Millimetres per user unit for a NextDraw ``units`` value."""
    try:
        return UNIT_TO_MM[int(units)]
    except (KeyError, ValueError, TypeError):
        raise ValueError(f"units must be 0 (in), 1 (cm) or 2 (mm), not {units!r}")


class Bounds:
    """Axis-aligned travel bounds in mm: [0, width] x [0, height]."""

    def __init__(self, width: float, height: float, tolerance: float = 0.0):
        if width <= 0 or height <= 0:
            raise ValueError("travel bounds must be positive")
        self.width = float(width)
        self.height = float(height)
        self.tol = float(tolerance)

    def contains(self, p: Point) -> bool:
        t = self.tol
        return -t <= p[0] <= self.width + t and -t <= p[1] <= self.height + t

    def clamp(self, p: Point) -> Point:
        return (min(max(p[0], 0.0), self.width), min(max(p[1], 0.0), self.height))

    def clip_segment(self, a: Point, b: Point) -> Optional[Tuple[Point, Point]]:
        """Liang-Barsky clipping. Returns the visible part of a->b, or None.

        Points within ``tolerance`` outside the bounds are snapped onto them
        instead of being treated as outside.
        """
        x0, y0 = a
        x1, y1 = b
        dx, dy = x1 - x0, y1 - y0
        t0, t1 = 0.0, 1.0
        lo_x, hi_x = -self.tol, self.width + self.tol
        lo_y, hi_y = -self.tol, self.height + self.tol
        for p, q in ((-dx, x0 - lo_x), (dx, hi_x - x0), (-dy, y0 - lo_y), (dy, hi_y - y0)):
            if p == 0:
                if q < 0:
                    return None
                continue
            r = q / p
            if p < 0:
                if r > t1:
                    return None
                t0 = max(t0, r)
            else:
                if r < t0:
                    return None
                t1 = min(t1, r)
        if t0 > t1:
            return None
        pa = self.clamp((x0 + t0 * dx, y0 + t0 * dy))
        pb = self.clamp((x0 + t1 * dx, y0 + t1 * dy))
        return pa, pb

    def clip_path(self, pts: Sequence[Point]) -> List[List[Point]]:
        """Split a polyline into the pieces that lie inside the bounds."""
        pieces: List[List[Point]] = []
        cur: List[Point] = []
        for a, b in zip(pts, pts[1:]):
            seg = self.clip_segment(a, b)
            if seg is None:
                if len(cur) >= 2:
                    pieces.append(cur)
                cur = []
                continue
            pa, pb = seg
            if cur and _close(cur[-1], pa):
                cur.append(pb)
            else:
                if len(cur) >= 2:
                    pieces.append(cur)
                cur = [pa, pb]
            if not _close(pb, self.clamp(b)) or not self.contains(b):
                # left the bounds within this segment
                if len(cur) >= 2:
                    pieces.append(cur)
                cur = []
        if len(cur) >= 2:
            pieces.append(cur)
        return [p for p in pieces if path_length(p) > 0 or len(p) == 2]


def _close(a: Point, b: Point, eps: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def path_length(pts: Sequence[Point]) -> float:
    return sum(dist(a, b) for a, b in zip(pts, pts[1:]))


def simplify(pts: Sequence[Point], tol: float) -> List[Point]:
    """Ramer-Douglas-Peucker simplification; keeps both end points.

    ``tol`` <= 0 returns the points unchanged (minus exact duplicates).
    """
    out: List[Point] = []
    for p in pts:
        if not out or not _close(out[-1], p):
            out.append((float(p[0]), float(p[1])))
    if tol <= 0 or len(out) < 3:
        return out
    keep = [False] * len(out)
    keep[0] = keep[-1] = True
    stack = [(0, len(out) - 1)]
    while stack:
        i, j = stack.pop()
        a, b = out[i], out[j]
        best, idx = -1.0, -1
        for k in range(i + 1, j):
            d = _point_segment_distance(out[k], a, b)
            if d > best:
                best, idx = d, k
        if best > tol:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(out, keep) if k]


def _point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


class Frame:
    """Maps NextDraw-frame vectors (mm) onto GRBL machine axes."""

    def __init__(self, flip_x: bool = False, flip_y: bool = True, swap_xy: bool = False):
        self.flip_x = bool(flip_x)
        self.flip_y = bool(flip_y)
        self.swap_xy = bool(swap_xy)

    def vec_to_machine(self, x: float, y: float) -> Point:
        if self.swap_xy:
            x, y = y, x
        if self.flip_x:
            x = -x
        if self.flip_y:
            y = -y
        return x, y

    def vec_from_machine(self, X: float, Y: float) -> Point:
        if self.flip_y:
            Y = -Y
        if self.flip_x:
            X = -X
        if self.swap_xy:
            X, Y = Y, X
        return X, Y


class HeightMap:
    """Bilinear pen-up S value over the bed (bed compensation).

    ``grid`` is a dict ``{"xs": [...], "ys": [...], "s_up": [[row per y]...]}``
    in NextDraw-frame mm. Values outside the grid are clamped to the edge.
    """

    def __init__(self, xs: Sequence[float], ys: Sequence[float], s_up: Sequence[Sequence[float]]):
        if len(xs) < 1 or len(ys) < 1:
            raise ValueError("height map needs at least one point")
        if len(s_up) != len(ys) or any(len(r) != len(xs) for r in s_up):
            raise ValueError("height map s_up must have len(ys) rows of len(xs) values")
        self.xs = [float(v) for v in xs]
        self.ys = [float(v) for v in ys]
        self.s = [[float(v) for v in row] for row in s_up]

    @classmethod
    def from_dict(cls, d: dict) -> "HeightMap":
        return cls(d["xs"], d["ys"], d["s_up"])

    def to_dict(self) -> dict:
        return {"xs": self.xs, "ys": self.ys, "s_up": self.s}

    @staticmethod
    def _locate(vals: List[float], v: float) -> Tuple[int, int, float]:
        if len(vals) == 1 or v <= vals[0]:
            return 0, 0, 0.0
        if v >= vals[-1]:
            n = len(vals) - 1
            return n, n, 0.0
        for i in range(len(vals) - 1):
            if vals[i] <= v <= vals[i + 1]:
                span = vals[i + 1] - vals[i]
                return i, i + 1, (v - vals[i]) / span if span else 0.0
        n = len(vals) - 1
        return n, n, 0.0

    def s_up_at(self, x: float, y: float) -> float:
        i0, i1, tx = self._locate(self.xs, x)
        j0, j1, ty = self._locate(self.ys, y)
        top = self.s[j0][i0] * (1 - tx) + self.s[j0][i1] * tx
        bot = self.s[j1][i0] * (1 - tx) + self.s[j1][i1] * tx
        return top * (1 - ty) + bot * ty

    def min_s(self) -> float:
        return min(min(r) for r in self.s)


def as_points(vertices: Iterable[Sequence[float]], scale: float = 1.0) -> List[Point]:
    """Validate a NextDraw-style vertex list and convert it to mm points."""
    pts: List[Point] = []
    for v in vertices:
        if len(v) != 2:
            raise ValueError(f"vertex {v!r} is not an [x, y] pair")
        pts.append((float(v[0]) * scale, float(v[1]) * scale))
    return pts
