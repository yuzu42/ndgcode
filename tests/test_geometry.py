import unittest

from ndgcode.geometry import Bounds, Frame, HeightMap, path_length, simplify, unit_scale


class TestUnits(unittest.TestCase):
    def test_scales(self):
        self.assertEqual(unit_scale(0), 25.4)
        self.assertEqual(unit_scale(1), 10.0)
        self.assertEqual(unit_scale(2), 1.0)
        with self.assertRaises(ValueError):
            unit_scale(3)


class TestClip(unittest.TestCase):
    def setUp(self):
        self.b = Bounds(100, 50)

    def test_inside(self):
        self.assertEqual(self.b.clip_segment((10, 10), (20, 20)), ((10, 10), (20, 20)))

    def test_outside(self):
        self.assertIsNone(self.b.clip_segment((-10, -10), (-5, -1)))

    def test_crossing(self):
        a, b = self.b.clip_segment((-10, 10), (110, 10))
        self.assertAlmostEqual(a[0], 0)
        self.assertAlmostEqual(b[0], 100)

    def test_clip_path_leaves_and_reenters(self):
        pts = [(10, 10), (10, 70), (20, 70), (20, 10)]  # goes above y=50 and back
        pieces = self.b.clip_path(pts)
        self.assertEqual(len(pieces), 2)
        self.assertEqual(pieces[0][0], (10, 10))
        self.assertAlmostEqual(pieces[0][-1][1], 50)
        self.assertAlmostEqual(pieces[1][0][1], 50)
        self.assertEqual(pieces[1][-1], (20, 10))

    def test_tolerance_snaps(self):
        b = Bounds(100, 50, tolerance=0.1)
        a, c = b.clip_segment((-0.05, 10), (10, 10))
        self.assertEqual(a, (0.0, 10.0))


class TestSimplify(unittest.TestCase):
    def test_collinear_removed(self):
        pts = [(0, 0), (1, 0), (2, 0), (3, 0)]
        self.assertEqual(simplify(pts, 0.01), [(0, 0), (3, 0)])

    def test_corner_kept(self):
        pts = [(0, 0), (5, 0), (5, 5)]
        self.assertEqual(simplify(pts, 0.01), [(0, 0), (5, 0), (5, 5)])

    def test_duplicates_removed_without_tolerance(self):
        self.assertEqual(simplify([(0, 0), (0, 0), (1, 1)], 0), [(0, 0), (1, 1)])

    def test_length(self):
        self.assertAlmostEqual(path_length([(0, 0), (3, 4)]), 5)


class TestFrame(unittest.TestCase):
    def test_flip_y(self):
        f = Frame(flip_y=True)
        self.assertEqual(f.vec_to_machine(3, 4), (3, -4))
        self.assertEqual(f.vec_from_machine(3, -4), (3, 4))

    def test_roundtrip_all(self):
        for fx in (False, True):
            for fy in (False, True):
                for sw in (False, True):
                    f = Frame(fx, fy, sw)
                    self.assertEqual(f.vec_from_machine(*f.vec_to_machine(1.5, -2.5)), (1.5, -2.5))


class TestHeightMap(unittest.TestCase):
    def test_bilinear(self):
        hm = HeightMap([0, 100], [0, 100], [[800, 700], [600, 500]])
        self.assertAlmostEqual(hm.s_up_at(0, 0), 800)
        self.assertAlmostEqual(hm.s_up_at(50, 50), 650)
        self.assertAlmostEqual(hm.s_up_at(200, -5), 700)  # clamped
        self.assertEqual(hm.min_s(), 500)


if __name__ == "__main__":
    unittest.main()
