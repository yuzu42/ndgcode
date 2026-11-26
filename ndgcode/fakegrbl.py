"""A simulated GRBL 1.1 board behind a serial-port-like interface.

Used by the tests and by ``port="fake:"`` for trying things without a machine.
It models the parts the backend depends on: banner on open, the 128-byte RX
buffer (an overflow is recorded), ``ok``/``error`` responses, status reports,
real-time commands, jogging, ``$SLP``, ``$C`` and settings. Motion is
instantaneous unless ``lines_per_read`` throttles it.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Dict, List, Optional

from .backends import SIM_SETTINGS

BANNER = b"\r\nVigoWriter 1.1f ['$' for help]\r\n"
_WORD = re.compile(r"([A-Z])(-?\d*\.?\d+)")


class FakeGrblSerial:
    def __init__(self, port: str = "fake", baud: int = 115200, reset_on_open: bool = True,
                 rx_size: int = 128, lines_per_read: int = 4,
                 settings: Optional[Dict[int, str]] = None, name: str = ""):
        self.port = port
        self.rx_size = rx_size
        self.lines_per_read = lines_per_read
        self.settings = dict(SIM_SETTINGS if settings is None else settings)
        self.name = name
        self._lock = threading.Lock()
        self.rx = bytearray()
        self.out = bytearray()
        self.closed = False
        self.overflow = False
        self.max_rx = 0
        self.lines: List[str] = []      # every processed line, for assertions
        self.realtime_log: List[bytes] = []
        self.hold = False
        self.fail_next_write: Optional[Exception] = None
        self._reset_state()
        if reset_on_open:
            self.out += BANNER

    # ------------------------------------------------------------ state
    def _reset_state(self):
        self.mpos = [0.0, 0.0, 0.0]
        self.absolute = True
        self.state = "Idle"
        self.spindle = 0.0
        self.spindle_on = False
        self.feed = 0.0
        self.feed_ov = 100
        self.check_mode = False
        self.hold = False

    def status_line(self) -> str:
        state = self.state
        if self.hold:
            state = "Hold:0"
        elif state == "Idle" and self._has_line():
            state = "Run"
        return (f"<{state}|MPos:{self.mpos[0]:.3f},{self.mpos[1]:.3f},{self.mpos[2]:.3f}"
                f"|FS:{self.feed:.0f},{self.spindle if self.spindle_on else 0:.0f}"
                f"|Ov:{self.feed_ov},100,100>")

    def _has_line(self) -> bool:
        return b"\n" in self.rx

    # ------------------------------------------------------------ serial API
    def write(self, data: bytes) -> int:
        with self._lock:
            if self.closed:
                raise OSError("port closed")
            if self.fail_next_write is not None:
                e, self.fail_next_write = self.fail_next_write, None
                raise e
            for b in data:
                ch = bytes([b])
                if ch in (b"?", b"!", b"~", b"\x18", b"\x85") or 0x90 <= b <= 0x9D:
                    self._realtime(ch)
                else:
                    self.rx.append(b)
            self.max_rx = max(self.max_rx, len(self.rx))
            if len(self.rx) > self.rx_size:
                self.overflow = True
            return len(data)

    def read(self, n: int = 1) -> bytes:
        with self._lock:
            if self.closed:
                raise OSError("port closed")
            if not self.hold:
                for _ in range(self.lines_per_read):
                    if b"\n" not in self.rx:
                        break
                    raw, _, rest = bytes(self.rx).partition(b"\n")
                    self.rx = bytearray(rest)
                    self._line(raw.decode("ascii", "replace").strip())
            chunk = bytes(self.out[:n])
            del self.out[:n]
        if not chunk:
            time.sleep(0.002)
        return chunk

    def close(self) -> None:
        with self._lock:
            self.closed = True

    # ------------------------------------------------------------ behavior
    def _say(self, *lines: str) -> None:
        for l in lines:
            self.out += l.encode("ascii") + b"\r\n"

    def _realtime(self, ch: bytes) -> None:
        self.realtime_log.append(ch)
        if ch == b"?":
            self._say(self.status_line())
        elif ch == b"!":
            self.hold = True
        elif ch == b"~":
            self.hold = False
        elif ch == b"\x18":
            self.rx.clear()
            self._reset_state()
            self.out += BANNER
        elif ch == b"\x90":
            self.feed_ov = 100
        elif ch == b"\x91":
            self.feed_ov = min(200, self.feed_ov + 10)
        elif ch == b"\x92":
            self.feed_ov = max(10, self.feed_ov - 10)
        elif ch == b"\x93":
            self.feed_ov = min(200, self.feed_ov + 1)
        elif ch == b"\x94":
            self.feed_ov = max(10, self.feed_ov - 1)

    def _line(self, line: str) -> None:
        if not line:
            return
        self.lines.append(line)
        if self.state == "Sleep":
            return  # a sleeping GRBL ignores everything until reset
        if line.startswith("$"):
            self._system(line)
            return
        ok = self._gcode(line.upper())
        self._say("ok" if ok is None else f"error:{ok}")

    def _system(self, line: str) -> None:
        if line == "$$":
            self._say(*(f"${k}={v}" for k, v in sorted(self.settings.items())), "ok")
        elif line == "$I":
            self._say(f"[VER:1.1f.20170131:{self.name}]", "[OPT:VC,15,128]", "ok")
        elif line.startswith("$I="):
            self.name = line[3:]
            self._say("ok")
        elif line == "$G":
            self._say(f"[GC:G0 G54 G17 G21 {'G90' if self.absolute else 'G91'} G94 "
                      f"{'M3' if self.spindle_on else 'M5'} M9 T0 F{self.feed:.0f} S{self.spindle:.0f}]", "ok")
        elif line == "$#":
            self._say("[G54:0.000,0.000,0.000]", "[G92:0.000,0.000,0.000]", "ok")
        elif line == "$SLP":
            self._say("ok", "[MSG:Sleeping]")
            self.state = "Sleep"
        elif line == "$C":
            self.check_mode = not self.check_mode
            if self.check_mode:
                self.state = "Check"
                self._say("[MSG:Enabled]", "ok")
            else:
                self._say("[MSG:Disabled]", "ok")
                self._reset_state()
                self.out += BANNER
        elif line == "$X":
            self.state = "Idle"
            self._say("[MSG:Caution: Unlocked]", "ok")
        elif line == "$H":
            self._say("ok" if self.settings.get(22) == "1" else "error:5")
        elif line.startswith("$J="):
            err = self._gcode(line[3:].upper(), jog=True)
            self._say("ok" if err is None else f"error:{err}")
        else:
            m = re.match(r"^\$(\d+)=(\S+)$", line)
            if m:
                self.settings[int(m.group(1))] = m.group(2)
                self._say("ok")
            else:
                self._say("error:3")

    def _gcode(self, line: str, jog: bool = False) -> Optional[int]:
        words = _WORD.findall(line.replace(" ", ""))
        if not words:
            return 1
        absolute = self.absolute
        target = {}
        motion = False
        for letter, val in words:
            v = float(val)
            if letter == "G":
                g = int(v)
                if g in (0, 1):
                    motion = True
                elif g == 90:
                    absolute = True
                elif g == 91:
                    absolute = False
                elif g in (4, 17, 21, 94, 54):
                    pass
                else:
                    return 20
            elif letter == "M":
                m = int(v)
                if m == 3:
                    self.spindle_on = True
                elif m == 5:
                    self.spindle_on = False
                elif m in (2, 30):
                    pass
                else:
                    return 20
            elif letter == "S":
                self.spindle = v
            elif letter == "F":
                self.feed = v
            elif letter in "XYZ":
                target["XYZ".index(letter)] = v
            elif letter == "P":
                pass
            else:
                return 20
        if not jog:
            self.absolute = absolute
        if target and (motion or jog):
            if jog and self.feed <= 0:
                return 22
            for i, v in target.items():
                self.mpos[i] = v if absolute else self.mpos[i] + v
        return None


def factory(**kw):
    """A ``transport_factory`` for :class:`ndgcode.grbl.SerialBackend`."""
    holder = {}

    def make(port, baud):
        t = FakeGrblSerial(port, baud, **kw)
        holder["last"] = t
        return t

    make.holder = holder
    return make
