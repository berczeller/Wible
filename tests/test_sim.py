# SPDX-License-Identifier: GPL-3.0-or-later

"""Rope dynamics tests. Run with: python -m unittest discover tests"""

import importlib.util
import math
import pathlib
import unittest

_root = pathlib.Path(__file__).resolve().parents[1] / "wible"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"wible_{name}", _root / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sim = _load("sim")
solver = _load("solver")

DT = 1.0 / 60.0


def rest_shape(p0, p1, length, count):
    a = solver.catenary_a_for_length(p0, p1, length)
    pts, _ = solver.catenary_points(p0, p1, a, count)
    return pts, sim.segment_lengths(pts)


class RopeTests(unittest.TestCase):
    def test_at_rest_stays_put(self):
        p0, p1 = (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)
        target, seg = rest_shape(p0, p1, 5.0, 24)
        rope = sim.Rope(target)
        for _ in range(120):
            rope.step(DT, p0, p1, seg, target=target, settle=4.0)
        # A chain of links rests within a couple of cm of the exact catenary.
        self.assertLess(rope.distance_to(target), 0.03)

    def test_moving_ends_makes_it_swing_then_settle(self):
        p0, p1 = (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)
        target, seg = rest_shape(p0, p1, 5.0, 24)
        rope = sim.Rope(target)
        # Jerk both ends sideways by 1 m, like a quick grab and move.
        q0, q1 = (0.0, 1.0, 0.0), (4.0, 1.0, 0.0)
        moved_target = [(x, y + 1.0, z) for x, y, z in target]
        rope.step(DT, q0, q1, seg)
        lag = rope.distance_to(moved_target)
        self.assertGreater(lag, 0.3)  # middle lags behind: inertia

        swing = []
        for _ in range(60):
            rope.step(DT, q0, q1, seg, damping=0.6)
            swing.append(rope.pos[12][1])
        # The middle overshoots past the new rest plane at some point.
        self.assertGreater(max(swing), 1.0 + 0.02)

        for _ in range(300):
            rope.step(DT, q0, q1, seg, damping=0.6, target=moved_target, settle=3.0)
        self.assertLess(rope.distance_to(moved_target), 0.05)
        self.assertLess(rope.speed(DT), 0.05)

        for _ in range(60):
            rope.blend(moved_target, 0.15)
        self.assertLess(rope.distance_to(moved_target), 1e-4)

    def test_length_is_kept(self):
        p0, p1 = (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)
        target, seg = rest_shape(p0, p1, 5.0, 24)
        rope = sim.Rope(target)
        for _ in range(200):
            rope.step(DT, p0, (3.0, 0.5, 1.0), seg)
        length = sum(math.dist(a, b) for a, b in zip(rope.pos, rope.pos[1:]))
        self.assertLess(length, 5.0 * 1.02)

    def test_collides_with_floor(self):
        def floor(p):
            return (p[0], p[1], -0.4), (0.0, 0.0, 1.0)

        p0, p1 = (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)
        target, seg = rest_shape(p0, p1, 5.0, 24)
        rope = sim.Rope(target)
        for _ in range(240):
            rope.step(DT, p0, p1, seg, nearest=floor, gap=0.01)
        self.assertTrue(all(p[2] >= -0.4 + 0.01 - 1e-9 for p in rope.pos))

    def test_stays_finite_with_extreme_settings(self):
        p0, p1 = (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)
        target, seg = rest_shape(p0, p1, 9.0, 48)
        rope = sim.Rope(target)
        for i in range(300):
            end = (4.0 + math.sin(i * 0.3) * 2.0, 0.0, math.cos(i * 0.2))
            rope.step(0.1, p0, end, seg, damping=0.0, stiffness=1.0)
        self.assertTrue(all(math.isfinite(c) for p in rope.pos for c in p))


class PinnedRopeTests(unittest.TestCase):
    def test_interior_pin_holds(self):
        p0, p1 = (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)
        target, seg = rest_shape(p0, p1, 5.0, 24)
        rope = sim.Rope(target)
        pin = (2.0, 0.5, 0.5)
        for _ in range(120):
            rope.step(DT, p0, p1, seg, pins={12: pin})
        self.assertLess(math.dist(rope.pos[12], pin), 1e-9)
        self.assertTrue(all(math.isfinite(c) for p in rope.pos for c in p))

    def test_guide_pulls_rope(self):
        p0, p1 = (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)
        target, seg = rest_shape(p0, p1, 5.0, 24)
        guide = (2.0, 0.0, 0.2)
        free = sim.Rope(target)
        pulled = sim.Rope(target)
        for _ in range(180):
            free.step(DT, p0, p1, seg)
            pulled.step(DT, p0, p1, seg, guides=[(12, guide, 1.0)])
        self.assertLess(math.dist(pulled.pos[12], guide), math.dist(free.pos[12], guide))


if __name__ == "__main__":
    unittest.main()
