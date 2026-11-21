# SPDX-License-Identifier: GPL-3.0-or-later

import dataclasses

import bpy

from . import build, mesh_build, props

MESH_OPTIONS_KEY = "wible_mesh"


def wire_of(obj):
    """The wire for an object: itself, or the wire an end handle belongs to."""
    if obj is None:
        return None
    if obj.get(build.HANDLE_KEY) is not None and obj.parent is not None and obj.parent.wible.is_wire:
        return obj.parent
    return obj


def _wires(context, accepted=None, types=None):
    objects = []
    for o in list(context.selected_objects) + [context.object]:
        o = wire_of(o)
        if o is not None and o not in objects:
            objects.append(o)
    out = []
    for o in objects:
        if not o.wible.is_wire:
            continue
        if accepted is not None and o.wible.accepted != accepted:
            continue
        if types is not None and o.type not in types:
            continue
        out.append(o)
    return out


def draw_cap_options(layout, settings):
    """Cap edge controls (applied when converting to mesh)."""
    layout.prop(settings, "cap_edges")
    if settings.cap_edges == "BEVEL":
        col = layout.column(align=True)
        col.prop(settings, "bevel_width", text="Bevel Amount")
        col.prop(settings, "bevel_segments", text="Bevel Segments")
        col.prop(settings, "bevel_profile", text="Bevel Shape")
        layout.prop(settings, "harden_normals")


def draw_mesh_options(layout, settings, caps=True, shading=True, trim_on=False):
    if shading:
        layout.prop(settings, "shading")
    if caps:
        draw_cap_options(layout, settings)
    # A trim slot sets the orientation and spacing itself.
    col = layout.column()
    col.active = not trim_on
    col.prop(settings, "length_along", expand=True)
    col.prop(settings, "uv_padding")


def _replace_object(old, new, view_layer):
    """Put `new` where `old` was (collections, parent, transform, name) and
    delete `old` with its data."""
    new.parent = old.parent
    new.matrix_parent_inverse = old.matrix_parent_inverse.copy()
    new.matrix_world = old.matrix_world.copy()
    for coll in old.users_collection:
        coll.objects.link(new)
    for child in old.children:
        child.parent = new
    selected = old.select_get(view_layer=view_layer)
    name = old.name
    data = old.data
    bpy.data.objects.remove(old)
    if data is not None and data.users == 0:
        if isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
        elif isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
    new.name = name
    if new.data is not None:
        new.data.name = name
    new.select_set(selected, view_layer=view_layer)


def _copy_wire_data(src, dst):
    from . import history
    props.copy_settings(src.wible, dst.wible)
    dst[build.STROKE_KEY] = list(src[build.STROKE_KEY]) if build.STROKE_KEY in src else []
    for key in (build.KINDS_KEY, build.STRENGTH_KEY, build.RUNS_KEY, build.SHAPE_KEY):
        if key in src:
            dst[key] = list(src[key])
    history.copy_history(src, dst)
    dst.wible.is_wire = True


class WIBLE_OT_accept_curve(bpy.types.Operator):
    bl_idname = "wible.accept_curve"
    bl_label = "Save as Curve"
    bl_description = (
        "Finish editing the selected curves and keep them as Blender curve objects. "
        "The lightest way to keep many in a scene: polygons are only made at render time"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_wires(context, accepted=False, types={"CURVE"}))

    def execute(self, context):
        wires = _wires(context, accepted=False, types={"CURVE"})
        for obj in wires:
            build.finalize(obj)
            obj.wible.accepted = True
        self.report({"INFO"}, f"Saved {len(wires)} curve(s)")
        return {"FINISHED"}


