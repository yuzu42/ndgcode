"""The NextDraw-compatible class.

``ndgcode.NextDraw`` mirrors the Bantam Tools NextDraw Python API (functions,
options, variables, errors) and drives a GRBL plotter through the planner in
:mod:`ndgcode.machine`. See SPEC.md section 4 for the mapping of each item.
"""

from __future__ import annotations

import sys
import time
from typing import List, Optional, Sequence

from . import __version__, errors
from .backends import Backend, FileBackend, NullBackend
from .geometry import as_points, unit_scale
from .machine import Machine, Settings
from .options import Options, Params, load_config_file

_NOT_APPLICABLE_OPTIONS = {"model": 8, "penlift": 1, "port_config": 0}


class NextDraw:
    """Drop-in replacement for ``nextdraw.NextDraw`` on a GRBL plotter."""

    def __init__(self):
        self.options = Options()
        self.params = Params()
        self.errors = errors.Errors()
        self.version_string = __version__
        self.connected = False
        self.fw_version_string: Optional[str] = None
        self.nickname: Optional[str] = None
        self.time_elapsed = 0.0
        self.time_estimate = 0.0
        self.distance_pendown = 0.0
        self.distance_total = 0.0
        self.pen_lifts = 0
        self.res_dist = 0.0
        self.name_list: List[str] = []
        self.keyboard_pause = False
        self.warnings: List[str] = []
        self.backend: Optional[Backend] = None
        self.machine: Optional[Machine] = None
        self._context: Optional[str] = None
        self._scale = 25.4
        self._warned = set()
        self._t0 = 0.0
        self._plot = None   # plot-context state (ndgcode.plot.PlotJob)

    # ================================================================ helpers
    def _warn(self, msg: str) -> None:
        if msg in self._warned:
            return
        self._warned.add(msg)
        self.warnings.append(msg)
        print(f"ndgcode: {msg}", file=sys.stderr)

    def _settings(self) -> Settings:
        for name in self.options.unknown():
            self._warn(f"unknown option '{name}' is ignored")
        for name, default in _NOT_APPLICABLE_OPTIONS.items():
            if getattr(self.options, name) != default:
                self._warn(f"option '{name}' has no effect on a GRBL plotter")
        return Settings(self.options, self.params, self._warn)

    def _require(self) -> Machine:
        if not self.connected or self.machine is None:
            raise RuntimeError("Not connected to the plotter; call connect() first.")
        return self.machine

    def _fail(self, exc: Exception, code: Optional[int] = None) -> None:
        """Record an error the NextDraw way; raise if the matching flag is set."""
        code = code if code is not None else getattr(exc, "code", errors.DISCONNECT)
        self.errors.code = code
        self._warn(str(exc))
        if code in (errors.DISCONNECT, errors.POWER, errors.HOMING) or isinstance(exc, errors.GrblAlarm):
            self._drop_connection()
        if self.errors.should_raise(code):
            raise RuntimeError(f"{errors.MESSAGES.get(code, 'error')}: {exc}") from exc

    def _drop_connection(self) -> None:
        self.connected = False
        be, self.backend = self.backend, None
        if be is not None:
            try:
                be.close()
            except Exception:
                pass

    def _run(self, fn, *args):
        """Run a machine call; map backend errors onto errors.code."""
        try:
            return fn(*args)
        except errors.NdGcodeError as e:
            self._fail(e)
        except KeyboardInterrupt:
            if self.keyboard_pause:
                self._keyboard_stop()
                return None
            raise

    def _keyboard_stop(self) -> None:
        self.stop()
        self.errors.code = errors.KEYBOARD
        if self.errors.keyboard:
            raise RuntimeError(errors.MESSAGES[errors.KEYBOARD])

    def _update_stats(self) -> None:
        m = self.machine
        if m is None:
            return
        self.distance_pendown = m.dist_down / 1000.0   # meters, as in NextDraw
        self.distance_total = m.dist_total / 1000.0
        self.pen_lifts = m.pen_lifts
        self.time_estimate = m.time_estimate_s

    # ================================================================ backend
    def make_backend(self, port=None) -> Backend:
        """Create the backend for ``options.port`` (see SPEC 4.3)."""
        port = self.options.port if port is None else port
        p = self.params
        if isinstance(port, Backend):
            return port
        if isinstance(port, str) and port.startswith("file:"):
            return FileBackend(port[5:], header=[
                f"ndgcode {__version__} export",
                "start with the carriage pushed into the Home corner (far left)",
            ])
        if isinstance(port, str) and port in ("null:", "preview:"):
            return NullBackend()
        from .grbl import SerialBackend, candidate_ports
        if isinstance(port, str) and port.startswith("fake:"):
            from .fakegrbl import factory
            return SerialBackend("fake", transport_factory=factory(), rx_buffer=p.rx_buffer_bytes,
                                 mode=p.stream_mode, banner_timeout=1.0)
        if isinstance(port, str) and not port.startswith(("/", "COM", "\\\\", "socket:", "rfc2217:")):
            # a nickname (see write_name): probe the candidate ports for it
            from .grbl import probe
            for dev in candidate_ports(p.port_match, p.port_vid):
                info = probe(dev, p.baud)
                if info and info.get("nickname") == port:
                    port = dev
                    break
            else:
                raise errors.ConnectError(f"no plotter with the nickname {port!r} found")
        if port is None:
            ports = candidate_ports(p.port_match, p.port_vid)
            if not ports:
                raise errors.ConnectError("no CH340 serial port found; is the plotter plugged in?")
            port = ports[0]
            if len(ports) > 1:
                self._warn(f"several candidate ports {ports}; using {port} (set options.port)")
        return SerialBackend(str(port), p.baud, rx_buffer=p.rx_buffer_bytes, mode=p.stream_mode,
                             banner_timeout=p.banner_timeout_s)

    def _check_board(self) -> None:
        be = self.backend
        if not be.live:
            return
        try:
            s = be.settings()
        except errors.NdGcodeError as e:
            self._warn(f"could not read GRBL settings: {e}")
            return
        if s.get(32) == "1":
            self._warn("GRBL laser mode is on ($32=1); the pen may not lift between moves")
        if s.get(1) not in (None, "255"):
            self._warn(f"motors release when idle ($1={s.get(1)}); the carriage can be pushed "
                       "out of position (set $1=255)")
        self._apply_accel(s)

    def _apply_accel(self, s=None) -> None:
        be, m = self.backend, self.machine
        if be is None or not be.live or not self.params.write_accel:
            return
        target = m.st.accel if m is not None else Settings(self.options, self.params).accel
        s = s if s is not None else be.settings()
        for key in (120, 121):
            try:
                cur = float(s.get(key, "nan"))
            except ValueError:
                cur = float("nan")
            if not abs(cur - target) < 0.5:
                be.query(f"${key}={target:.3f}")

    # ================================================================ interactive
    def interactive(self) -> None:
        """Initialize the Interactive context."""
        self._context = "interactive"

    def connect(self) -> bool:
        """Open the connection, raise the pen, home. Returns True on success."""
        if self.connected:
            return True
        self.errors.code = errors.OK
        try:
            st = self._settings()
            self._scale = unit_scale(self.options.units)
            self.backend = self.make_backend()
            self.backend.open()
        except errors.NdGcodeError as e:
            self.backend = None
            self.errors.code = errors.CONNECT
            self._warn(str(e))
            if self.errors.connect:
                raise RuntimeError(f"{errors.MESSAGES[errors.CONNECT]}: {e}") from e
            return False
        self.fw_version_string = self.backend.fw_version
        self.nickname = self.backend.nickname
        self.machine = Machine(st, self.backend, self._warn)
        try:
            self._check_board()
            self.machine.start(home=bool(self.options.homing), strategy=self.params.homing_strategy)
        except errors.NdGcodeError as e:
            homing = isinstance(e, (errors.HomingError, errors.GrblError))
            code = errors.HOMING if homing else getattr(e, "code", errors.CONNECT)
            self.errors.code = code
            self._warn(str(e))
            self._drop_connection()
            if self.errors.should_raise(code) or (code == errors.CONNECT and self.errors.connect):
                raise RuntimeError(f"{errors.MESSAGES.get(code, 'error')}: {e}") from e
            return False
        self.connected = True
        self._t0 = time.monotonic()
        return True

    def disconnect(self) -> None:
        """Pen up, park in the Home corner, motors off, close the port."""
        if self.backend is None:
            self.connected = False
            return
        be, m = self.backend, self.machine
        try:
            if m is not None and self.connected:
                m.finish(park=bool(self.params.park_on_disconnect))
                be.wait_idle()
                if be.live and self.params.sleep_on_disconnect:
                    be.query("$SLP")
        except errors.NdGcodeError as e:
            self._warn(f"during disconnect: {e}")
        finally:
            self._update_stats()
            self.time_elapsed = time.monotonic() - self._t0 if self._t0 else 0.0
            self._drop_connection()

    def update(self) -> None:
        """Apply changed options (speeds, pen heights, accel, units)."""
        m = self._require()
        st = self._settings()
        self._update_stats()
        self._scale = unit_scale(self.options.units)
        m.apply_settings(st)
        if self.backend.live and self.params.write_accel:
            self._run(self.backend.wait_idle)
            self._run(self._apply_accel)

    def _xy(self, x, y):
        return float(x) * self._scale, float(y) * self._scale

    def goto(self, final_x, final_y) -> None:
        """Absolute move; keeps the current pen state."""
        m = self._require()
        self._run(m.goto, self._xy(final_x, final_y))

    def moveto(self, final_x, final_y) -> None:
        """Absolute pen-up move."""
        m = self._require()
        self._run(m.penup)
        self._run(m.goto, self._xy(final_x, final_y))

    def lineto(self, final_x, final_y) -> None:
        """Absolute pen-down move."""
        m = self._require()
        self._run(m.pendown)
        self._run(m.goto, self._xy(final_x, final_y))

    def _rel(self, dx, dy):
        tx, ty = self._require().turtle
        ddx, ddy = self._xy(dx, dy)
        return tx + ddx, ty + ddy

    def go(self, delta_x, delta_y) -> None:
        """Relative move; keeps the current pen state."""
        m = self._require()
        self._run(m.goto, self._rel(delta_x, delta_y))

    def move(self, delta_x, delta_y) -> None:
        """Relative pen-up move."""
        m = self._require()
        self._run(m.penup)
        self._run(m.goto, self._rel(delta_x, delta_y))

    def line(self, delta_x, delta_y) -> None:
        """Relative pen-down move."""
        m = self._require()
        self._run(m.pendown)
        self._run(m.goto, self._rel(delta_x, delta_y))

    def penup(self) -> None:
        self._run(self._require().penup)

    def pendown(self) -> None:
        self._run(self._require().pendown)

    def draw_path(self, vertex_list: Sequence[Sequence[float]]) -> None:
        """Clip, then draw a pen-down path through the vertices."""
        m = self._require()
        pts = as_points(vertex_list, self._scale)
        if len(pts) < 2:
            raise ValueError("draw_path() needs a list of at least two [x, y] vertices")
        self._run(m.draw_path, pts)

    def delay(self, time_ms) -> None:
        """Queued, machine-timed delay (G4)."""
        self._run(self._require().dwell, float(time_ms) / 1000.0)

    def block(self) -> None:
        """Wait until all queued motion has finished."""
        self._require()
        self._run(self.backend.wait_idle)
        self._update_stats()

    def current_pos(self):
        m = self._require()
        return (m.pos[0] / self._scale, m.pos[1] / self._scale)

    def turtle_pos(self):
        m = self._require()
        return (m.turtle[0] / self._scale, m.turtle[1] / self._scale)

    def current_pen(self) -> bool:
        """True if the physical pen is up."""
        return self._require().pen_up

    def turtle_pen(self) -> bool:
        """True if the turtle pen is up."""
        return self._require().turtle_pen_up

    def usb_command(self, command: str) -> None:
        """Send a raw G-code or ``$`` line. Bypasses position tracking."""
        self._require()
        self._warn("usb_command() bypasses position tracking; use with care")
        self._run(self.backend.send, command)

    def usb_query(self, query: str) -> str:
        """Send a raw line and return its response (e.g. ``$$``, ``$I``, ``?``)."""
        self._require()
        if query.strip() == "?":
            st = self._run(self.backend.status)
            return st["raw"] if st else ""
        lines = self._run(self.backend.query, query) or []
        return "\n".join(lines).strip()

    def load_config(self, config_file) -> None:
        """Load a ``nextdraw_conf.py``-style file (options and params)."""
        load_config_file(config_file, self.options, self.params)

    # ================================================================ extensions
    def status(self) -> Optional[dict]:
        """Parsed GRBL status report (state, MPos, FS, Ov, ...)."""
        self._require()
        return self._run(self.backend.status)

    def grbl_settings(self) -> dict:
        """``$$`` as ``{number: value}``. Read-only."""
        self._require()
        return self._run(self.backend.settings) or {}

    def gcode(self, line: str) -> None:
        """Alias of :meth:`usb_command`."""
        self.usb_command(line)

    def home(self) -> None:
        """Re-run homing. Manual strategy: assumes the carriage was pushed into the corner."""
        m = self._require()
        self._run(self.backend.wait_idle)
        if self.backend.live:
            self._run(self.backend.soft_reset)
        self._run(m.start, True, self.params.homing_strategy)

    def set_home(self) -> None:
        """Make the current carriage position the new (0, 0)."""
        self._run(self._require().set_home)

    def walk_home(self) -> None:
        """Pen-up move back to (0, 0)."""
        self._run(self._require().walk_home)

    def jog(self, delta_x, delta_y, feed=None) -> None:
        """Unclipped relative jog in current units (cancel with jog_cancel())."""
        m = self._require()
        dx, dy = self._xy(delta_x, delta_y)
        self._run(m.jog, dx, dy, feed)

    def jog_cancel(self) -> None:
        from .grbl import RT_JOG_CANCEL
        self._require()
        self._run(self.backend.realtime, RT_JOG_CANCEL)

    def pause(self) -> None:
        """Feed hold: decelerate and stop; resume() continues."""
        from .grbl import RT_HOLD
        self._require()
        self._run(self.backend.realtime, RT_HOLD)

    def resume(self) -> None:
        from .grbl import RT_RESUME
        self._require()
        self._run(self.backend.realtime, RT_RESUME)

    def stop(self) -> None:
        """Stop now: feed hold, discard queued motion, pen up.

        The position is kept when the machine had come to rest (feed hold
        first), so the session can continue.
        """
        if self.backend is None or self.machine is None:
            return
        be, m = self.backend, self.machine
        from .grbl import RT_HOLD
        try:
            if be.live:
                be.realtime(RT_HOLD)
                for _ in range(50):
                    st = be.status()
                    if st and st.get("substate") in ("Hold:0", "Idle"):
                        break
                    time.sleep(0.05)
                st = be.status() or {}
                be.soft_reset()
                if "MPos" in st:
                    m.pos = m.from_machine(st["MPos"][0], st["MPos"][1])
                    m.turtle = m.pos
                be.send("G21 G90 G17 G94")
            m.s_now = None
            m.pen_up = False
            m.turtle_pen_up = True
            m._f_last = None
            m.raise_pen()
        except errors.NdGcodeError as e:
            self._fail(e)

    def feed_override(self, percent: int) -> None:
        """Set the feed override (10-200 %) via real-time commands."""
        from .grbl import RT_FEED_100, RT_FEED_MINUS_1, RT_FEED_MINUS_10, RT_FEED_PLUS_1, RT_FEED_PLUS_10
        self._require()
        target = int(min(200, max(10, percent)))
        seq = [RT_FEED_100]
        delta = target - 100
        step10, step1 = (RT_FEED_PLUS_10, RT_FEED_PLUS_1) if delta > 0 else (RT_FEED_MINUS_10, RT_FEED_MINUS_1)
        seq += [step10] * (abs(delta) // 10) + [step1] * (abs(delta) % 10)
        for b in seq:
            self._run(self.backend.realtime, b)

    # ================================================================ plot context
    def plot_setup(self, svg_input=None) -> None:
        """Initialize the Plot context with an SVG file, SVG string, or None."""
        from .plot import PlotJob
        self._context = "plot"
        self._plot = PlotJob(self, svg_input)

    def plot_run(self, output: bool = False):
        """Run the action selected with ``options.mode`` (see SPEC 4.4)."""
        if self._plot is None:
            raise RuntimeError("call plot_setup() before plot_run()")
        return self._plot.run(output)
