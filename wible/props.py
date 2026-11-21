# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
)

from . import build

# Settings that define a wire's shape. Kept in one list so they can be copied
# between wires and from the scene defaults.
WIRE_SETTINGS = (
    "gravity",
    "tension",
    "weight",
    "length",
    "physics",
    "stiffness",
    "damping",
    "drape",
    "drape_collection",
    "drape_iterations",
    "drape_gap",
    "point_count",
    "smoothing",
    "resolution",
    "depth",
    "bevel_resolution",
    "fill_caps",
    "use_trim",
    "trim_slot",
)

_syncing = False


def _sync_length(obj):
    set_silent(obj.wible, "length", build.length_for_tension(obj))


def _sync_tension(obj):
    build.scale_runs_to_length(obj, obj.wible.length)
    tension = build.tension_for_length(obj)
    if tension is not None:
        set_silent(obj.wible, "tension", tension)


def _make_update(name, after=None, shape=True):
    """Push the edited value to the other selected wires, then refresh them.

    `after(obj)` runs on each wire before the refresh. Shape settings let a
    wire with physics swing into its new shape; the rest just redraw.
    """

    def update(self, context):
        global _syncing
        if _syncing:
            return
        obj = self.id_data
        if not isinstance(obj, bpy.types.Object) or not self.is_wire:
            return
        _syncing = True
        try:
            value = getattr(self, name)
            targets = [obj] + [o for o in _selected_wires(context) if o != obj]
            for wire in targets:
                if wire != obj:
                    setattr(wire.wible, name, value)
                if after is not None:
                    after(wire)
                if shape:
                    build.rebuild(wire, animate=True)
                else:
                    build.refresh(wire)
        finally:
            _syncing = False

    return update


def set_silent(settings, name, value):
    """Set a wire setting without running its update callback."""
    global _syncing
    previous = _syncing
    _syncing = True
    try:
        setattr(settings, name, value)
    finally:
        _syncing = previous


def _selected_wires(context):
    objects = getattr(context, "selected_objects", None) or ()
    return [o for o in objects if o.wible.is_wire and not o.wible.accepted]


def copy_settings(src, dst):
    global _syncing
    previous = _syncing
    _syncing = True
    try:
        for name in WIRE_SETTINGS:
            setattr(dst, name, getattr(src, name))
    finally:
        _syncing = previous


