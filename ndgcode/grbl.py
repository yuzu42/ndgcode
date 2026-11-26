"""GRBL 1.1 serial backend: streaming, responses, status, real-time commands.

Streaming uses GRBL's character-counting protocol: the number of bytes sent
but not yet acknowledged (``ok`` / ``error:N``) never exceeds the 128-byte RX
buffer. ``stream_mode="simple"`` sends one line per ``ok`` instead.

The transport is any object with ``write(bytes)``, ``read(n) -> bytes``
(returning ``b""`` on timeout) and ``close()``; by default a pyserial port.
Tests and demos use :class:`ndgcode.fakegrbl.FakeGrblSerial`.
"""

from __future__ import annotations

import collections
import re
import threading
import time
from typing import Callable, Deque, Dict, List, Optional

from . import errors
from .backends import Backend

BANNER_RE = re.compile(r"^(Grbl|VigoWriter)\s+(\S+)\s+\['\$' for help\]")

# real-time command bytes (GRBL 1.1)
RT_STATUS = b"?"
RT_HOLD = b"!"
RT_RESUME = b"~"
RT_RESET = b"\x18"
RT_JOG_CANCEL = b"\x85"
RT_FEED_100 = b"\x90"
RT_FEED_PLUS_10 = b"\x91"
RT_FEED_MINUS_10 = b"\x92"
RT_FEED_PLUS_1 = b"\x93"
RT_FEED_MINUS_1 = b"\x94"


def parse_status(line: str) -> dict:
    """Parse ``<Idle|MPos:0.000,0.000,0.000|FS:0,0|...>`` into a dict."""
    body = line.strip()[1:-1]
    fields = body.split("|")
    st: dict = {"state": fields[0].split(":")[0], "substate": fields[0], "raw": line.strip()}
    for f in fields[1:]:
        if ":" not in f:
            continue
        k, v = f.split(":", 1)
        if k in ("MPos", "WPos", "WCO"):
            st[k] = tuple(float(x) for x in v.split(","))
        elif k in ("FS", "F"):
            st["FS"] = tuple(float(x) for x in v.split(","))
        elif k in ("Ov", "Bf"):
            st[k] = tuple(int(float(x)) for x in v.split(","))
        else:
            st[k] = v
    if "MPos" in st and "WCO" in st and "WPos" not in st:
        st["WPos"] = tuple(m - w for m, w in zip(st["MPos"], st["WCO"]))
    return st


class _Pending:
    __slots__ = ("nbytes", "text", "is_query", "lines", "done", "error")

    def __init__(self, nbytes: int, text: str, is_query: bool):
        self.nbytes = nbytes
        self.text = text
        self.is_query = is_query
        self.lines: List[str] = []
        self.done = False
        self.error: Optional[int] = None


def _default_transport(port: str, baud: int):
    import serial  # pyserial
    return serial.Serial(port, baud, timeout=0.05)


