"""ndgcode: the NextDraw Python API on GRBL G-code pen plotters (VigoTec VG-X4).

    from ndgcode import NextDraw     # instead of: from nextdraw import NextDraw
"""

__version__ = "0.1.0.dev0"

from .api import NextDraw  # noqa: E402

__all__ = ["NextDraw", "__version__"]