class WIBLE_OT_accept_mesh(bpy.types.Operator):
    bl_idname = "wible.accept_mesh"
    bl_label = "Save as Mesh"
    bl_description = (
        "Convert the selected curves to meshes with clean UVs. Each one gets about "
        "Points x Resolution x sides faces per span; lower Resolution or Radial Resolution "
        "first if you're saving many"
    )
    bl_options = {"REGISTER", "UNDO"}

    __annotations__ = props.mesh_option_annotations()

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_wires(context, types={"CURVE"}))

    def invoke(self, context, event):
        settings = context.scene.wible.mesh
        for name in props.MESH_OPTIONS:
            setattr(self, name, getattr(settings, name))
        return self.execute(context)

    def mesh_options(self):
        return mesh_build.MeshOptions(
            shade_smooth=self.shading == "SMOOTH",
            cap_edges=self.cap_edges,
            bevel_width=self.bevel_width,
            bevel_segments=self.bevel_segments,
            bevel_profile=self.bevel_profile,
            harden_normals=self.harden_normals,
            length_along_u=self.length_along == "U",
            uv_padding=self.uv_padding,
        )

    def execute(self, context):
        settings = context.scene.wible.mesh
        for name in props.MESH_OPTIONS:
            setattr(settings, name, getattr(self, name))

        opts = self.mesh_options()
        wires = _wires(context, types={"CURVE"})
        active = wire_of(context.object)
        new_active = None
        created = []
        for obj in wires:
            # A saved curve is converted as it is; only live curves are
            # settled first (finalize would re-solve a saved one).
            live_curve = not obj.wible.accepted
            if live_curve:
                build.finalize(obj)
            wire_opts = opts
            if obj.wible.use_trim:
                layout, reason = build.trim_layout(obj)
                if layout is not None:
                    if live_curve:
                        build.apply_trim(obj)
                    if obj.data.use_fill_caps and not layout.caps_fit():
                        self.report({"WARNING"}, f"{obj.name}: the slot is too short for the caps, "
                                    "they run into the next slot. Use a longer slot or less padding")
                    wire_opts = dataclasses.replace(opts, trim=layout)
                else:
                    self.report({"WARNING"}, f"{obj.name}: {reason}; using regular UVs")
            mesh = mesh_build.mesh_from_wire(obj, wire_opts)
            new = bpy.data.objects.new(obj.name, mesh)
            _copy_wire_data(obj, new)
            new.wible.accepted = True
            new[MESH_OPTIONS_KEY] = {name: getattr(self, name) for name in props.MESH_OPTIONS}
            if obj == active:
                new_active = new
            _replace_object(obj, new, context.view_layer)
            created.append(new)

        for new in created:
            new.select_set(True, view_layer=context.view_layer)
        if new_active is not None:
            context.view_layer.objects.active = new_active
        self.report({"INFO"}, f"Converted {len(created)} curve(s) to mesh")
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        draw_mesh_options(layout, self)


class WIBLE_OT_edit_wire(bpy.types.Operator):
    bl_idname = "wible.edit_wire"
    bl_label = "Edit Curve"
    bl_description = "Reopen the selected saved curves for editing. Saved meshes turn back into live curves"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_wires(context, accepted=True))

    def execute(self, context):
        active = wire_of(context.object)
        new_active = None
        for obj in _wires(context, accepted=True):
            if obj.type == "CURVE":
                obj.wible.accepted = False
                build.ensure_length(obj)
                build.ensure_handles(obj)
                build.rebuild(obj)
                continue
            if build.STROKE_KEY not in obj:
                self.report({"WARNING"}, f"{obj.name} has no stored points to edit")
                continue
            curve = bpy.data.curves.new(obj.name, "CURVE")
            if obj.data is not None:
                for mat in obj.data.materials:
                    curve.materials.append(mat)
            new = bpy.data.objects.new(obj.name, curve)
            _copy_wire_data(obj, new)
            new.wible.accepted = True  # keep the update callbacks quiet until placed
            if obj == active:
                new_active = new
            _replace_object(obj, new, context.view_layer)
            new.wible.accepted = False
            build.ensure_length(new)
            build.ensure_handles(new)
            build.rebuild(new)
            new.select_set(True, view_layer=context.view_layer)
        if new_active is not None:
            context.view_layer.objects.active = new_active
        return {"FINISHED"}


