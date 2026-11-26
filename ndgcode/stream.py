"""Stream or check an existing G-code file on a GRBL board."""

from __future__ import annotations

import re
import time
from typing import Callable, Iterable, Iterator, List, Optional, Tuple

from . import errors
from .grbl import RT_HOLD, SerialBackend

_PAREN = re.compile(r"\([^)]*\)")


def clean_lines(lines: Iterable[str]) -> Iterator[Tuple[int, str]]:
    """Strip comments and blank lines; yield (line number, text)."""
    for n, raw in enumerate(lines, 1):
        line = _PAREN.sub("", raw.split(";", 1)[0]).strip()
        if line and line != "%":
            yield n, line


def stream(be: SerialBackend, lines: Iterable[str],
           progress: Optional[Callable[[int, int], None]] = None) -> float:
    """Send all lines with flow control and wait until done. Returns seconds.

    On Ctrl-C: feed hold, soft reset (discards the queue, keeps position),
    servo to rest (``M5`` = pen fully up), then re-raise.
    """
    todo = list(clean_lines(lines))
    t0 = time.monotonic()
    try:
        for i, (_, line) in enumerate(todo, 1):
            be.send(line)
            if progress and (i % 50 == 0 or i == len(todo)):
                progress(i, len(todo))
        be.wait_idle()
    except KeyboardInterrupt:
        emergency_stop(be)
        raise
    return time.monotonic() - t0


def emergency_stop(be: SerialBackend) -> None:
    try:
        be.realtime(RT_HOLD)
        time.sleep(0.5)
        be.soft_reset()
        be.send("M5")
        be.wait_idle(timeout=5)
    except Exception:
        pass


def check(be: SerialBackend, lines: Iterable[str]) -> List[Tuple[int, str, str]]:
    """Run the file in GRBL check mode (``$C``): parsed, nothing moves.

    Returns ``[(line number, text, error message), ...]``; empty means OK.
    """
    problems: List[Tuple[int, str, str]] = []
    be.wait_idle(timeout=10)
    be.query("$C")
    try:
        for n, line in clean_lines(lines):
            try:
                if line.startswith("$") and not line.startswith("$J="):
                    continue   # settings / system commands are not checked
                be.send(line)
                be.drain(timeout=10)
                be.check_error()
            except errors.GrblError as e:
                problems.append((n, line, str(e)))
    finally:
        # leaving check mode makes GRBL reset itself; announce it as expected
        be._expect_reset = True
        try:
            seq = be._banner_seq
            be.query("$C")
            be._wait_banner(seq, 3.0)
        finally:
            be._expect_reset = False
    return problems
