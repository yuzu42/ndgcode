import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr

from ndgcode import NextDraw
from ndgcode.backends import NullBackend


def nd_null(**opts):
    nd = NextDraw()
    nd.interactive()
    be = NullBackend()
    nd.options.port = be
    for k, v in opts.items():
        setattr(nd.options if hasattr(nd.options, k) else nd.params, k, v)
    return nd, be


class TestInteractive(unittest.TestCase):
    def test_requires_connect(self):
        nd = NextDraw()
        nd.interactive()
        with self.assertRaises(RuntimeError):
            nd.moveto(1, 1)

    def test_doc_square_inches(self):
        # NextDraw docs, goto(): a square with goto + penup/pendown, inches
        nd, be = nd_null()
        self.assertTrue(nd.connect())
        nd.goto(1, 1)
        nd.pendown()
        nd.goto(2, 1)
        nd.goto(2, 2)
        nd.goto(1, 2)
        nd.goto(1, 1)
        nd.penup()
        self.assertEqual(nd.current_pos(), (1.0, 1.0))
        nd.disconnect()
        self.assertFalse(nd.connected)
        g1 = [l for l in be.lines if l.startswith("G1")]
        self.assertIn("G1 X55.8 Y-55.8", g1)       # (2 in, 2 in) + 5 mm pull-off
        self.assertAlmostEqual(nd.distance_pendown, 4 * 0.0254, places=6)   # meters
        self.assertEqual(nd.pen_lifts, 1)

    def test_units_and_relative(self):
        nd, be = nd_null(units=2)
        nd.connect()
        nd.moveto(10, 10)
        nd.line(5, 0)
        nd.move(0, 5)
        nd.go(-5, 0)
        self.assertEqual(nd.turtle_pos(), (10.0, 15.0))
        self.assertTrue(nd.turtle_pen())
        nd.disconnect()

    def test_draw_path_lists(self):
        nd, be = nd_null(units=2)
        nd.connect()
        nd.draw_path([[20, 20], [60, 20], [60, 60], [20, 60], [20, 20]])
        self.assertEqual(nd.current_pos(), (20.0, 20.0))
        self.assertTrue(nd.current_pen())
        with self.assertRaises(ValueError):
            nd.draw_path([[1, 1]])
        nd.disconnect()

    def test_out_of_bounds_turtle_vs_machine(self):
        # NextDraw docs, current_pos(): (2, -1) is clipped to (2, 0)
        nd, be = nd_null()
        nd.connect()
        nd.moveto(2, 1)
        nd.moveto(2, -1)
        self.assertEqual(nd.turtle_pos(), (2.0, -1.0))
        self.assertEqual(nd.current_pos(), (2.0, 0.0))
        nd.disconnect()

    def test_update_changes_speed(self):
        nd, be = nd_null(units=2, speed_pendown=10, feed_cap_mm_min=5000)
        nd.connect()
        nd.lineto(10, 0)
        nd.options.speed_pendown = 20
        nd.update()
        nd.lineto(20, 0)
        nd.disconnect()
        self.assertTrue(any(l.endswith("F500") for l in be.lines))
        self.assertTrue(any(l.endswith("F1000") for l in be.lines))

    def test_delay_and_block(self):
        nd, be = nd_null()
        nd.connect()
        nd.delay(2500)
        nd.block()
        self.assertIn("G4 P2.5", be.lines)
        self.assertGreaterEqual(nd.time_estimate, 2.5)
        nd.disconnect()

    def test_warns_for_not_applicable(self):
        nd, be = nd_null(model=10)
        with redirect_stderr(io.StringIO()):
            nd.connect()
        self.assertTrue(any("model" in w for w in nd.warnings))
        nd.disconnect()

    def test_connect_failure_returns_false(self):
        nd = NextDraw()
        nd.interactive()
        nd.options.port = "/dev/does-not-exist"
        with redirect_stderr(io.StringIO()):
            self.assertFalse(nd.connect())
        self.assertEqual(nd.errors.code, 101)

    def test_connect_failure_raises_when_asked(self):
        nd = NextDraw()
        nd.interactive()
        nd.options.port = "/dev/does-not-exist"
        nd.errors.connect = True
        with redirect_stderr(io.StringIO()), self.assertRaises(RuntimeError):
            nd.connect()

    def test_file_export(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "square.gcode")
            nd = NextDraw()
            nd.interactive()
            nd.options.units = 2
            nd.options.port = "file:" + path
            nd.connect()
            nd.draw_path([[0, 0], [10, 0], [10, 10]])
            nd.disconnect()
            with open(path) as fh:
                text = fh.read()
        self.assertIn("G1 X15 Y-5", text)
        self.assertTrue(text.rstrip().endswith("M5"))

    def test_load_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "conf.py")
            with open(path, "w") as fh:
                fh.write("speed_pendown = 33\ntravel_x_mm = 200.0\npen_pos_up = 30\n")
            nd = NextDraw()
            nd.load_config(path)
        self.assertEqual(nd.options.speed_pendown, 33)
        self.assertEqual(nd.params.travel_x_mm, 200.0)
        self.assertEqual(nd.options.pen_pos_up, 30)


class TestWithFakeBoard(unittest.TestCase):
    def test_session_on_fake_grbl(self):
        nd = NextDraw()
        nd.interactive()
        nd.options.units = 2
        nd.options.port = "fake:"
        with redirect_stderr(io.StringIO()):
            self.assertTrue(nd.connect())
        self.assertEqual(nd.fw_version_string, "1.1f.20170131")
        nd.draw_path([[20, 20], [60, 20], [60, 60]])
        nd.block()
        st = nd.status()
        self.assertEqual(st["state"], "Idle")
        self.assertEqual(st["MPos"][:2], (65.0, -65.0))
        self.assertIn("$110=5000.000", nd.usb_query("$$"))
        fake = nd.backend._t
        nd.disconnect()
        self.assertEqual(fake.lines[-1], "$SLP")
        self.assertIn("M5", fake.lines)
        self.assertEqual(fake.mpos[:2], [0.0, 0.0])    # parked in the corner

    def test_accel_written_only_when_changed(self):
        nd = NextDraw()
        nd.interactive()
        nd.options.port = "fake:"
        nd.options.accel = 100          # = 400 mm/s^2, already on the board
        with redirect_stderr(io.StringIO()):
            nd.connect()
        fake = nd.backend._t
        self.assertFalse(any(l.startswith("$120=") for l in fake.lines))
        nd.options.accel = 50
        nd.update()
        self.assertIn("$120=200.000", fake.lines)
        nd.disconnect()


if __name__ == "__main__":
    unittest.main()