class SerialBackend(Backend):
    live = True
    name = "serial"

    def __init__(self, port: str, baud: int = 115200,
                 transport_factory: Optional[Callable] = None,
                 rx_buffer: int = 127, mode: str = "count",
                 banner_timeout: float = 5.0, log: Optional[Callable[[str, str], None]] = None):
        super().__init__()
        self.port = port
        self.baud = baud
        self._factory = transport_factory or _default_transport
        self.rx_buffer = int(rx_buffer)
        if mode not in ("count", "simple"):
            raise ValueError("stream_mode must be 'count' or 'simple'")
        self.mode = mode
        self.banner_timeout = float(banner_timeout)
        self.log = log
        self._t = None
        self._cond = threading.Condition()
        self._pending: Deque[_Pending] = collections.deque()
        self._in_flight = 0
        self._query: Optional[_Pending] = None
        self._status: Optional[dict] = None
        self._status_seq = 0
        self._banner_seq = 0
        self._expect_reset = False
        self._async_error: Optional[Exception] = None
        self._fatal: Optional[Exception] = None
        self._stop = False
        self._reader: Optional[threading.Thread] = None
        self.messages: Deque[str] = collections.deque(maxlen=200)
        self.banner: Optional[str] = None

    # ------------------------------------------------------------ lifecycle
    @property
    def is_open(self) -> bool:
        return self._t is not None and self._fatal is None

    def open(self) -> None:
        try:
            self._t = self._factory(self.port, self.baud)
        except Exception as e:  # pyserial raises SerialException / OSError
            raise errors.ConnectError(f"cannot open {self.port}: {e}") from e
        self._stop = False
        self._reader = threading.Thread(target=self._read_loop, name="ndgcode-grbl-reader", daemon=True)
        self._reader.start()
        # Opening the port resets the board (DTR); wait for its banner. If the
        # board did not reset (other OS / adapters), request a soft reset.
        if not self._wait_banner(0, self.banner_timeout):
            self._expect_reset = True
            self.realtime(RT_RESET)
            if not self._wait_banner(0, self.banner_timeout):
                self.close()
                raise errors.ConnectError(f"no GRBL banner from {self.port}")
        self._expect_reset = False
        try:
            self._read_build_info()
        except errors.NdGcodeError:
            pass

    def _read_build_info(self) -> None:
        for line in self.query("$I"):
            m = re.match(r"^\[VER:([^:]*):?(.*)\]$", line)
            if m:
                self.fw_version = m.group(1)
                self.nickname = m.group(2) or None
            m = re.match(r"^\[OPT:(.*)\]$", line)
            if m:
                self.build_info = m.group(1)

    def close(self) -> None:
        self._stop = True
        t, self._t = self._t, None
        if t is not None:
            try:
                t.close()
            except Exception:
                pass
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=1.0)
        with self._cond:
            self._pending.clear()
            self._in_flight = 0
            self._cond.notify_all()

    # ------------------------------------------------------------ reader
    def _read_loop(self) -> None:
        buf = b""
        while not self._stop:
            t = self._t
            if t is None:
                break
            try:
                data = t.read(256)
            except Exception as e:
                if not self._stop:
                    with self._cond:
                        self._fatal = errors.DisconnectError(f"lost connection to {self.port}: {e}")
                        self._cond.notify_all()
                break
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("ascii", "replace").strip()
                if line:
                    self._handle(line)

    def _handle(self, line: str) -> None:
        if self.log:
            self.log("<", line)
        with self._cond:
            if line == "ok" or line.startswith("error:"):
                if not self._pending:
                    self.messages.append(f"unexpected response: {line}")
                    return
                p = self._pending.popleft()
                self._in_flight -= p.nbytes
                p.done = True
                if line.startswith("error:"):
                    p.error = int(line.split(":", 1)[1] or 0)
                    if not p.is_query and self._async_error is None:
                        self._async_error = errors.GrblError(p.error, p.text)
                if p is self._query:
                    self._query = None
                self._cond.notify_all()
            elif line.startswith("<") and line.endswith(">"):
                self._status = parse_status(line)
                self._status_seq += 1
                self._cond.notify_all()
            elif line.startswith("ALARM:"):
                try:
                    num = int(line.split(":", 1)[1])
                except ValueError:
                    num = 0
                if self._async_error is None:
                    self._async_error = errors.GrblAlarm(num)
                self._cond.notify_all()
            elif BANNER_RE.match(line):
                self.banner = line
                self._banner_seq += 1
                for p in self._pending:
                    p.done = True
                    p.error = -1
                self._pending.clear()
                self._in_flight = 0
                self._query = None
                if not self._expect_reset and self._banner_seq > 1 and self._async_error is None:
                    self._async_error = errors.ResetError("the board reset unexpectedly; position is lost")
                self._cond.notify_all()
            else:
                if self._query is not None:
                    self._query.lines.append(line)
                else:
                    self.messages.append(line)

    # ------------------------------------------------------------ waiting
    def _raise_fatal(self) -> None:
        if self._fatal is not None:
            raise self._fatal

    def check_error(self) -> None:
        with self._cond:
            self._raise_fatal()
            err, self._async_error = self._async_error, None
        if err is not None:
            raise err

    def _wait(self, pred: Callable[[], bool], timeout: Optional[float], what: str) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while not pred():
                self._raise_fatal()
                if self._async_error is not None:
                    err, self._async_error = self._async_error, None
                    raise err
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"timed out waiting for {what}")
                self._cond.wait(0.1 if remaining is None else min(0.1, remaining))

    def _wait_banner(self, seq: int, timeout: float) -> bool:
        try:
            self._wait(lambda: self._banner_seq > seq, timeout, "GRBL banner")
            return True
        except TimeoutError:
            return False

    # ------------------------------------------------------------ traffic
    def _write(self, data: bytes) -> None:
        t = self._t
        if t is None:
            raise errors.DisconnectError("not connected")
        if self.log:
            self.log(">", data.decode("ascii", "replace").rstrip())
        try:
            t.write(data)
        except Exception as e:
            with self._cond:
                self._fatal = errors.DisconnectError(f"lost connection to {self.port}: {e}")
            raise self._fatal from e

    def _enqueue(self, line: str, is_query: bool) -> _Pending:
        data = (line.strip() + "\n").encode("ascii")
        n = len(data)
        if n > self.rx_buffer:
            raise ValueError(f"line longer than the GRBL buffer ({n} bytes): {line!r}")
        if self.mode == "simple" or is_query:
            pred = lambda: not self._pending
        else:
            pred = lambda: self._in_flight + n <= self.rx_buffer
        self._wait(pred, None, "GRBL buffer space")
        p = _Pending(n, line.strip(), is_query)
        with self._cond:
            self._pending.append(p)
            self._in_flight += n
            if is_query:
                self._query = p
            self._write(data)
        self.lines_sent += 1
        return p

    def send(self, line: str) -> None:
        self.check_error()
        self._enqueue(line, is_query=False)

    def realtime(self, data: bytes) -> None:
        self._write(data)

    def query(self, cmd: str, timeout: float = 5.0) -> List[str]:
        self.check_error()
        p = self._enqueue(cmd, is_query=True)
        self._wait(lambda: p.done, timeout, f"response to {cmd!r}")
        if p.error is not None:
            if p.error == -1:
                raise errors.ResetError(f"board reset while waiting for {cmd!r}")
            raise errors.GrblError(p.error, cmd)
        return p.lines

    def drain(self, timeout: Optional[float] = None) -> None:
        """Wait until every sent line was acknowledged."""
        self._wait(lambda: not self._pending, timeout, "acknowledgements")

    def status(self, timeout: float = 1.0) -> Optional[dict]:
        seq = self._status_seq
        self.realtime(RT_STATUS)
        try:
            self._wait(lambda: self._status_seq > seq, timeout, "status report")
        except TimeoutError:
            return None
        return self._status

    @property
    def last_status(self) -> Optional[dict]:
        return self._status

    def wait_idle(self, timeout: Optional[float] = None, poll_s: float = 0.2) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        self.drain(timeout)
        while True:
            st = self.status()
            if st is not None:
                state = st["state"]
                if state in ("Idle", "Check", "Sleep"):
                    return
                if state == "Alarm":
                    raise errors.GrblAlarm(0)
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("timed out waiting for Idle")
            time.sleep(poll_s)

    def settings(self) -> Dict[int, str]:
        out: Dict[int, str] = {}
        for line in self.query("$$"):
            m = re.match(r"^\$(\d+)=(\S+)", line)
            if m:
                out[int(m.group(1))] = m.group(2)
        return out

    def soft_reset(self, timeout: float = 5.0) -> None:
        seq = self._banner_seq
        self._expect_reset = True
        try:
            self.realtime(RT_RESET)
            if not self._wait_banner(seq, timeout):
                raise errors.DisconnectError("no banner after soft reset")
        finally:
            self._expect_reset = False


# ---------------------------------------------------------------- discovery
def candidate_ports(match=("usbserial", "wchusbserial", "CH340", "CH341"), vid: int = 0x1A86) -> List[str]:
    """Serial ports that look like the plotter's CH340 USB chip.

    Only these are ever opened by auto-detection, so other USB devices (for
    example a NextDraw) are never sent anything.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in list_ports.comports():
        dev = p.device or ""
        if dev.startswith("/dev/tty.") and dev.replace("/dev/tty.", "/dev/cu.") != dev:
            continue  # on macOS prefer the cu.* twin
        text = " ".join(str(x) for x in (dev, p.description, p.manufacturer, p.hwid) if x)
        if (p.vid == vid) or any(m.lower() in text.lower() for m in match):
            out.append(dev)
    return out


def probe(port: str, baud: int = 115200, timeout: float = 4.0) -> Optional[dict]:
    """Open a port, return its GRBL identity or None. Resets the board."""
    be = SerialBackend(port, baud, banner_timeout=timeout)
    try:
        be.open()
    except errors.NdGcodeError:
        return None
    try:
        return {"port": port, "banner": be.banner, "fw_version": be.fw_version,
                "nickname": be.nickname, "build_info": be.build_info}
    finally:
        be.close()
