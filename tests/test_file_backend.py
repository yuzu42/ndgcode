import io
import math
import os
import tempfile
import unittest

from ndgcode.backends import FileBackend
from ndgcode.machine import Machine, Settings
from ndgcode.options import Options, Params


class TestFileBackend(unittest.TestCase):
    def _machine(self, target, **kw):
        o, p = Options(), Params()
        for k, v in kw.items():
            setattr(o if hasattr(o, k) else p, k, v)
        be = FileBackend(target, header=["test export"])
        be.open()
        return Machine(Settings(o, p), be), be

    def test_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.gcode")
            m, be = self._machine(path)
            m.start()
            m.draw_path([(10, 10), (20, 10)])
            m.finish()
            be.close()
            with open(path) as fh:
                text = fh.read().splitlines()
        self.assertEqual(text[0], "; test export")
        self.assertEqual(text[1], "G21 G90 G17 G94")
        self.assertEqual(text[-1], "M5")

    def test_sine_is_simplified(self):
        buf = io.StringIO()
        m, be = self._machine(buf, simplify_mm=0.01)
        m.start()
        pts = [(x / 10, 25 + 10 * math.sin(math.tau * x / 1000)) for x in range(1000)]
        m.draw_path(pts)
        m.finish()
        moves = [l for l in buf.getvalue().splitlines() if l.startswith("G1")]
        self.assertLess(len(moves), 999)      # RDP removed nearly collinear points
        self.assertGreater(len(moves), 50)    # but kept the curve
        self.assertAlmostEqual(m.dist_down, sum(
            math.dist(a, b) for a, b in zip(pts, pts[1:])), delta=0.05)


if __name__ == "__main__":
    unittest.main()