class WIBLE_OT_capture_trim(bpy.types.Operator):
    bl_idname = "wible.capture_trim"
    bl_label = "Capture Selected Faces"
    bl_description = (
        "Store the UV rectangle of each selected face on the Trim Plane as a slot. "
        "Select the faces in Edit Mode first; slots are numbered top to bottom"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.scene.wible.trim_object
        return obj is not None and obj.type == "MESH"

    def execute(self, context):
        import bmesh
        from . import trim

        settings = context.scene.wible
        obj = settings.trim_object
        face_uvs = []
        if obj.mode == "EDIT":
            bm = bmesh.from_edit_mesh(obj.data)
            uv = bm.loops.layers.uv.active
            if uv is None:
                self.report({"ERROR"}, f"{obj.name} has no UV map")
                return {"CANCELLED"}
            for face in bm.faces:
                if face.select:
                    face_uvs.append([tuple(loop[uv].uv) for loop in face.loops])
        else:
            mesh = obj.data
            uv = mesh.uv_layers.active
            if uv is None:
                self.report({"ERROR"}, f"{obj.name} has no UV map")
                return {"CANCELLED"}
            for poly in mesh.polygons:
                if poly.select:
                    face_uvs.append([tuple(uv.data[i].uv) for i in poly.loop_indices])

        rects = trim.faces_to_slots(face_uvs)
        if not rects:
            self.report({"WARNING"}, f"No faces selected on {obj.name}. Select them in Edit Mode first")
            return {"CANCELLED"}

        settings.trim_slots.clear()
        for rect in rects:
            slot = settings.trim_slots.add()
            slot.u0, slot.v0, slot.u1, slot.v1 = rect
        for wire in context.scene.objects:
            if wire.type == "CURVE" and wire.wible.is_wire and not wire.wible.accepted and wire.wible.use_trim:
                build.apply_trim(wire)
        self.report({"INFO"}, f"Captured {len(rects)} slot(s)")
        return {"FINISHED"}


UV_OPTIONS = ("length_along", "uv_padding")


def _reset_group(settings, names, keep=()):
    for name in names:
        if name in keep:
            continue
        prop = settings.bl_rna.properties[name]
        if getattr(prop, "is_array", False):
            value = tuple(prop.default_array)
        elif prop.type == "POINTER":
            value = None
        else:
            value = prop.default
        props.set_silent(settings, name, value)


class WIBLE_OT_reset_settings(bpy.types.Operator):
    bl_idname = "wible.reset_settings"
    bl_label = "Reset to Defaults"
    bl_description = (
        "Reset the selected curves' settings to their light defaults (the Drape Over "
        "collection is kept). While drawing, resets the settings for new curves"
    )
    bl_options = {"REGISTER", "UNDO"}

    target: bpy.props.EnumProperty(
        items=(
            ("WIRE", "Curve", "Curve settings, including shading and cap edges"),
            ("MESH", "UVs", "UV settings"),
        ),
        default="WIRE",
        options={"HIDDEN"},
    )

    def execute(self, context):
        from . import draw

        mesh = context.scene.wible.mesh
        if self.target == "MESH":
            _reset_group(mesh, UV_OPTIONS)
            return {"FINISHED"}

        # Shading and cap edges are shown with the curve's geometry.
        _reset_group(mesh, [n for n in props.MESH_OPTIONS if n not in UV_OPTIONS])

        if draw.session_active(context):
            _reset_group(context.scene.wible.defaults, props.WIRE_SETTINGS, keep=("drape_collection",))
            return {"FINISHED"}

        wires = _wires(context, accepted=False, types={"CURVE"})
        if not wires:
            # With the trim plane selected the panel shows the last curve.
            from . import ui
            shown = ui.shown_wire(context)
            if shown is not None and shown.type == "CURVE" and not shown.wible.accepted:
                wires = [shown]
        if not wires:
            _reset_group(context.scene.wible.defaults, props.WIRE_SETTINGS, keep=("drape_collection",))
            self.report({"INFO"}, "Reset the settings for new curves")
            return {"FINISHED"}
        for obj in wires:
            _reset_group(obj.wible, props.WIRE_SETTINGS, keep=("drape_collection", "length"))
            # Length follows from the default tension at the wire's current ends.
            props.set_silent(obj.wible, "length", build.length_for_tension(obj))
            build.rebuild(obj, animate=True)
        self.report({"INFO"}, f"Reset {len(wires)} curve(s)")
        return {"FINISHED"}


classes = (
    WIBLE_OT_accept_curve,
    WIBLE_OT_accept_mesh,
    WIBLE_OT_edit_wire,
    WIBLE_OT_reset_settings,
    WIBLE_OT_capture_trim,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
