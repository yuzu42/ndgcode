"""Output backends: where generated G-code goes.

* :class:`NullBackend` keeps lines in memory (preview, estimates, tests).
* :class:`FileBackend` writes a ``.gcode`` file (export / dry run).
* :class:`ndgcode.grbl.SerialBackend` streams to a GRBL board over USB.

All share one small interface so the planner doesn't care which is used.
"""

from __future__ import annotations

import io
import os
from typing import Dict, List, Optional

# What the VG-X4 reported (2026-11-25); used by simulated backends.
SIM_SETTINGS: Dict[int, str] = {
    0: "10", 1: "255", 2: "0", 3: "3", 4: "0", 5: "0", 6: "0", 10: "1",
    11: "0.100", 12: "0.002", 13: "0", 20: "0", 21: "0", 22: "0", 23: "0",
    24: "25.000", 25: "500.000", 26: "250", 27: "1.000", 30: "1000", 31: "0",
    32: "0", 100: "160.000", 101: "160.000", 102: "250.000", 110: "5000.000",
    111: "5000.000", 112: "500.000", 120: "400.000", 121: "400.000",
    122: "10.000", 130: "1000.000", 131: "1000.000", 132: "200.000",
}


class Backend:
    """Interface. ``live`` is True only for real hardware."""

    live = False
    name = "backend"

    def __init__(self):
        self.fw_version: Optional[str] = None
        self.build_info: Optional[str] = None
        self.nickname: Optional[str] = None
        self.lines_sent = 0

    # lifecycle
    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @property
    def is_open(self) -> bool:
        raise NotImplementedError

    # traffic
    def send(self, line: str) -> None:
        """Queue one G-code line (flow controlled on live backends)."""
        raise NotImplementedError

    def realtime(self, data: bytes) -> None:
        """Send a GRBL real-time command byte (``?``, ``!``, ``~``, 0x18 ...)."""

    def query(self, cmd: str, timeout: float = 5.0) -> List[str]:
        """Send a command after the queue drained; return its response lines."""
        raise NotImplementedError

    def wait_idle(self, timeout: Optional[float] = None) -> None:
        """Block until all lines were executed and the machine is idle."""

    def status(self) -> Optional[dict]:
        """Latest parsed status report, or None if not available."""
        return None

    def settings(self) -> Dict[int, str]:
        """``$$`` as ``{number: value-string}``."""
        return {}

    def soft_reset(self) -> None:
        """Ctrl-X. Position is kept if the machine was not moving."""

    def check_error(self) -> None:
        """Raise a pending asynchronous error (streamed ``error:``, alarm)."""


class NullBackend(Backend):
    """In-memory backend; simulates a VG-X4 that answers every query."""

    name = "null"

    def __init__(self, sim_settings: Optional[Dict[int, str]] = None):
        super().__init__()
        self.lines: List[str] = []
        self._open = False
        self._settings = dict(SIM_SETTINGS if sim_settings is None else sim_settings)
        self.fw_version = "simulated"
        self.build_info = "VC,15,128"

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def send(self, line: str) -> None:
        if not self._open:
            raise RuntimeError("backend is not open")
        self.lines.append(line)
        self.lines_sent += 1

    def query(self, cmd: str, timeout: float = 5.0) -> List[str]:
        if cmd == "$$":
            return [f"${k}={v}" for k, v in sorted(self._settings.items())]
        if cmd == "$I":
            return [f"[VER:1.1f.simulated:{self.nickname or ''}]", f"[OPT:{self.build_info}]"]
        if cmd.startswith("$") and "=" in cmd[1:] and cmd[1:].split("=")[0].isdigit():
            k, v = cmd[1:].split("=", 1)
            self._settings[int(k)] = v
        if cmd.startswith("$I="):
            self.nickname = cmd[3:]
        return []

    def settings(self) -> Dict[int, str]:
        return dict(self._settings)


class FileBackend(NullBackend):
    """Writes the G-code to a file (or any text stream) instead of a machine."""

    name = "file"

    def __init__(self, target, header: Optional[List[str]] = None):
        super().__init__()
        self._target = target
        self._fh: Optional[io.TextIOBase] = None
        self._owns = False
        self.header = header or []
        self.path = target if isinstance(target, (str, os.PathLike)) else None

    def open(self) -> None:
        super().open()
        if isinstance(self._target, (str, os.PathLike)):
            self._fh = open(self._target, "w", encoding="ascii", newline="\n")
            self._owns = True
        else:
            self._fh = self._target
        for h in self.header:
            self._fh.write(f"; {h}\n")

    def send(self, line: str) -> None:
        super().send(line)
        self._fh.write(line + "\n")

    def close(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            if self._owns:
                self._fh.close()
        self._fh = None
        super().close()
