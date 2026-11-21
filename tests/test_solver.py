# SPDX-License-Identifier: GPL-3.0-or-later

"""Solver tests. Run with: python -m unittest discover tests"""

import importlib.util
import math
import pathlib
import unittest

# Load solver.py directly: the package __init__ imports bpy.
_path = pathlib.Path(__file__).resolve().parents[1] / "wible" / "solver.py"
_spec = importlib.util.spec_from_file_location("wible_solver", _path)
solver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(solver)


def dist(a, b):
    return math.dist(a, b)


class CatenaryTests(unittest.TestCase):
    def test_endpoints_are_exact(self):
        p0, p1 = (0.0, 0.0, 0.0), (3.0, 1.0, 0.5)
        pts, _ = solver.catenary_points(p0, p1, 1.5, 20)
        self.assertEqual(pts[0], p0)
        self.assertEqual(pts[-1], p1)
        self.assertEqual(len(pts), 20)

    def test_length_roundtrip(self):
        p0, p1 = (0.0, 0.0, 1.0), (4.0, 0.0, 2.0)
        for length in (4.3, 5.0, 8.0):
            a = solver.catenary_a_for_length(p0, p1, length)
            _, got = solver.catenary_points(p0, p1, a, 50)
            self.assertAlmostEqual(got, length, places=4)

    def test_even_spacing(self):
        pts, length = solver.catenary_points((0, 0, 0), (4, 0, 0), 1.0, 33)
        seg = length / 32
        for a, b in zip(pts, pts[1:]):
            self.assertAlmostEqual(dist(a, b), seg, delta=seg * 0.02)

    def test_sags_below_ends(self):
        pts, _ = solver.catenary_points((0, 0, 0), (4, 0, 0), 1.0, 21)
        self.assertLess(pts[10][2], -0.5)
        self.assertTrue(all(p[2] <= 1e-9 for p in pts))

    def test_lower_a_sags_more(self):
        low = min(p[2] for p in solver.catenary_points((0, 0, 0), (4, 0, 0), 0.8, 41)[0])
        high = min(p[2] for p in solver.catenary_points((0, 0, 0), (4, 0, 0), 4.0, 41)[0])
        self.assertLess(low, high)

    def test_taut_and_vertical_fall_back_to_line(self):
        self.assertEqual(solver.catenary_a_for_length((0, 0, 0), (2, 0, 0), 1.5), math.inf)
        self.assertIsNone(solver.catenary_a_for_length((0, 0, 0), (0, 0, 2), 3.0))
        pts, length = solver.catenary_points((0, 0, 0), (0, 0, 2), None, 5)
        self.assertAlmostEqual(length, 2.0)
        self.assertTrue(all(abs(p[0]) < 1e-12 and abs(p[1]) < 1e-12 for p in pts))

    def test_tiny_a_does_not_overflow(self):
        pts, length = solver.catenary_points((0, 0, 0), (10, 0, 0), 1e-6, 30)
        self.assertTrue(all(math.isfinite(c) for p in pts for c in p))
        self.assertTrue(math.isfinite(length))


class DrapeTests(unittest.TestCase):
    def test_free_drape_stays_close_to_catenary(self):
        pts, length = solver.catenary_points((0, 0, 0), (4, 0, 0), 1.5, 25)
        draped = solver.drape(pts, length / 24, 60)
        self.assertEqual(draped[0], pts[0])
        self.assertEqual(draped[-1], pts[-1])
        drift = max(dist(a, b) for a, b in zip(pts, draped))
        self.assertLess(drift, 0.1)

    def test_collider_pushes_wire_out(self):
        # Floor at z = -0.3 under a wire that would sag below it.
        def floor(p):
            return (p[0], p[1], -0.3), (0.0, 0.0, 1.0)

        pts = solver.solve((0, 0, 0), (4, 0, 0), 0.8, 31, drape_iterations=80, nearest=floor, gap=0.02)
        self.assertTrue(all(p[2] >= -0.3 + 0.02 - 1e-9 for p in pts))

    def test_wire_rests_on_box(self):
        # Box spanning x -0.3..0.3, z 1.5..2.5, infinite in y. The free
        # catenary would pass through it at z ~0.94.
        x0, x1, z0, z1 = -0.3, 0.3, 1.5, 2.5

        def nearest(p):
            x, y, z = p
            if x0 < x < x1 and z0 < z < z1:
                faces = [
                    (z1 - z, (x, y, z1), (0, 0, 1)),
                    (z - z0, (x, y, z0), (0, 0, -1)),
                    (x1 - x, (x1, y, z), (1, 0, 0)),
                    (x - x0, (x0, y, z), (-1, 0, 0)),
                ]
                _, surface, normal = min(faces)
                return surface, normal
            cx, cz = min(max(x, x0), x1), min(max(z, z0), z1)
            dx, dz = x - cx, z - cz
            length = math.hypot(dx, dz) or 1.0
            return (cx, y, cz), (dx / length, 0.0, dz / length)

        def cast(a, b):
            t0, t1, hit_normal = 0.0, 1.0, None
            for axis, lo, hi, n_lo, n_hi in ((0, x0, x1, (-1, 0, 0), (1, 0, 0)), (2, z0, z1, (0, 0, -1), (0, 0, 1))):
                d = b[axis] - a[axis]
                if abs(d) < 1e-12:
                    if not lo < a[axis] < hi:
                        return None
                    continue
                ta, tb = (lo - a[axis]) / d, (hi - a[axis]) / d
                na, nb = n_lo, n_hi
                if ta > tb:
                    ta, tb, na = tb, ta, nb
                if ta > t0:
                    t0, hit_normal = ta, na
                t1 = min(t1, tb)
                if t0 > t1:
                    return None
            if hit_normal is None:
                return None
            return tuple(a[i] + (b[i] - a[i]) * t0 for i in range(3)), hit_normal

        p0, p1 = (-3.0, 0.0, 3.0), (3.0, 0.0, 3.0)
        a = solver.catenary_a_for_length(p0, p1, 7.6)
        pts = solver.solve(p0, p1, a, 24, drape_iterations=40, nearest=nearest, gap=0.03, cast=cast)
        over = [p for p in pts if abs(p[0]) < 0.25]
        self.assertTrue(over)
        self.assertTrue(all(p[2] > z1 for p in over))
        self.assertAlmostEqual(solver.polyline_length(pts), 7.6, delta=0.05)

    def test_rope_does_not_stretch(self):
        pts, length = solver.catenary_points((0, 0, 0), (4, 0, 0), 1.0, 21)
        rest = length / 20
        draped = solver.drape(pts, rest, 50)
        for a, b in zip(draped, draped[1:]):
            self.assertLess(dist(a, b), rest * 1.05)


if __name__ == "__main__":
    unittest.main()
