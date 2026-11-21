# SPDX-License-Identifier: GPL-3.0-or-later

"""Trim slot tests. Run with: python -m unittest discover tests"""

import importlib.util
import math
import pathlib
import unittest

_path = pathlib.Path(__file__).resolve().parents[1] / "wible" / "trim.py"
_spec = importlib.util.spec_from_file_location("wible_trim", _path)
trim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trim)


class SlotTests(unittest.TestCase):
    def test_horizontal_strip(self):
        # Full-width strip, 64 px tall on a 1024 texture, no padding.
        layout = trim.slot_layout((0.0, 0.5, 1.0, 0.5625), 1024, 1024, 512.0, 0)
        self.assertTrue(layout.along_u)
        self.assertAlmostEqual(layout.across_px, 64.0)
        # 64 px around at 512 px/m is 0.125 m of circumference.
        self.assertAlmostEqual(layout.radius, 0.125 / (2 * math.pi))
        self.assertAlmostEqual(layout.across_scale, 0.0625)
        self.assertAlmostEqual(layout.length_scale, 0.0625)

    def test_vertical_strip_runs_along_v(self):
        layout = trim.slot_layout((0.25, 0.0, 0.3125, 1.0), 1024, 1024, 512.0, 0)
        self.assertFalse(layout.along_u)
        self.assertAlmostEqual(layout.across_px, 64.0)

    def test_padding_insets_half_on_each_side(self):
        layout = trim.slot_layout((0.0, 0.0, 1.0, 0.0625), 1024, 1024, 512.0, 4)
        u0, v0, u1, v1 = layout.rect
        self.assertAlmostEqual(u0 * 1024, 2.0)
        self.assertAlmostEqual(v0 * 1024, 2.0)
        self.assertAlmostEqual((1 - u1) * 1024, 2.0)
        self.assertAlmostEqual(layout.across_px, 60.0)
        self.assertAlmostEqual(layout.pad_layout, 4.0 / 60.0)

    def test_non_square_texture_keeps_square_texels(self):
        # 2048 x 512 texture: one UV unit is 2048 px across U, 512 px across V.
        layout = trim.slot_layout((0.0, 0.0, 1.0, 0.125), 2048, 512, 512.0, 0)
        self.assertTrue(layout.along_u)
        self.assertAlmostEqual(layout.across_px, 64.0)
        # One layout unit (64 px) is 64/512 of V and 64/2048 of U.
        self.assertAlmostEqual(layout.across_scale, 0.125)
        self.assertAlmostEqual(layout.length_scale, 0.03125)

    def test_padding_larger_than_slot_is_rejected(self):
        with self.assertRaises(ValueError):
            trim.slot_layout((0.0, 0.0, 1.0, 0.002), 1024, 1024, 512.0, 4)

    def test_faces_to_slots_order_and_bounds(self):
        faces = [
            [(0.0, 0.0), (1.0, 0.0), (1.0, 0.1), (0.0, 0.1)],
            [(0.0, 0.5), (0.5, 0.5), (0.5, 0.6), (0.0, 0.6)],
            [(0.5, 0.5), (1.0, 0.5), (1.0, 0.6), (0.5, 0.6)],
            [(0.2, 0.2), (0.2, 0.2), (0.2, 0.2)],  # degenerate, dropped
        ]
        rects = trim.faces_to_slots(faces)
        self.assertEqual(len(rects), 3)
        self.assertEqual(rects[0], (0.0, 0.5, 0.5, 0.6))
        self.assertEqual(rects[1], (0.5, 0.5, 1.0, 0.6))
        self.assertEqual(rects[2], (0.0, 0.0, 1.0, 0.1))


if __name__ == "__main__":
    unittest.main()
