"""Error codes and exceptions, mirroring NextDraw's ``errors`` object."""

from __future__ import annotations

# NextDraw errors.code values
OK = 0
CONNECT = 101
BUTTON = 102      # here: GRBL entered Hold (external feed hold or pause())
KEYBOARD = 103
DISCONNECT = 104
POWER = 105       # here: unexpected board reset
HOMING = 106

MESSAGES = {
    OK: "No error; operation nominal",
    CONNECT: "Failed to connect",
    BUTTON: "Stopped by pause (feed hold)",
    KEYBOARD: "Stopped by keyboard interrupt",
    DISCONNECT: "Lost USB connectivity",
    POWER: "Board reset unexpectedly (power loss?)",
    HOMING: "Homing failed",
}

# GRBL 1.1 error and alarm texts (subset that matters for plotting)
GRBL_ERRORS = {
    1: "G-code words consist of a letter and a value. Letter was not found.",
    2: "Numeric value format is not valid or missing an expected value.",
    3: "Grbl '$' system command was not recognized or supported.",
    4: "Negative value received for an expected positive value.",
    5: "Homing cycle is not enabled via settings.",
    8: "Grbl '$' command cannot be used unless Grbl is IDLE.",
    9: "G-code locked out during alarm or jog state.",
    10: "Soft limits cannot be enabled without homing also enabled.",
    11: "Max characters per line exceeded. Line was not processed and executed.",
    15: "Jog target exceeds machine travel. Command ignored.",
    16: "Jog command with no '=' or contains prohibited g-code.",
    20: "Unsupported or invalid g-code command found in block.",
    22: "Feed rate has not yet been set or is undefined.",
    24: "Two G-code commands that both require the use of the XYZ axis words were detected in the block.",
    33: "The motion command has an invalid target.",
}

GRBL_ALARMS = {
    1: "Hard limit triggered. Machine position is likely lost.",
    2: "G-code motion target exceeds machine travel (soft limit).",
    3: "Reset while in motion. Position is likely lost.",
    4: "Probe fail.",
    5: "Probe fail.",
    6: "Homing fail. Reset during active homing cycle.",
    7: "Homing fail. Safety door was opened during active homing cycle.",
    8: "Homing fail. Cycle failed to clear limit switch when pulling off.",
    9: "Homing fail. Could not find limit switch within search distance.",
}


class NdGcodeError(RuntimeError):
    """Base class. Subclasses carry a NextDraw-style error code."""

    code = OK


class ConnectError(NdGcodeError):
    code = CONNECT


class DisconnectError(NdGcodeError):
    code = DISCONNECT


class ResetError(NdGcodeError):
    code = POWER


class HomingError(NdGcodeError):
    code = HOMING


class PausedError(NdGcodeError):
    code = BUTTON


class KeyboardPause(NdGcodeError):
    code = KEYBOARD


class GrblError(NdGcodeError):
    """GRBL answered ``error:N`` for a line."""

    def __init__(self, num: int, line: str = ""):
        self.num = num
        self.line = line
        text = GRBL_ERRORS.get(num, "unknown error")
        super().__init__(f"GRBL error:{num} ({text}) for line {line!r}")


class GrblAlarm(NdGcodeError):
    """GRBL reported ``ALARM:N``."""

    def __init__(self, num: int):
        self.num = num
        self.code = HOMING if num in (6, 7, 8, 9) else DISCONNECT
        super().__init__(f"GRBL ALARM:{num} ({GRBL_ALARMS.get(num, 'unknown alarm')})")


class Errors:
    """NextDraw-compatible ``nd.errors``: raise flags plus the last code."""

    def __init__(self):
        self.connect = False
        self.button = False
        self.keyboard = False
        self.disconnect = False
        self.power = False
        self.homing = False
        self.code = OK

    def should_raise(self, code: int) -> bool:
        return {
            CONNECT: self.connect,
            BUTTON: self.button,
            KEYBOARD: self.keyboard,
            DISCONNECT: self.disconnect,
            POWER: self.power,
            HOMING: self.homing,
        }.get(code, True)
