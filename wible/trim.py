# SPDX-License-Identifier: GPL-3.0-or-later

"""Trim sheet and hotspot slots: fit a curve's UVs into a face's UV
rectangle at a given texel density.

Pure Python, so it can be tested without Blender.

A slot is the UV bounding rectangle of one face on the trim plane. Its long
side follows the length of the curve (and repeats past the rectangle, as
trim strips tile); its short side wraps once around it. That short side, in
pixels, divided by the texel density gives the circumference, so the slot
sets the thickness too.
"""

import math
from dataclasses import dataclass


@dataclass
class SlotLayout:
    rect: tuple  # inset (u0, v0, u1, v1) in UV units
    along_u: bool  # length runs along U (the slot is wider than tall)
    across_px: float  # pixels around the circumference
    radius: float  # curve radius in meters for the texel density
    across_scale: float  # UV units per layout unit across the curve
    length_scale: float  # UV units per layout unit along it
    pad_layout: float  # padding between islands, in layout units
    length_units: float  # the slot's long side, in layout units

    def caps_fit(self, cap_count=2):
        """Whether the cap islands fit in the slot before it repeats.
        Caps are 1/pi layout units wide (texel-correct), `pad_layout` apart."""
        need = self.pad_layout + cap_count * (1.0 / math.pi + self.pad_layout)
        return need <= self.length_units + 1e-9


def slot_layout(rect, tex_w, tex_h, texel_density, padding_px):
    """Layout for a slot rectangle (u0, v0, u1, v1) on a tex_w x tex_h texture.

    Layout units are circumferences: 1 unit across is the full way around the
    curve, 1 unit along is the same distance along it. Padding insets the
    rectangle by half the padding on every side, so neighbouring slots end up
    `padding_px` apart, and also separates the cap islands from the side.
    """
    u0, v0, u1, v1 = rect
    u0, u1 = min(u0, u1), max(u0, u1)
    v0, v1 = min(v0, v1), max(v0, v1)
    half_u = padding_px * 0.5 / tex_w
    half_v = padding_px * 0.5 / tex_h
    inset = (u0 + half_u, v0 + half_v, u1 - half_u, v1 - half_v)
    if inset[2] <= inset[0] or inset[3] <= inset[1]:
        raise ValueError("Slot is smaller than its padding")

    w_px = (inset[2] - inset[0]) * tex_w
    h_px = (inset[3] - inset[1]) * tex_h
    along_u = w_px >= h_px
    across_px = h_px if along_u else w_px
    across_tex = tex_h if along_u else tex_w
    length_tex = tex_w if along_u else tex_h
    return SlotLayout(
        rect=inset,
        along_u=along_u,
        across_px=across_px,
        radius=across_px / (texel_density * 2.0 * math.pi),
        across_scale=across_px / across_tex,
        length_scale=across_px / length_tex,
        pad_layout=padding_px / across_px,
        length_units=(w_px if along_u else h_px) / across_px,
    )


def slot_size_px(rect, tex_w, tex_h):
    """(width, height) of a slot rectangle in pixels."""
    u0, v0, u1, v1 = rect
    return abs(u1 - u0) * tex_w, abs(v1 - v0) * tex_h


def faces_to_slots(face_uvs):
    """UV bounding rectangles for faces given as lists of (u, v) corners,
    ordered top to bottom, then left to right, like reading the texture."""
    rects = []
    for corners in face_uvs:
        if not corners:
            continue
        us = [c[0] for c in corners]
        vs = [c[1] for c in corners]
        rect = (min(us), min(vs), max(us), max(vs))
        if rect[2] - rect[0] > 1e-9 and rect[3] - rect[1] > 1e-9:
            rects.append(rect)
    rects.sort(key=lambda r: (-round(r[3], 6), round(r[0], 6)))
    return rects
