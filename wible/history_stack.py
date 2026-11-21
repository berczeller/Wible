# SPDX-License-Identifier: GPL-3.0-or-later

"""Per curve edit history: a list of snapshots with a current index.

Pure Python (snapshots are plain dicts), so it can be tested without Blender.
A snapshot has "settings" (name -> value), "ends" ((x, y, z), (x, y, z)) in
world space, "matrix" (16 floats) and "stroke" (flat list of floats).
"""

import json

MAX_STEPS = 50
DECIMALS = 5


def _round(value):
    if isinstance(value, float):
        return round(value, DECIMALS)
    if isinstance(value, (list, tuple)):
        return [_round(v) for v in value]
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    return value


def signature(state):
    """Stable text form of a snapshot, rounded so float noise doesn't count
    as a change."""
    return json.dumps(_round(state), sort_keys=True)


class History:
    def __init__(self, steps=None, index=-1):
        self.steps = list(steps or [])  # [{"label", "sig", "state"}]
        self.index = index if steps else -1

    @classmethod
    def loads(cls, text, index):
        try:
            steps = json.loads(text) if text else []
        except ValueError:
            steps = []
        if not isinstance(steps, list):
            steps = []
        index = min(max(index, -1), len(steps) - 1)
        if steps and index < 0:
            index = len(steps) - 1
        return cls(steps, index)

    def dumps(self):
        return json.dumps(self.steps)

    @property
    def current(self):
        return self.steps[self.index] if 0 <= self.index < len(self.steps) else None

    def matches_current(self, state):
        current = self.current
        return current is not None and current["sig"] == signature(state)

    def push(self, state, label=None):
        """Record `state` as the newest step. Steps after the current one (the
        ones you could redo) are dropped. Returns False if nothing changed."""
        sig = signature(state)
        current = self.current
        if current is not None and current["sig"] == sig:
            return False
        if label is None:
            label = describe(current["state"] if current else None, state)
        del self.steps[self.index + 1:]
        self.steps.append({"label": label, "sig": sig, "state": _round(state)})
        if len(self.steps) > MAX_STEPS:
            del self.steps[: len(self.steps) - MAX_STEPS]
        self.index = len(self.steps) - 1
        return True

    def jump(self, index):
        """Make step `index` current and return its state."""
        if not 0 <= index < len(self.steps):
            raise IndexError(index)
        self.index = index
        return self.steps[index]["state"]


def _format(value):
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:.3g}"
    if value is None or value == "":
        return "none"
    return str(value)


def describe(before, after, names=None):
    """Short label for what changed between two snapshots. `names` maps
    setting keys to display names."""
    if before is None:
        return "Drawn"
    names = names or {}
    parts = []
    settings_before = before.get("settings", {})
    settings_after = after.get("settings", {})
    changed = [k for k in settings_after if _round(settings_before.get(k)) != _round(settings_after.get(k))]

    ends_before, ends_after = before.get("ends"), after.get("ends")
    moved = []
    if ends_before and ends_after and len(ends_before) == len(ends_after):
        for i, (a, b) in enumerate(zip(ends_before, ends_after)):
            if _round(list(a)) != _round(list(b)):
                moved.append(i)
    matrix_changed = _round(before.get("matrix")) != _round(after.get("matrix"))

    kinds_before, kinds_after = before.get("kinds"), after.get("kinds")
    kind_names = ("Anchor", "Support", "Guide")
    if kinds_before and kinds_after and len(kinds_before) == len(kinds_after):
        for i, (a, b) in enumerate(zip(kinds_before, kinds_after)):
            if a != b and 0 <= a < 3 and 0 <= b < 3:
                parts.append(f"Point {i + 1}: {kind_names[a]} → {kind_names[b]}")
                break

    if matrix_changed:
        parts.append("Moved curve")
    elif len(moved) == 1:
        parts.append(f"Moved point {moved[0] + 1}")
    elif moved:
        parts.append(f"Moved {len(moved)} points")

    # Tension, weight and length are linked: changing one updates another.
    # Moving the ends updates tension and length, so don't list them then;
    # a weight change updates length; tension and length change together.
    if parts:
        changed = [k for k in changed if k not in ("tension", "length")]
    elif "weight" in changed:
        changed = [k for k in changed if k != "length"]

    def change(key, lower=False):
        label = names.get(key, key.replace("_", " ").title())
        if lower:
            label = label.lower()
        return f"{label} {_format(settings_before.get(key))} → {_format(settings_after.get(key))}"

    if "tension" in changed and "length" in changed:
        parts.append(f"{change('tension')}, {change('length', lower=True)}")
        changed = [k for k in changed if k not in ("tension", "length")]
        if changed:
            parts.append(f"+{len(changed)} more")
    else:
        for key in changed[:1]:
            parts.append(change(key))
        if len(changed) > 1:
            parts.append(f"+{len(changed) - 1} more")
    if not parts and _round(before.get("stroke")) != _round(after.get("stroke")):
        parts.append("Shape changed")
    return ", ".join(parts) if parts else "Changed"