class WibleWireSettings(bpy.types.PropertyGroup):
    is_wire: BoolProperty(default=False, options={"HIDDEN"})
    accepted: BoolProperty(default=False, options={"HIDDEN"})

    gravity: BoolProperty(
        name="Gravity",
        description=(
            "Let the curve hang between its points. Off keeps the placed "
            "shape, which is the lightest option"
        ),
        default=True,
        update=_make_update("gravity"),
    )
    tension: FloatProperty(
        name="Tension",
        description=(
            "Pull along the curve. Higher values hang tighter. Changing it "
            "re-solves the shape; it doesn't change performance"
        ),
        default=2.0,
        min=0.001,
        soft_min=0.05,
        soft_max=50.0,
        precision=3,
        update=_make_update("tension", after=_sync_length),
    )
    weight: FloatProperty(
        name="Weight",
        description="Weight per meter. Heavier curves sag more at the same tension",
        default=1.0,
        min=0.001,
        soft_min=0.05,
        soft_max=10.0,
        precision=3,
        update=_make_update("weight", after=_sync_length),
    )
    length: FloatProperty(
        name="Length",
        description=(
            "Total length of the curve. Moving its points keeps this length; "
            "pulling them further apart than it makes the curve taut"
        ),
        default=0.0,
        min=0.0,
        soft_max=100.0,
        precision=3,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_make_update("length", after=_sync_tension),
    )
    physics: BoolProperty(
        name="Physics",
        description=(
            "Swing and settle when the curve or its points move. Each swinging "
            "curve costs a little every frame until it settles. Tip: turn off "
            "when editing many curves in a heavy scene"
        ),
        default=True,
        update=_make_update("physics"),
    )
    stiffness: FloatProperty(
        name="Stiffness",
        description=(
            "How much the curve resists bending away from its hanging shape. "
            "Doesn't change performance"
        ),
        default=0.2,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_make_update("stiffness", shape=False),
    )
    damping: FloatProperty(
        name="Damping",
        description=(
            "How quickly swinging dies down. Low values swing longer and keep the "
            "simulation running longer; 0.4 to 0.8 settles quickly"
        ),
        default=0.6,
        min=0.0,
        max=0.99,
        subtype="FACTOR",
        update=_make_update("damping", shape=False),
    )
    drape: BoolProperty(
        name="Drape",
        description=(
            "Let the curve collide with and rest on other objects: those in "
            "Drape Over, or every visible object near it. Each nearby object adds "
            "collision checks. Tip: in heavy scenes, set Drape Over to only the "
            "objects curves touch"
        ),
        default=True,
        update=_make_update("drape"),
    )
    drape_collection: PointerProperty(
        name="Drape Over",
        description=(
            "Limit collisions to the objects in this collection. Empty: every "
            "visible object near the curve. Tip: a small collection of simple "
            "objects keeps physics fast; very dense meshes cost the most"
        ),
        type=bpy.types.Collection,
        update=_make_update("drape_collection"),
    )
    drape_iterations: IntProperty(
        name="Iterations",
        description=(
            "How long a resting shape is searched for when the curve touches "
            "other objects. Higher settles tangled cases better, but with Physics "
            "off every setting change waits for it. 20 is enough for most scenes"
        ),
        default=20,
        min=1,
        soft_max=60,
        max=100,
        update=_make_update("drape_iterations"),
    )
    drape_gap: FloatProperty(
        name="Gap",
        description=(
            "Extra distance kept between the curve surface and colliders. "
            "Doesn't change performance"
        ),
        default=0.0,
        soft_min=-0.05,
        soft_max=0.1,
        precision=4,
        step=0.1,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_make_update("drape_gap"),
    )

    point_count: IntProperty(
        name="Points",
        description=(
            "Points per span (between two placed points). More follow the hanging "
            "shape and colliders more closely, but cost more in physics, collisions "
            "and polygons. 12 to 24 is plenty; points are added automatically near "
            "thin colliders"
        ),
        default=16,
        min=2,
        soft_max=48,
        max=128,
        update=_make_update("point_count"),
    )
    smoothing: IntProperty(
        name="Smoothing",
        description=(
            "Smoothing passes for curves made with Wible's first version, "
            "when Gravity is off"
        ),
        default=2,
        min=0,
        max=10,
        update=_make_update("smoothing"),
    )
    resolution: IntProperty(
        name="Resolution",
        soft_max=8,
        description=(
            "Curve segments between points. Mostly sets how smooth the curve "
            "looks and the polygon count of the mesh. 3 to 6 is usually enough; "
            "it's lowered automatically where colliders add points"
        ),
        default=4,
        min=1,
        max=16,
        update=_make_update("resolution", shape=False),
    )
    depth: FloatProperty(
        name="Depth",
        description="Radius of the curve. Doesn't change performance",
        default=0.01,
        min=0.0,
        soft_max=0.5,
        precision=4,
        step=0.1,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_make_update("depth", shape=False),
    )
    bevel_resolution: IntProperty(
        name="Radial Resolution",
        soft_max=4,
        description=(
            "Roundness of the curve: 1 gives 6 sides, 2 gives 8, 4 gives 12. "
            "Each step adds 2 sides along the whole length; 1 or 2 is enough for "
            "thin curves"
        ),
        default=2,
        min=0,
        max=8,
        update=_make_update("bevel_resolution", shape=False),
    )
    fill_caps: BoolProperty(
        name="Fill Caps",
        description="Close both ends of the curve. Adds two small faces",
        default=True,
        update=_make_update("fill_caps", shape=False),
    )
    use_trim: BoolProperty(
        name="Trim Sheet",
        description=(
            "Fit this curve's UVs into a slot of the trim sheet or hotspot "
            "texture. The slot also sets its thickness"
        ),
        default=False,
        update=_make_update("use_trim", after=lambda o: build.apply_trim(o), shape=False),
    )
    trim_slot: IntProperty(
        name="Slot",
        description="Which captured face of the trim plane to use (numbered top to bottom)",
        default=1,
        min=1,
        update=_make_update("trim_slot", after=lambda o: build.apply_trim(o), shape=False),
    )



def mesh_option_annotations():
    """Mesh conversion options, shared by the scene settings and the operator
    so the operator's redo panel shows the same controls."""
    return {
        "shading": EnumProperty(
            name="Shading",
            items=(
                ("SMOOTH", "Smooth", "Smooth normals around the curve"),
                ("FLAT", "Flat", "Faceted normals"),
            ),
            default="SMOOTH",
        ),
        "cap_edges": EnumProperty(
            name="Cap Edges",
            description="How caps meet the side of the curve",
            items=(
                ("HARD", "Hard", "Sharp edge between cap and side"),
                ("BEVEL", "Bevel", "Rounded edge, like the Bevel modifier"),
            ),
            default="HARD",
        ),
        "bevel_width": FloatProperty(
            name="Amount",
            description="Bevel width on the cap edges. Applied when saving as mesh",
            default=0.002,
            min=0.0,
            soft_max=0.05,
            precision=4,
            step=0.01,
            subtype="DISTANCE",
            unit="LENGTH",
        ),
        "bevel_segments": IntProperty(
            name="Segments",
            description=(
                "Segments in the cap bevel. Each adds a ring of faces at both ends; 1 "
                "to 3 is usually enough"
            ),
            soft_max=4,
            default=2,
            min=1,
            max=8,
        ),
        "bevel_profile": FloatProperty(
            name="Shape",
            description=(
                "Bevel profile shape: 0.25 is a flat chamfer, 0.5 round, toward 1 "
                "square"
            ),
            default=0.5,
            min=0.0,
            max=1.0,
            subtype="FACTOR",
        ),
        "harden_normals": BoolProperty(
            name="Harden Normals",
            description=(
                "Keep the flat cap shading crisp next to the bevel. Stores custom "
                "normals on the mesh"
            ),
            default=False,
        ),
        "length_along": EnumProperty(
            name="Length Along",
            description="UV axis that follows the length of the curve",
            items=(
                ("V", "V", "Length runs up the UV space"),
                ("U", "U", "Length runs across the UV space"),
            ),
            default="V",
        ),
        "uv_padding": FloatProperty(
            name="Island Padding",
            description=(
                "UV space between the side island and the cap islands. Ignored when a "
                "trim slot is used"
            ),
            default=0.01,
            min=0.0,
            soft_max=0.1,
            precision=3,
        ),
    }


