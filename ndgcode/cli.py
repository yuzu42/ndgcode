"""Command line: ``python -m ndgcode <command>`` (or ``ndgcode`` when installed).

Every connection resets the board, so commands that need a known position
(jogging, setting home, calibration) live in the interactive ``shell``, which
keeps one connection open.
"""

from __future__ import annotations

import argparse
import cmd
import shlex
import sys
from typing import List, Optional

from . import __version__, errors


# ---------------------------------------------------------------- helpers
def _nd(args, units: int = 2):
    from .api import NextDraw
    nd = NextDraw()
    nd.interactive()
    nd.options.units = units
    if getattr(args, "config", None):
        nd.load_config(args.config)
    if getattr(args, "port", None):
        nd.options.port = args.port
    if getattr(args, "fake", False):
        nd.options.port = "fake:"
    if getattr(args, "no_home", False):
        nd.options.homing = False
    return nd


def _backend(args):
    """Open a bare backend (no motion) for info/stream/check."""
    from .grbl import SerialBackend, candidate_ports
    from .fakegrbl import factory
    from .conf import PARAMS
    if getattr(args, "fake", False):
        be = SerialBackend("fake", transport_factory=factory(), banner_timeout=1.0)
    else:
        port = args.port
        if port is None:
            ports = candidate_ports(PARAMS["port_match"], PARAMS["port_vid"])
            if not ports:
                raise SystemExit("no CH340 serial port found; is the plotter plugged in?")
            port = ports[0]
        be = SerialBackend(port, PARAMS["baud"])
    be.open()
    return be


def _progress(i, n):
    print(f"\r{i}/{n} lines", end="" if i < n else "\n", flush=True)


# ---------------------------------------------------------------- commands
def cmd_ports(args) -> int:
    from .grbl import candidate_ports, probe
    from .conf import PARAMS
    ports = candidate_ports(PARAMS["port_match"], PARAMS["port_vid"])
    if not ports:
        print("no CH340 serial ports found")
        return 1
    for p in ports:
        if args.probe:
            info = probe(p)
            print(f"{p}: {info['banner'] if info else 'no GRBL answer'}"
                  + (f" (name: {info['nickname']})" if info and info.get("nickname") else ""))
        else:
            print(p)
    return 0


def cmd_info(args) -> int:
    be = _backend(args)
    try:
        print(f"port:      {be.port}")
        print(f"banner:    {be.banner}")
        print(f"firmware:  {be.fw_version}   build: {be.build_info}   name: {be.nickname or '-'}")
        s = be.settings()
        for k, label in ((1, "idle hold"), (3, "dir invert"), (22, "homing"), (32, "laser mode"),
                         (100, "X steps/mm"), (101, "Y steps/mm"), (110, "X max mm/min"),
                         (120, "X accel mm/s2"), (130, "X travel"), (131, "Y travel")):
            print(f"${k:<4}= {s.get(k, '?'):<10} {label}")
        st = be.status()
        print(f"status:    {st['raw'] if st else '-'}")
    finally:
        be.close()
    return 0


def cmd_stream(args) -> int:
    from .stream import stream
    be = _backend(args)
    try:
        with open(args.file, encoding="ascii", errors="replace") as fh:
            secs = stream(be, fh, _progress)
        print(f"done in {secs:.1f} s")
    except KeyboardInterrupt:
        print("\nstopped (feed hold, queue discarded, pen up)")
        return 130
    finally:
        be.close()
    return 0


