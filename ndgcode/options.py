"""``nd.options`` / ``nd.params`` namespaces and config-file loading."""

from __future__ import annotations

import copy
import runpy
from typing import Any, Dict

from . import conf


class _Namespace:
    """Attribute bag with known defaults. Unknown names are allowed (NextDraw
    scripts may set parameters we don't use), but reported once by the API."""

    _defaults: Dict[str, Any] = {}

    def __init__(self, **overrides):
        for k, v in self._defaults.items():
            object.__setattr__(self, k, copy.deepcopy(v))
        for k, v in overrides.items():
            setattr(self, k, v)

    def as_dict(self) -> Dict[str, Any]:
        return dict(vars(self))

    def unknown(self):
        return [k for k in vars(self) if k not in self._defaults]

    def __repr__(self) -> str:
        items = ", ".join(f"{k}={v!r}" for k, v in sorted(vars(self).items()))
        return f"{type(self).__name__}({items})"


class Options(_Namespace):
    _defaults = conf.OPTIONS


class Params(_Namespace):
    _defaults = conf.PARAMS


def load_config_file(path, options: Options, params: Params) -> Dict[str, Any]:
    """Run a ``nextdraw_conf.py``-style file and apply its assignments.

    Names that are options go to ``options``, everything else to ``params``.
    Returns the applied values. The file is executed as Python, so only load
    configuration files you trust (normally your own).
    """
    values = runpy.run_path(str(path))
    applied = {}
    for k, v in values.items():
        if k.startswith("_") or callable(v) or type(v).__name__ == "module":
            continue
        if k in conf.OPTIONS:
            setattr(options, k, v)
        elif k == "overrides" and isinstance(v, dict):
            params.overrides.update({kk: vv for kk, vv in v.items() if vv is not None})
        else:
            setattr(params, k, v)
        applied[k] = v
    return applied
