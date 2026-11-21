# SPDX-License-Identifier: GPL-3.0-or-later

from . import icons, props, draw, operators, live, history, ui, keymap

_modules = (icons, props, draw, operators, live, history, ui, keymap)


def register():
    for module in _modules:
        module.register()


def unregister():
    for module in reversed(_modules):
        module.unregister()