def cmd_check(args) -> int:
    from .stream import check
    be = _backend(args)
    try:
        with open(args.file, encoding="ascii", errors="replace") as fh:
            problems = check(be, fh)
    finally:
        be.close()
    for n, line, msg in problems:
        print(f"line {n}: {line}\n    {msg}")
    print("OK: no errors" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


def cmd_run(args) -> int:
    from .runner import run_script
    port = None
    if args.export:
        port = "file:" + args.export
    elif args.preview:
        port = "null:"
    elif args.fake:
        port = "fake:"
    elif args.port:
        port = args.port
    run_script(args.script, port=port, argv=args.script_args)
    return 0


def cmd_plot(args) -> int:
    from .api import NextDraw
    nd = NextDraw()
    if args.config:
        nd.load_config(args.config)
    nd.plot_setup(args.svg)
    if args.layer is not None:
        nd.options.mode = "layers"
        nd.options.layer = args.layer
    nd.options.preview = bool(args.preview)
    nd.options.report_time = True
    for name in ("reordering", "copies"):
        v = getattr(args, name, None)
        if v is not None:
            setattr(nd.options, name, v)
    if args.hiding:
        nd.options.hiding = True
    if args.export:
        nd.options.port = "file:" + args.export
    elif args.fake:
        nd.options.port = "fake:"
    elif args.port:
        nd.options.port = args.port
    out = nd.plot_run(bool(args.output))
    if args.output and out:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    return 0 if nd.errors.code == 0 else 1


def cmd_shell(args) -> int:
    nd = _nd(args)
    if not nd.connect():
        print(f"could not connect (error {nd.errors.code})")
        return 1
    try:
        Shell(nd).cmdloop()
    finally:
        if nd.connected:
            nd.disconnect()
    return 0


# ---------------------------------------------------------------- shell
class Shell(cmd.Cmd):
    intro = ("ndgcode shell. Coordinates in mm, NextDraw frame (0,0 = Home, +y toward you).\n"
             "Type help or ? to list commands. 'quit' parks the carriage and ends.")
    prompt = "ndgcode> "

    def __init__(self, nd):
        super().__init__()
        self.nd = nd

    def _floats(self, arg: str, n: Optional[int] = None) -> List[float]:
        vals = [float(v) for v in shlex.split(arg)]
        if n is not None and len(vals) != n:
            raise ValueError(f"expected {n} numbers")
        return vals

    def onecmd(self, line):
        try:
            return super().onecmd(line)
        except (ValueError, RuntimeError, errors.NdGcodeError) as e:
            print(f"error: {e}")
        except KeyboardInterrupt:
            print("\nstopping")
            self.nd.stop()
        return False

    def emptyline(self):
        return False

    def do_status(self, arg):
        """status: machine state, positions, pen."""
        st = self.nd.status()
        print(st["raw"] if st else "no status")
        print(f"position {self.nd.current_pos()}  turtle {self.nd.turtle_pos()}  "
              f"pen {'up' if self.nd.current_pen() else 'down'}")

    def do_settings(self, arg):
        """settings: print GRBL $$ settings."""
        for k, v in sorted(self.nd.grbl_settings().items()):
            print(f"${k}={v}")

    def do_pen(self, arg):
        """pen up | down | cycle | <S value>: move the pen (S = raw servo value)."""
        a = arg.strip().lower()
        m = self.nd.machine
        if a == "up":
            self.nd.penup()
        elif a == "down":
            self.nd.pendown()
        elif a == "cycle":
            m.lower_pen()
            m.raise_pen()
        elif a:
            m.servo(float(a))
        else:
            print(self.do_pen.__doc__)
        self.nd.block()

    def do_jog(self, arg):
        """jog dx dy [feed]: unclipped relative move in mm (pen up)."""
        v = self._floats(arg)
        self.nd.jog(v[0], v[1], v[2] if len(v) > 2 else None)
        self.nd.block()

    def do_goto(self, arg):
        """goto x y: pen-up move to an absolute position (clipped to the bed)."""
        x, y = self._floats(arg, 2)
        self.nd.moveto(x, y)
        self.nd.block()

    def do_line(self, arg):
        """line x y: pen-down move to an absolute position."""
        x, y = self._floats(arg, 2)
        self.nd.lineto(x, y)
        self.nd.block()

    def do_draw(self, arg):
        """draw x1 y1 x2 y2 ...: draw_path through the points."""
        v = self._floats(arg)
        if len(v) < 4 or len(v) % 2:
            raise ValueError("need pairs of numbers, at least two points")
        self.nd.draw_path([v[i:i + 2] for i in range(0, len(v), 2)])
        self.nd.block()

    def do_square(self, arg):
        """square [size] [x y]: draw a test square (default 50 mm at 20 20)."""
        v = self._floats(arg) if arg.strip() else []
        size = v[0] if v else 50.0
        x, y = (v[1], v[2]) if len(v) >= 3 else (20.0, 20.0)
        self.nd.draw_path([[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]])
        self.nd.block()

    def do_sethome(self, arg):
        """sethome: make the current position the new (0, 0)."""
        self.nd.set_home()

    def do_walkhome(self, arg):
        """walkhome: pen-up move to (0, 0)."""
        self.nd.walk_home()
        self.nd.block()

    def do_home(self, arg):
        """home: carriage was pushed into the Home corner; reset and pull off again."""
        self.nd.home()
        self.nd.block()

    def do_feed(self, arg):
        """feed <percent>: feed override 10-200 % for the running motion."""
        self.nd.feed_override(int(float(arg)))

    def do_speed(self, arg):
        """speed <pendown %> [penup %]: set speed_pendown / speed_penup."""
        v = self._floats(arg)
        self.nd.options.speed_pendown = v[0]
        if len(v) > 1:
            self.nd.options.speed_penup = v[1]
        self.nd.update()
        st = self.nd.machine.st
        print(f"pen-down {st.feed_down:.0f} mm/min, pen-up {st.feed_up:.0f} mm/min "
              f"(cap {st.feed_cap:.0f})")

    def do_pause(self, arg):
        """pause: feed hold."""
        self.nd.pause()

    def do_resume(self, arg):
        """resume: continue after pause."""
        self.nd.resume()

    def do_gcode(self, arg):
        """gcode <line>: send a raw line (position tracking is not updated)."""
        print(self.nd.usb_query(arg) or "ok")

    def do_calibrate(self, arg):
        """calibrate pen | bed [file.json]: guided pen-up / bed height calibration."""
        from .calibrate import calibrate_bed, calibrate_pen
        parts = shlex.split(arg)
        if not parts or parts[0] not in ("pen", "bed"):
            print(self.do_calibrate.__doc__)
            return
        if parts[0] == "pen":
            calibrate_pen(self.nd)
        else:
            calibrate_bed(self.nd, save_to=parts[1] if len(parts) > 1 else "bed_map.json")

    def do_quit(self, arg):
        """quit: pen up, park in the Home corner, motors off, disconnect."""
        return True

    do_exit = do_quit
    do_EOF = do_quit


# ---------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ndgcode", description=__doc__.split("\n")[0])
    ap.add_argument("--version", action="version", version=f"ndgcode {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    def conn(p):
        p.add_argument("--port", help="serial port (default: auto-detect the CH340)")
        p.add_argument("--fake", action="store_true", help="use the simulated GRBL board")

    p = sub.add_parser("ports", help="list candidate serial ports")
    p.add_argument("--probe", action="store_true", help="open each port and ask for GRBL (resets the board)")
    p.set_defaults(fn=cmd_ports)

    p = sub.add_parser("info", help="firmware, key settings and status (no motion)")
    conn(p)
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("shell", help="interactive session (jog, pen, calibrate, ...)")
    conn(p)
    p.add_argument("--config", help="ndgcode/nextdraw config file")
    p.add_argument("--no-home", action="store_true", help="skip the pull-off on connect")
    p.set_defaults(fn=cmd_shell)

    p = sub.add_parser("run", help="run a NextDraw script on the plotter (or export it)")
    conn(p)
    p.add_argument("script")
    p.add_argument("script_args", nargs="*", help="arguments for the script (after --)")
    p.add_argument("--export", metavar="OUT.gcode", help="write G-code instead of plotting")
    p.add_argument("--preview", action="store_true", help="no output; estimates only")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("plot", help="plot an SVG (needs the nextdraw library)")
    conn(p)
    p.add_argument("svg")
    p.add_argument("--config")
    p.add_argument("--layer", type=int)
    p.add_argument("--preview", action="store_true")
    p.add_argument("--reordering", type=int)
    p.add_argument("--hiding", action="store_true")
    p.add_argument("--copies", type=int)
    p.add_argument("--export", metavar="OUT.gcode")
    p.add_argument("--output", metavar="OUT.svg", help="save the output SVG (resume data)")
    p.set_defaults(fn=cmd_plot)

    p = sub.add_parser("stream", help="stream an existing .gcode file")
    conn(p)
    p.add_argument("file")
    p.set_defaults(fn=cmd_stream)

    p = sub.add_parser("check", help="validate a .gcode file in GRBL check mode (no motion)")
    conn(p)
    p.add_argument("file")
    p.set_defaults(fn=cmd_check)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
