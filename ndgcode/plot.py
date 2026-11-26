"""Plot context: ``plot_setup()`` / ``plot_run()`` with all modes.

SVG processing is delegated to the real Bantam Tools ``nextdraw`` library:
with ``digest=2`` it returns a *Plob* (plot digest) without any hardware,
already filtered by layer, reordered, hidden-line-removed, auto-rotated,
clipped and joined. We read the Plob's polylines and plot them through the
planner. Layer-name codes (``!`` pause, ``%`` skip, ``+S`` speed, ``+H``
height, ``+D`` delay) survive as text and are applied here.

Resume data is kept in our own ``<ndgcode:plotdata>`` element in the output
SVG; ``res_dist`` is the pen-down distance in mm, as in NextDraw.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from . import errors
from .geometry import Point, dist, path_length

_REAL = None

NS_SVG = "http://www.w3.org/2000/svg"
NS_INK = "http://www.inkscape.org/namespaces/inkscape"
NS_NDG = "https://github.com/ndgcode/ndgcode"
_DATA_RE = re.compile(r"<ndgcode:plotdata\b[^>]*/>\s*", re.S)
_PREVIEW_RE = re.compile(r'<g id="ndgcode-preview".*?</g>\s*', re.S)


def real_nextdraw():
    """The real Bantam Tools ``nextdraw`` module, or None if not installed."""
    global _REAL
    if _REAL is not None:
        return _REAL
    mod = sys.modules.get("nextdraw")
    if mod is not None and getattr(mod, "__ndgcode_shim__", False):
        return None
    try:
        _REAL = importlib.import_module("nextdraw")
    except ImportError:
        return None
    return _REAL


# ---------------------------------------------------------------- data
class Layer:
    def __init__(self, label: str, strokes: List[List[Point]]):
        self.label = label
        self.strokes = strokes
        lab = label.strip()
        self.pause = lab.startswith("!")
        self.skip = lab.lstrip("!").startswith("%")
        codes = {k.upper(): int(v) for k, v in re.findall(r"\+([SHDshd])(\d+)", lab)}
        self.speed = codes.get("S")
        self.height = codes.get("H")
        self.delay_ms = codes.get("D")

    @property
    def length(self) -> float:
        return sum(path_length(s) for s in self.strokes)


def parse_dist_mm(v) -> float:
    """NextDraw ``dist``: number (inches) or "5in" / "127mm" / "5cm"."""
    if isinstance(v, (int, float)):
        return float(v) * 25.4
    s = str(v).strip().lower()
    for suffix, k in (("mm", 1.0), ("cm", 10.0), ("in", 25.4)):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * k
    return float(s) * 25.4


def parse_plob(text: str) -> List[Layer]:
    """Read layers and polylines (inches -> mm) from a Plob document."""
    root = ET.fromstring(text)
    layers: List[Layer] = []
    for g in root.iter(f"{{{NS_SVG}}}g"):
        if g.get(f"{{{NS_INK}}}groupmode") != "layer":
            continue
        label = g.get(f"{{{NS_INK}}}label") or ""
        if label == "__digest-root__":
            continue
        strokes = []
        for pl in g.iter(f"{{{NS_SVG}}}polyline"):
            pts = []
            for pair in (pl.get("points") or "").split():
                x, y = pair.split(",")
                pts.append((float(x) * 25.4, float(y) * 25.4))
            if pts:
                strokes.append(pts if len(pts) > 1 else [pts[0], pts[0]])
        layers.append(Layer(label, strokes))
    return layers


def read_resume(svg: Optional[str]) -> Optional[Dict[str, str]]:
    if not svg:
        return None
    m = re.search(r"<ndgcode:plotdata\b([^>]*)/>", svg, re.S)
    if not m:
        return None
    return dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))


def write_resume(svg: str, attrs: Dict[str, object]) -> str:
    svg = _DATA_RE.sub("", svg)
    if "xmlns:ndgcode=" not in svg:
        svg = re.sub(r"<svg\b", f'<svg xmlns:ndgcode="{NS_NDG}"', svg, count=1)
    body = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    return re.sub(r"</svg>\s*$", f"<ndgcode:plotdata {body}/>\n</svg>\n", svg, count=1)


def strip_data(svg: str) -> str:
    return _PREVIEW_RE.sub("", _DATA_RE.sub("", svg))


# ---------------------------------------------------------------- job
class PlotJob:
    def __init__(self, nd, svg_input=None):
        self.nd = nd
        self.svg_path: Optional[str] = None
        self.svg_text: Optional[str] = None
        if svg_input is None:
            pass
        elif isinstance(svg_input, (str, os.PathLike)) and not str(svg_input).lstrip().startswith("<") \
                and os.path.exists(str(svg_input)):
            self.svg_path = str(svg_input)
            with open(self.svg_path, encoding="utf-8") as fh:
                self.svg_text = fh.read()
        elif isinstance(svg_input, str):
            self.svg_text = svg_input
        else:
            raise ValueError(f"plot_setup(): cannot read {svg_input!r}")
        self.preview_paths: List[Tuple[bool, List[Point]]] = []

    # ------------------------------------------------------------ helpers
    def _say(self, msg: str) -> None:
        print(msg)

    def _output(self, output: bool, svg: Optional[str] = None):
        if not output:
            return None
        svg = svg if svg is not None else self.svg_text
        if svg is None:
            return ('<svg xmlns="http://www.w3.org/2000/svg" width="8.5in" height="11in" '
                    'viewBox="0 0 8.5 11"></svg>\n')
        return svg

    def _connect(self, home: bool = True) -> bool:
        nd = self.nd
        saved = nd.options.homing
        nd.options.homing = bool(saved) and home
        try:
            ok = nd.connect()
        finally:
            nd.options.homing = saved
        return ok

    def _open_backend(self):
        nd = self.nd
        be = nd.make_backend()
        be.open()
        return be

    def plob(self) -> str:
        """Digest the SVG with the real nextdraw library (no hardware)."""
        real = real_nextdraw()
        if real is None:
            raise RuntimeError("SVG plotting needs the Bantam Tools NextDraw Python API "
                               "('nextdraw' package); it is installed separately.")
        if self.svg_text is None:
            raise RuntimeError("plot_setup() was called without an SVG document")
        o, p = self.nd.options, self.nd.params
        nd2 = real.NextDraw()
        nd2.plot_setup(self.svg_path or self.svg_text)
        nd2.options.mode = "layers" if o.mode == "layers" else "plot"
        nd2.options.layer = o.layer
        for name in ("auto_rotate", "reordering", "random_start", "hiding"):
            setattr(nd2.options, name, getattr(o, name))
        nd2.options.digest = 2
        nd2.params.clip_to_page = p.clip_to_page
        nd2.params.min_gap = p.min_gap
        overrides = dict(p.overrides or {})
        overrides.setdefault("travel_x", p.travel_x_mm / 25.4)
        overrides.setdefault("travel_y", p.travel_y_mm / 25.4)
        nd2.params.overrides.update(overrides)
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            out = nd2.plot_run(True)
        for line in (buf_out.getvalue() + buf_err.getvalue()).splitlines():
            if line.strip() and "contact technical support" not in line.lower():
                self.nd._warn("nextdraw: " + line.strip())
        if nd2.errors.code:
            raise RuntimeError(f"nextdraw digest failed (code {nd2.errors.code})")
        return out

    # ------------------------------------------------------------ run
    def run(self, output: bool = False):
        nd, o = self.nd, self.nd.options
        nd.errors.code = errors.OK
        mode = o.mode
        if mode in ("plot", "layers", "res_plot"):
            return self._plot(output, resume=(mode == "res_plot"))
        if mode == "version":
            self._say(nd.version_string)
            return self._output(output)
        if mode == "sysinfo":
            return self._sysinfo(output)
        if mode == "cycle":
            if self._connect(home=False):
                nd.machine.lower_pen()
                nd.machine.raise_pen()
                nd.block()
                self._close(park=False, sleep=False)
            return self._output(output)
        if mode == "align":
            be = self._open_backend()
            try:
                be.send(f"M3 S{round(nd._settings().s_up)}")
                be.wait_idle()
                if be.live:
                    be.query("$SLP")
            finally:
                be.close()
            self._say("Pen raised, motors off: move the carriage to the Home corner by hand.")
            return self._output(output)
        if mode == "find_home":
            if nd.params.homing_strategy == "switches":
                if self._connect(home=True):
                    self._close(park=False, sleep=False)
            else:
                self._say("This plotter has no homing switches: push the carriage into the Home "
                          "corner (far left) by hand; connect() then pulls off and sets (0, 0).")
            return self._output(output)
        if mode == "utility":
            return self._utility(output)
        raise ValueError(f"unknown mode {mode!r}")

    def _close(self, park: bool, sleep: bool) -> None:
        nd = self.nd
        p = nd.params
        old = (p.park_on_disconnect, p.sleep_on_disconnect)
        p.park_on_disconnect, p.sleep_on_disconnect = park and old[0], sleep and old[1]
        try:
            nd.disconnect()
        finally:
            p.park_on_disconnect, p.sleep_on_disconnect = old

    def _sysinfo(self, output):
        nd = self.nd
        be = self._open_backend()
        try:
            self._say(f"ndgcode {nd.version_string}")
            self._say(f"firmware: {be.fw_version}  build: {be.build_info}  name: {be.nickname or '-'}")
            s = be.settings()
            self._say("  ".join(f"${k}={s[k]}" for k in (1, 22, 32, 100, 110, 120) if k in s))
            nd.fw_version_string = be.fw_version
            nd.nickname = be.nickname
        finally:
            be.close()
        return self._output(output)

    # ------------------------------------------------------------ utilities
    def _utility(self, output):
        nd, o = self.nd, self.nd.options
        cmd = str(o.utility_cmd)
        svg = self.svg_text
        if cmd.startswith("write_name"):
            name = cmd[len("write_name"):]
            be = self._open_backend()
            try:
                be.query(f"$I={name}")
                nd.nickname = name or None
            finally:
                be.close()
            self._say(f"Nickname written: {name!r}" if name else "Nickname cleared.")
            return self._output(output)
        if cmd == "read_name":
            be = self._open_backend()
            try:
                nd.nickname = be.nickname
            finally:
                be.close()
            self._say(f"Nickname: {nd.nickname}" if nd.nickname else "No nickname set.")
            return self._output(output)
        if cmd == "list_names":
            from .grbl import candidate_ports, probe
            names = []
            for port in candidate_ports(nd.params.port_match, nd.params.port_vid):
                info = probe(port, nd.params.baud)
                if info:
                    names.append(info.get("nickname") or port)
            nd.name_list = names
            self._say("\n".join(names) if names else "No plotters found.")
            return self._output(output)
        if cmd in ("lower_pen", "raise_pen", "toggle"):
            st = nd._settings()
            # after the reset on connect the pen is at rest (up), so toggle lowers it
            s = st.s_down if cmd in ("lower_pen", "toggle") else st.s_up
            be = self._open_backend()
            try:
                be.send(f"M3 S{round(s)}")
                be.wait_idle()
            finally:
                be.close()
            return self._output(output)
        if cmd in ("walk_x", "walk_y", "walk_mmx", "walk_mmy"):
            d = float(o.dist) * (1.0 if cmd.startswith("walk_mm") else 25.4)
            dx, dy = (d, 0.0) if cmd.endswith("x") else (0.0, d)
            if self._connect(home=False):
                nd.machine.jog(dx, dy)
                nd.block()
                self._close(park=False, sleep=False)
            nd._warn("each plot_run() connection resets the board's position; "
                     "use the interactive shell for walking + set_home")
            return self._output(output)
        if cmd in ("walk_home", "set_home"):
            nd._warn(f"{cmd}: the board forgets its position on every connection; "
                     "use NextDraw.walk_home()/set_home() in an interactive session or the shell")
            return self._output(output)
        if cmd in ("enable_xy", "disable_xy"):
            be = self._open_backend()
            try:
                if cmd == "disable_xy":
                    be.wait_idle()
                    if be.live:
                        be.query("$SLP")
                else:
                    be.send("$J=G91 G21 X0.01 F100")
                    be.send("$J=G91 G21 X-0.01 F100")
                    be.wait_idle()
            finally:
                be.close()
            return self._output(output)
        if cmd in ("res_read", "res_adj_in", "res_adj_mm"):
            data = read_resume(svg) or {}
            orig = float(data.get("res_dist_mm", 0.0))
            cur = float(data.get("res_adj_mm", orig))
            if cmd == "res_read":
                nd.res_dist = cur
                if data:
                    self._say(f"Plot was paused after {orig:.3f} mm ({orig / 25.4:.3f} inches) of "
                              f"pen-down travel." + (f" The resume position was then adjusted to "
                                                     f"{cur:.3f} mm ({cur / 25.4:.3f} inches)."
                                                     if cur != orig else ""))
                else:
                    self._say("No in-progress plot data found in file.")
                return self._output(output)
            delta = float(o.dist) * (25.4 if cmd == "res_adj_in" else 1.0)
            new = max(0.0, cur + delta)
            nd.res_dist = new
            data.setdefault("res_dist_mm", f"{orig:.3f}")
            data["res_adj_mm"] = f"{new:.3f}"
            self._say(f"After adding an offset of {delta:.3f} mm, the resume position is now "
                      f"set at {new:.3f} mm.")
            out = write_resume(svg, data) if svg else None
            if svg:
                self.svg_text = out
            return self._output(output, out)
        if cmd == "strip_data":
            out = strip_data(svg) if svg else None
            return self._output(output, out)
        if cmd == "bootload":
            nd._warn("bootload is not available on a GRBL board (flash firmware with avrdude)")
            return self._output(output)
        raise ValueError(f"unknown utility_cmd {cmd!r}")

    # ------------------------------------------------------------ plotting
    def _plot(self, output: bool, resume: bool):
        nd, o, p = self.nd, self.nd.options, self.nd.params
        t0 = time.monotonic()
        plob = self.plob()
        if o.digest:
            return plob if output else None
        layers = [l for l in parse_plob(plob) if not l.skip]

        start = 0.0
        data = read_resume(self.svg_text) if resume else None
        if resume:
            if not data:
                raise RuntimeError("res_plot: no ndgcode resume data in this SVG")
            start = float(data.get("res_adj_mm", data.get("res_dist_mm", 0.0)))
        if o.offset_start:
            start = max(0.0, start + parse_dist_mm(o.dist))

        port_saved = o.port
        if o.preview:
            o.port = "null:"
        try:
            if not self._connect(home=True):
                return self._output(output)
            copies = int(o.copies)
            n = 0
            paused_at: Optional[float] = None
            while True:
                n += 1
                paused_at = self._plot_layers(layers, start)
                if paused_at is not None or (copies and n >= copies):
                    break
                start = 0.0
                nd.machine.walk_home()
                nd.block()
                self._wait(float(o.page_delay))
            nd._update_stats()
            estimate = nd.time_estimate
            # as in NextDraw, a resumed plot reports pen-down distance from the document start
            first_start = float(data.get("res_adj_mm", data.get("res_dist_mm", 0.0))) if data else 0.0
            if o.offset_start:
                first_start = max(0.0, first_start + parse_dist_mm(o.dist))
            dist_down = nd.distance_pendown + first_start / 1000.0
            dist_total, lifts = nd.distance_total, nd.pen_lifts
            self._close(park=True, sleep=True)
            nd.time_estimate = estimate
            nd.distance_pendown, nd.distance_total, nd.pen_lifts = dist_down, dist_total, lifts
        finally:
            o.port = port_saved
        nd.time_elapsed = time.monotonic() - t0
        if not o.preview:
            nd.time_estimate = nd.time_elapsed if paused_at is None else nd.time_estimate
        if o.report_time:
            self._report()
        if o.webhook and o.webhook_url and not o.preview:
            self._webhook()
        svg = self.svg_text
        if svg is not None:
            if paused_at is not None:
                nd.res_dist = paused_at
                svg = write_resume(svg, {"res_dist_mm": f"{paused_at:.3f}", "mode": o.mode,
                                         "layer": o.layer})
            else:
                nd.res_dist = 0.0
                svg = _DATA_RE.sub("", svg)
            if o.preview and o.rendering:
                svg = self._render_preview(_PREVIEW_RE.sub("", svg))
        return self._output(output, svg)

    def _wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            time.sleep(min(0.2, end - time.monotonic()))

    def _plot_layers(self, layers: List[Layer], start: float) -> Optional[float]:
        """Plot from pen-down distance ``start``; return the pause distance or None."""
        nd = self.nd
        m = nd.machine
        done = 0.0                      # pen-down distance through the document
        base_settings = m.st
        o = nd.options
        try:
            for li, layer in enumerate(layers):
                layer_len = layer.length
                if done + layer_len <= start and layer_len > 0:
                    done += layer_len
                    continue
                if layer.pause and done >= start and (done > start or start == 0.0) and li > 0:
                    return done
                self._apply_layer(layer, base_settings)
                if layer.delay_ms:
                    m.dwell(layer.delay_ms / 1000.0)
                for stroke in layer.strokes:
                    L = path_length(stroke)
                    if done + L <= start and L > 0:
                        done += L
                        continue
                    pts = stroke
                    if done < start:
                        pts = _cut(stroke, start - done)
                    if self.nd.options.preview and self.nd.options.rendering:
                        self.preview_paths.append((False, pts))
                    m.draw_path(pts)
                    done += L
                m.apply_settings(base_settings)
        except KeyboardInterrupt:
            if not nd.keyboard_pause:
                raise
            pos = self._stop_position()
            nd.errors.code = errors.KEYBOARD
            m.apply_settings(base_settings)
            at = self._distance_at(layers, pos)
            if nd.errors.keyboard:
                raise RuntimeError(errors.MESSAGES[errors.KEYBOARD])
            return at
        return None

    def _apply_layer(self, layer: Layer, base) -> None:
        if layer.speed is None and layer.height is None:
            return
        from .machine import Settings
        o = self.nd.options
        saved = (o.speed_pendown, o.pen_pos_down)
        try:
            if layer.speed is not None:
                o.speed_pendown = layer.speed
            if layer.height is not None:
                o.pen_pos_down = layer.height
            self.nd.machine.apply_settings(Settings(o, self.nd.params, self.nd._warn))
        finally:
            o.speed_pendown, o.pen_pos_down = saved

    def _stop_position(self) -> Point:
        nd = self.nd
        nd.stop()
        return nd.machine.pos

    def _distance_at(self, layers: List[Layer], pos: Point) -> float:
        """Pen-down distance of the stroke point closest to ``pos``."""
        best, best_d = 0.0, math.inf
        done = 0.0
        for layer in layers:
            for stroke in layer.strokes:
                acc = 0.0
                for a, b in zip(stroke, stroke[1:]):
                    seg = dist(a, b)
                    t = _project(pos, a, b)
                    q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                    d = dist(pos, q)
                    if d < best_d:
                        best_d, best = d, done + acc + seg * t
                    acc += seg
                done += acc
        return best

    def _webhook(self) -> None:
        """POST {value1: document, value2: elapsed, value3: port} as NextDraw does."""
        import json
        import urllib.request
        nd, o = self.nd, self.nd.options
        port = o.port if isinstance(o.port, str) else ""
        body = json.dumps({
            "value1": os.path.basename(self.svg_path) if self.svg_path else "document",
            "value2": time.strftime("%H:%M:%S", time.gmtime(nd.time_elapsed)),
            "value3": port or "",
        }).encode("utf-8")
        req = urllib.request.Request(str(o.webhook_url), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception as e:   # a failed notification must not fail the plot
            nd._warn(f"webhook failed: {e}")

    def _report(self) -> None:
        nd = self.nd
        el, est = nd.time_elapsed, nd.time_estimate
        fmt = lambda s: time.strftime("%H:%M:%S", time.gmtime(s)) if s >= 60 else f"{s:.1f} s"
        if nd.options.preview:
            self._say(f"Estimated print time: {fmt(est)}")
        else:
            self._say(f"Elapsed time: {fmt(el)}")
        self._say(f"Length of path to draw: {nd.distance_pendown:.2f} m")
        self._say(f"Pen-up travel distance: {nd.distance_total - nd.distance_pendown:.2f} m")
        if nd.params.report_lifts:
            self._say(f"Total number of pen lifts: {nd.pen_lifts}")

    def _render_preview(self, svg: str) -> str:
        """Add a preview layer with the pen-down paths, in the SVG's user units.

        Without auto-rotation the plot frame equals the page frame; with a
        rotated plot the preview shows the plotted (rotated) orientation.
        """
        color = self.nd.params.preview_color_down
        k = _user_units_per_mm(svg)
        parts = []
        for _, pts in self.preview_paths:
            pp = " ".join(f"{x * k:.4f},{y * k:.4f}" for x, y in pts)
            parts.append(f'<polyline points="{pp}" fill="none" stroke="{color}" '
                         f'stroke-width="{0.3 * k:.4f}"/>')
        group = f'<g id="ndgcode-preview" inkscape:groupmode="layer" inkscape:label="% ndgcode preview">' \
                f'{"".join(parts)}</g>\n'
        if "xmlns:inkscape=" not in svg:
            svg = re.sub(r"<svg\b", f'<svg xmlns:inkscape="{NS_INK}"', svg, count=1)
        return re.sub(r"</svg>\s*$", group + "</svg>\n", svg, count=1)


_UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6, "px": 25.4 / 96, "": 25.4 / 96}


def _length_mm(v: Optional[str]) -> Optional[float]:
    if not v:
        return None
    m = re.match(r"^\s*([0-9.eE+-]+)\s*([a-z%]*)\s*$", v)
    if not m or m.group(2) == "%":
        return None
    return float(m.group(1)) * _UNIT_MM.get(m.group(2), 25.4 / 96)


def _user_units_per_mm(svg: str) -> float:
    """SVG user units per mm, from the root width and viewBox."""
    m = re.search(r"<svg\b[^>]*>", svg, re.S)
    if not m:
        return 1.0
    tag = m.group(0)
    w = re.search(r'\bwidth="([^"]*)"', tag)
    vb = re.search(r'\bviewBox="([^"]*)"', tag)
    w_mm = _length_mm(w.group(1)) if w else None
    if vb:
        parts = vb.group(1).replace(",", " ").split()
        if len(parts) == 4 and w_mm:
            return float(parts[2]) / w_mm
    return 96 / 25.4   # user units are px


def _project(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return 0.0
    return max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))


def _cut(stroke: List[Point], offset: float) -> List[Point]:
    """The part of a stroke after ``offset`` mm of its length."""
    acc = 0.0
    for i, (a, b) in enumerate(zip(stroke, stroke[1:])):
        seg = dist(a, b)
        if acc + seg >= offset:
            t = (offset - acc) / seg if seg else 0.0
            q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            return [q] + list(stroke[i + 1:])
        acc += seg
    return [stroke[-1], stroke[-1]]
