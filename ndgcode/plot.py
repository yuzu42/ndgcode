"""Plot context (SVG). Implemented in milestone M4."""

from __future__ import annotations

import importlib
import sys

_REAL = None


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


class PlotJob:
    def __init__(self, nd, svg_input=None):
        self.nd = nd
        self.svg_input = svg_input

    def run(self, output=False):
        raise NotImplementedError("the plot context arrives in milestone M4")