MESH_OPTIONS = tuple(mesh_option_annotations().keys())


WibleMeshSettings = type(
    "WibleMeshSettings",
    (bpy.types.PropertyGroup,),
    {"__annotations__": mesh_option_annotations()},
)


class WibleTrimSlot(bpy.types.PropertyGroup):
    u0: FloatProperty()
    v0: FloatProperty()
    u1: FloatProperty()
    v1: FloatProperty()

    @property
    def rect(self):
        return (self.u0, self.v0, self.u1, self.v1)


def _on_trim_setup_change(self, context):
    """Texture size, texel density or padding changed: refit every curve
    that uses the trim sheet."""
    scene = self.id_data
    for obj in scene.objects:
        if obj.type == "CURVE" and obj.wible.is_wire and not obj.wible.accepted and obj.wible.use_trim:
            build.apply_trim(obj)


def _is_mesh(_self, obj):
    return obj.type == "MESH"


class WibleSceneSettings(bpy.types.PropertyGroup):
    trim_object: PointerProperty(
        name="Trim Plane",
        description="Mesh whose faces mark the slots of your trim sheet or hotspot texture in UV space",
        type=bpy.types.Object,
        poll=_is_mesh,
    )
    trim_slots: CollectionProperty(type=WibleTrimSlot, options={"HIDDEN"})
    # Last live curve that was active: the panel keeps showing it while
    # the trim plane is selected, so it doesn't shrink while picking faces.
    last_wire: PointerProperty(type=bpy.types.Object, options={"HIDDEN"})
    texture_width: IntProperty(
        name="Texture Width",
        description="Width of the trim sheet texture in pixels",
        default=1024,
        min=1,
        soft_max=8192,
        subtype="PIXEL",
        update=_on_trim_setup_change,
    )
    texture_height: IntProperty(
        name="Height",
        description="Height of the trim sheet texture in pixels",
        default=1024,
        min=1,
        soft_max=8192,
        subtype="PIXEL",
        update=_on_trim_setup_change,
    )
    texel_density: FloatProperty(
        name="Texel Density",
        description="Pixels per meter. With the slot's height this sets the curve's thickness",
        default=512.0,
        min=1.0,
        soft_max=4096.0,
        precision=1,
        update=_on_trim_setup_change,
    )
    trim_padding: IntProperty(
        name="Padding",
        description="Pixels between UV islands: between slots, and between the side and the caps",
        default=4,
        min=0,
        soft_max=64,
        subtype="PIXEL",
        update=_on_trim_setup_change,
    )

    placement: EnumProperty(
        name="Placement",
        description="Where clicked points land",
        items=(
            ("SNAP_ENDS", "Snap Ends", "Points land on the surface under the pointer; over empty space, on a plane facing the view through the previous point"),
            ("VIEW_PLANE", "View Plane", "Points always land on a plane facing the view, through the previous point (the 3D cursor for the first)"),
        ),
        default="SNAP_ENDS",
    )
    only_selected: BoolProperty(
        name="Only Selected",
        description=(
            "Place points only on the objects selected when Draw started (any "
            "object if none were selected). Tip: selecting the objects to attach to "
            "keeps placing fast in big scenes"
        ),
        default=True,
    )
    defaults: PointerProperty(type=WibleWireSettings)
    mesh: PointerProperty(type=WibleMeshSettings)


classes = (WibleWireSettings, WibleMeshSettings, WibleTrimSlot, WibleSceneSettings)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.wible = PointerProperty(type=WibleWireSettings)
    bpy.types.Scene.wible = PointerProperty(type=WibleSceneSettings)


def unregister():
    del bpy.types.Scene.wible
    del bpy.types.Object.wible
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
