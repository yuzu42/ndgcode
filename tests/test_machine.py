import unittest

from ndgcode.backends import NullBackend
from ndgcode.machine import Machine, Settings
from ndgcode.options import Options, Params


def make(warn=None, **kw):
    o, p = Options(), Params()
    for k, v in kw.items():
        setattr(o if hasattr(o, k) else p, k, v)
    warnings = [] if warn is None else warn
    st = Settings(o, p, warnings.append)
    be = NullBackend()
    be.open()
    return Machine(st, be, warnings.append), be


class TestSettings(unittest.TestCase):
    def test_pen_mapping(self):
        m, _ = make(pen_pos_down=0, pen_pos_up=30)
        self.assertEqual(m.st.s_down, 1000)
        self.assertEqual(m.st.s_up, 700)

    def test_feed_cap(self):
        m, _ = make(speed_pendown=100, feed_cap_mm_min=1200)
        self.assertEqual(m.st.feed_down, 1200)
        m, _ = make(speed_pendown=10, feed_cap_mm_min=5000)
        self.assertEqual(m.st.feed_down, 500)

    def test_warns_when_pen_down_does_not_touch(self):
        w = []
        make(warn=w, pen_pos_down=40, pen_pos_up=60)   # NextDraw defaults: S600 is too high
        self.assertTrue(any("may not draw" in x for x in w))


class TestGolden(unittest.TestCase):
    def test_square(self):
        m, be = make(pen_rate_lower=100, pen_rate_raise=100)
        m.start(home=True)
        m.draw_path([(10, 10), (20, 10), (20, 20), (10, 20), (10, 10)])
        m.finish(park=True)
        self.assertEqual(be.lines, [
            "G21 G90 G17 G94",
            "M3 S550", "G4 P0.15",
            "G1 X5 Y-5 F500",          # pull-off
            "G1 X15 Y-15 F1000",       # travel to (10, 10)
            "M3 S1000", "G4 P0.15",
            "G1 X25 Y-15",
            "G1 X25 Y-25",
            "G1 X15 Y-25",
            "G1 X15 Y-15",
            "M3 S550", "G4 P0.15",
            "G1 X0 Y0",                # park in the Home corner
            "M5",
        ])
        self.assertAlmostEqual(m.dist_down, 40)
        self.assertEqual(m.pen_lifts, 1)
        self.assertGreater(m.time_estimate_s, 0)

    def test_no_homing(self):
        m, be = make(pen_rate_lower=100)
        m.start(home=False)
        m.draw_path([(1, 1), (2, 1)])
        self.assertIn("G1 X1 Y-1 F1000", be.lines)


class TestPen(unittest.TestCase):
    def test_slow_lower_emulated_in_steps(self):
        m, be = make(pen_rate_lower=25)
        m.start(home=False)
        n0 = len(be.lines)
        m.lower_pen()
        servo = [l for l in be.lines[n0:] if l.startswith("M3")]
        self.assertGreater(len(servo), 3)
        self.assertEqual(servo[-1], "M3 S1000")
        vals = [int(l.split("S")[1]) for l in servo]
        self.assertEqual(vals, sorted(vals))

    def test_pen_delays_added(self):
        m, be = make(pen_rate_lower=100, pen_delay_down=50)
        m.start(home=False)
        m.lower_pen()
        self.assertEqual(be.lines[-1], "G4 P0.2")

    def test_no_repeat(self):
        m, be = make()
        m.start(home=False)
        n = len(be.lines)
        m.raise_pen()
        self.assertEqual(len(be.lines), n)


class TestClipping(unittest.TestCase):
    def test_goto_outside_lifts_and_reenters(self):
        m, be = make(pen_rate_lower=100, pen_rate_raise=100, travel_x_mm=100, travel_y_mm=100)
        m.start(home=False)
        m.turtle_pen_up = True
        m.goto((50, 50))
        m.pendown()
        m.goto((50, 150))        # leaves through y=100
        self.assertTrue(m.pen_up)
        self.assertEqual(m.turtle, (50, 150))
        self.assertAlmostEqual(m.pos[1], 100)
        m.goto((60, 50))         # re-enters
        self.assertFalse(m.pen_up)
        self.assertEqual(m.pos, (60, 50))

    def test_draw_path_splits(self):
        m, be = make(pen_rate_lower=100, pen_rate_raise=100, travel_x_mm=100, travel_y_mm=100)
        m.start(home=False)
        m.draw_path([(10, 10), (10, 150), (20, 150), (20, 10)])
        downs = [l for l in be.lines if l == "M3 S1000"]
        self.assertEqual(len(downs), 2)
        self.assertEqual(m.turtle, (20, 10))

    def test_never_pen_down_outside(self):
        m, be = make(travel_x_mm=100, travel_y_mm=100)
        m.start(home=False)
        m.goto((500, 500))
        m.pendown()
        self.assertTrue(m.pen_up)


class TestHeightMapLift(unittest.TestCase):
    def test_travel_lifts_higher_where_needed(self):
        hm = {"xs": [0, 100], "ys": [0, 100], "s_up": [[750, 750], [550, 550]]}
        m, be = make(pen_height_map=hm, pen_rate_raise=100, pen_rate_lower=100,
                     travel_x_mm=100, travel_y_mm=100)
        m.start(home=False)
        self.assertEqual(be.lines[1], "M3 S750")   # at (0, 0)
        m.travel((0, 100))
        self.assertIn("M3 S550", be.lines)


if __name__ == "__main__":
    unittest.main()
