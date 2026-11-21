# SPDX-License-Identifier: GPL-3.0-or-later

"""Custom UI icons (wible/icons/*.png, made by tools/make_icons.py)."""

import pathlib

import bpy.utils.previews

NAMES = ("wible", "draw", "shape", "physics", "geometry", "mesh")
_DIR = pathlib.Path(__file__).parent / "icons"
_previews = None


def get(name):
    """icon_value for `name`, or 0 (no icon) if it isn't loaded."""
    if _previews is None or name not in _previews:
        return 0
    return _previews[name].icon_id


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    for name in NAMES:
        path = _DIR / f"{name}.png"
        if path.exists():
            _previews.load(name, str(path), "IMAGE")


def unregister():
    global _previews
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
