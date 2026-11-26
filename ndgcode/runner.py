"""Run an unchanged NextDraw script on the GRBL plotter (``ndgcode run``).

The script's ``from nextdraw import NextDraw`` is redirected to ndgcode. The
real ``nextdraw`` package (if installed) stays available to ndgcode itself for
SVG digestion in the plot context.
"""

from __future__ import annotations

import os
import runpy
import sys
import types
from typing import Dict, Optional

from . import api, plot


def run_script(path: str, port: Optional[str] = None, overrides: Optional[Dict] = None,
               argv: Optional[list] = None) -> None:
    plot.real_nextdraw()          # import the real library before shadowing it
    defaults = dict(overrides or {})

    class NextDraw(api.NextDraw):
        def __init__(self):
            super().__init__()
            if port is not None:
                self.options.port = port
            for k, v in defaults.items():
                setattr(self.options if hasattr(self.options, k) else self.params, k, v)

    shim = types.ModuleType("nextdraw")
    shim.NextDraw = NextDraw
    shim.__ndgcode_shim__ = True
    import ndgcode
    saved = sys.modules.get("nextdraw")
    saved_cls = ndgcode.NextDraw
    sys.modules["nextdraw"] = shim
    ndgcode.NextDraw = NextDraw          # scripts that import ndgcode directly
    old_argv, old_path = sys.argv, list(sys.path)
    sys.argv = [path] + list(argv or [])
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    try:
        runpy.run_path(path, run_name="__main__")
    finally:
        ndgcode.NextDraw = saved_cls
        sys.argv, sys.path[:] = old_argv, old_path
        if saved is not None:
            sys.modules["nextdraw"] = saved
        else:
            sys.modules.pop("nextdraw", None)
