# SPDX-License-Identifier: GPL-3.0-or-later

"""Edit history tests. Run with: python -m unittest discover tests"""

import importlib.util
import pathlib
import unittest

_path = pathlib.Path(__file__).resolve().parents[1] / "wible" / "history_stack.py"
_spec = importlib.util.spec_from_file_location("wible_history_stack", _path)
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)


def state(tension=2.0, depth=0.01, end_b=(2.0, 0.0, 0.0), matrix=None):
    return {
        "settings": {"tension": tension, "depth": depth, "fill_caps": True},
        "ends": [(0.0, 0.0, 0.0), end_b],
        "matrix": matrix or [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0],
        "stroke": [0.0, 0.0, 0.0, 2.0, 0.0, 0.0],
    }


class HistoryTests(unittest.TestCase):
    def test_first_step_is_drawn(self):
        h = hs.History()
        self.assertTrue(h.push(state()))
        self.assertEqual(h.steps[0]["label"], "Drawn")
        self.assertEqual(h.index, 0)

    def test_same_state_is_not_recorded(self):
        h = hs.History()
        h.push(state())
        self.assertFalse(h.push(state(tension=2.0000001)))
        self.assertEqual(len(h.steps), 1)

    def test_labels(self):
        h = hs.History()
        h.push(state())
        h.push(state(tension=4.1))
        self.assertEqual(h.steps[-1]["label"], "Tension 2 → 4.1")
        h.push(state(tension=4.1, end_b=(3.0, 0.0, 0.0)))
        self.assertEqual(h.steps[-1]["label"], "Moved point 2")
        moved = state(tension=4.1, end_b=(3.0, 0.0, 0.0), matrix=[1.0, 0, 0, 5.0] + [0] * 12)
        h.push(moved)
        self.assertEqual(h.steps[-1]["label"], "Moved curve")

    def test_moving_ends_hides_tension_change(self):
        label = hs.describe(state(), state(tension=3.3, end_b=(3.0, 0.0, 0.0)))
        self.assertEqual(label, "Moved point 2")

    def test_linked_settings(self):
        before = state()
        before["settings"].update(length=4.0, weight=1.0)
        after = state(tension=5.0)
        after["settings"].update(length=3.5, weight=1.0)
        self.assertEqual(hs.describe(before, after, {"tension": "Tension", "length": "Length"}),
                         "Tension 2 → 5, length 4 → 3.5")
        heavier = state()
        heavier["settings"].update(length=4.4, weight=2.0)
        self.assertEqual(hs.describe(before, heavier, {"weight": "Weight"}), "Weight 1 → 2")

    def test_several_settings(self):
        label = hs.describe(state(), state(tension=3.0, depth=0.02), names={"depth": "Depth"})
        self.assertTrue(label.endswith("+1 more"))

    def test_jump_and_branch(self):
        h = hs.History()
        for t in (1.0, 2.0, 3.0, 4.0):
            h.push(state(tension=t))
        self.assertEqual(h.jump(1)["settings"]["tension"], 2.0)
        self.assertTrue(h.matches_current(state(tension=2.0)))
        # A new change after jumping back drops the later steps.
        h.push(state(tension=9.0))
        self.assertEqual([s["state"]["settings"]["tension"] for s in h.steps], [1.0, 2.0, 9.0])
        self.assertEqual(h.index, 2)

    def test_cap(self):
        h = hs.History()
        for i in range(hs.MAX_STEPS + 10):
            h.push(state(tension=float(i)))
        self.assertEqual(len(h.steps), hs.MAX_STEPS)
        self.assertEqual(h.steps[0]["state"]["settings"]["tension"], 10.0)
        self.assertEqual(h.index, hs.MAX_STEPS - 1)

    def test_roundtrip_and_bad_data(self):
        h = hs.History()
        h.push(state())
        h.push(state(tension=3.0))
        h.jump(0)
        back = hs.History.loads(h.dumps(), h.index)
        self.assertEqual(back.index, 0)
        self.assertEqual(len(back.steps), 2)
        broken = hs.History.loads("not json", 5)
        self.assertEqual((broken.steps, broken.index), ([], -1))
        clamped = hs.History.loads(h.dumps(), 99)
        self.assertEqual(clamped.index, 1)


if __name__ == "__main__":
    unittest.main()
