# SPDX-License-Identifier: GPL-3.0-or-later

"""Multi-point curve solver tests. Run with: python -m unittest discover tests"""

import importlib.util
import math
import pathlib
import unittest

_path = pathlib.Path(__file__).resolve().parents[1] / "wible" / "solver.py"
_spec = importlib.util.spec_from_file_location("wible_solver_path", _path)
solver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(solver)

A, S, G = solver.ANCHOR, solver.SUPPORT, solver.GUIDE


class PathTests(unittest.TestCase):
    def test_runs_split_at_anchors_and_ends_are_anchors(self):
        kinds = solver.normalize_kinds([G, S, A, G, S])
        self.assertEqual(kinds, [A, S, A, G, A])
        self.assertEqual(solver.runs(kinds), [(0, 2), (2, 4)])
        self.assertEqual(solver.hard_indices(kinds, 0, 2), [0, 1, 2])
        self.assertEqual(solver.hard_spans(kinds), [(0, 1), (1, 2), (2, 4)])

    def test_a_for_run_matches_length(self):
        pts = [(0, 0, 0), (2, 0, 0.5), (5, 0, 0)]
        hard = [0, 1, 2]
        a = solver.a_for_run(pts, hard, 6.5)
        self.assertAlmostEqual(solver.run_length(pts, hard, a), 6.5, places=3)
        self.assertEqual(solver.a_for_run(pts, hard, 1.0), math.inf)

    def test_points_land_on_samples(self):
        pts = [(0, 0, 0), (2, 0, 0), (3, 0, 0), (6, 0, 0)]
        kinds = [A, G, S, A]
        count, where = solver.sample_layout(pts, kinds, 8)
        self.assertEqual(count, 17)  # two hard spans of 8 segments
        self.assertEqual((where[0], where[2], where[3]), (0, 8, 16))
        self.assertTrue(0 < where[1] < 8)

    def test_z_shape_through_anchors(self):
        pts = [(0, 0, 3), (2, 0, 1), (4, 0, 3), (6, 0, 1)]
        kinds = [A, A, A, A]
        lengths = [solver.chord_length(pts, [i, i + 1]) * 1.2 for i in range(3)]
        out = solver.solve_path(pts, kinds, [1] * 4, lengths, 10)
        _count, where = solver.sample_layout(pts, kinds, 10)
        for i, p in enumerate(pts):
            self.assertLess(math.dist(out[where[i]], p), 1e-9)
        # Each span sags below the line between its anchors.
        mid = out[where[0] + 5]
        self.assertLess(mid[2], 2.0)

    def test_support_lets_slack_slide(self):
        # Same total length through a support: one shared parameter, so the
        # longer span takes more of the slack than with a fixed split.
        pts = [(0, 0, 0), (1, 0, 0), (5, 0, 0)]
        kinds = [A, S, A]
        out = solver.solve_path(pts, kinds, [1] * 3, [6.0], 12)
        _count, where = solver.sample_layout(pts, kinds, 12)
        short = min(p[2] for p in out[: where[1] + 1])
        long_ = min(p[2] for p in out[where[1]:])
        self.assertLess(long_, short)
        self.assertAlmostEqual(solver.polyline_length(out), 6.0, delta=0.05)

    def test_guide_pulls_toward_it(self):
        pts = [(0, 0, 0), (3, 0, 1.0), (6, 0, 0)]
        kinds = [A, G, A]
        free = solver.solve_path(pts, kinds, [1, 0.0, 1], [7.0], 16)
        pulled = solver.solve_path(pts, kinds, [1, 1.0, 1], [7.0], 16)
        _count, where = solver.sample_layout(pts, kinds, 16)
        g = where[1]
        self.assertLess(math.dist(pulled[g], pts[1]), math.dist(free[g], pts[1]))
        self.assertTrue(all(math.isfinite(c) for p in pulled for c in p))


if __name__ == "__main__":
    unittest.main()
