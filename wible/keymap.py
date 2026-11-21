# SPDX-License-Identifier: GPL-3.0-or-later

"""Shift+Alt+X in the 3D View opens the Wible popup at the mouse.

The combination is unused in Blender's default keymap in every mode. It is
registered in the add-on keyconfig, so it can be changed in the add-on's
preferences or in Preferences > Keymap without touching anything else.
"""

import bpy
import rna_keymap_ui

from .ui import POPUP_PANEL

KEYMAP_NAME = "3D View"
DEFAULT_KEY = dict(type="X", value="PRESS", shift=True, alt=True)

_addon_keymaps = []


def _is_ours(kmi):
    return kmi.idname == "wm.call_panel" and getattr(kmi.properties, "name", "") == POPUP_PANEL


def register_keymap():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # background mode
        return
    km = kc.keymaps.new(name=KEYMAP_NAME, space_type="VIEW_3D")
    kmi = km.keymap_items.new("wm.call_panel", **DEFAULT_KEY)
    kmi.properties.name = POPUP_PANEL
    kmi.properties.keep_open = True
    _addon_keymaps.append((km, kmi))


def unregister_keymap():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _addon_keymaps.clear()


class WibleAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        layout = self.layout
        layout.label(text="Shortcut")
        kc = context.window_manager.keyconfigs.user
        km = kc.keymaps.get(KEYMAP_NAME)
        found = False
        if km is not None:
            for kmi in km.keymap_items:
                if _is_ours(kmi):
                    col = layout.column()
                    col.context_pointer_set("keymap", km)
                    rna_keymap_ui.draw_kmi([], kc, km, kmi, col, 0)
                    found = True
        if not found:
            layout.label(text="Shortcut not found. Restore it in Preferences > Keymap.", icon="ERROR")


def register():
    bpy.utils.register_class(WibleAddonPreferences)
    register_keymap()


def unregister():
    unregister_keymap()
    bpy.utils.unregister_class(WibleAddonPreferences)
