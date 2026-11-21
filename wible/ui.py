# SPDX-License-Identifier: GPL-3.0-or-later

"""Wible UI. One draw function feeds both the sidebar panel and the popup
opened with the shortcut, so they always look the same."""

import bpy

from . import build, draw, history, icons, operators, trim

POPUP_PANEL = "VIEW3D_PT_wible_popup"
POPUP_WIDTH = 15  # UI units; wide enough for the two Save buttons side by side
TITLE_GAP = 0.6  # separator factor around section title icons
SECTION_END_GAP = 1.0  # separator factor under each section's last option
SIDE_GAP = 0.6  # separator factor between the UI and the sidebar/popup edges
HISTORY_BAR_HEIGHT = 0.4  # scale of the history step bar, thin like ZBrush's


def _section(layout, title, icon, toggle=None, reset=None):
    """An always-open section: a box with a colored icon and title, plus an
    optional captioned on/off toggle (settings, prop, caption) and a reset
    button in its title row. Returns the box to draw the section's fields into."""
    box = layout.box()
    # Same gap above the title as to the left of its icon.
    box.separator(factor=TITLE_GAP)
    row = box.row(align=True)
    row.use_property_split = False
    row.separator(factor=TITLE_GAP)  # keep the icon off the box edge
    row.label(text=title, icon_value=icons.get(icon))
    if toggle is not None:
        settings, prop, caption = toggle
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.prop(settings, prop, text=caption)
    if reset is not None:
        _reset_button(row, target=reset)
    row.separator(factor=TITLE_GAP)  # same inset on the right
    # Fields go in a column with the same inset on the right, so they don't
    # run into the box edge.
    inner = box.row()
    body = inner.column()
    inner.separator(factor=TITLE_GAP)
    return body


def _end_section(body):
    """Space under a section's last option, like the space above the Draw row."""
    body.separator(factor=SECTION_END_GAP)


def _reset_button(layout, target="WIRE", emboss=False):
    op = layout.operator("wible.reset_settings", text="", icon="LOOP_BACK", emboss=emboss)
    op.target = target


def _draw_header(layout):
    row = layout.row(align=True)
    row.label(text="Wible", icon_value=icons.get("wible"))


def _draw_draw_block(layout, scene_settings, active):
    col = layout.column()
    col.prop(scene_settings, "placement")
    if scene_settings.placement == "SNAP_ENDS":
        col.prop(scene_settings, "only_selected")
    col.separator(factor=1.0)  # breathing room above the main action
    row = col.row(align=True)
    row.scale_y = 1.4
    # The main action gets Blender's "default action" highlight (the theme's
    # accent, blue by default), like the confirm button in Blender's dialogs.
    main = row.row(align=True)
    main.active_default = True
    if active:
        main.operator("wible.draw_finish", icon="CHECKMARK")
        row.operator("wible.draw_cancel", text="", icon="X")
    else:
        main.operator("wible.draw_start", text="Draw")
    # Reset sits right after the main action.
    _reset_button(row, emboss=True)


def _draw_wire_sections(layout, settings, is_wire=True, mesh_settings=None):
    # Shape
    body = _section(layout, "Shape", "shape", toggle=(settings, "gravity", "Gravity"))
    if settings.gravity:
        col = body.column(align=True)
        col.prop(settings, "tension")
        col.prop(settings, "weight")
        if is_wire:
            col.prop(settings, "length")
        col.prop(settings, "point_count")
        body.prop(settings, "drape")
        if settings.drape:
            col = body.column(align=True)
            col.prop(settings, "drape_collection")
            col.prop(settings, "drape_iterations")
            col.prop(settings, "drape_gap")
    else:
        col = body.column(align=True)
        col.prop(settings, "point_count")
        # Smoothing only shapes curves made with Wible's first version.
        owner = settings.id_data
        if is_wire and isinstance(owner, bpy.types.Object) and build.SHAPE_KEY in owner:
            col.prop(settings, "smoothing")
    _end_section(body)

    # Physics
    if settings.gravity:
        body = _section(layout, "Physics", "physics", toggle=(settings, "physics", "Simulate"))
        col = body.column(align=True)
        col.active = settings.physics
        col.prop(settings, "stiffness", slider=True)
        col.prop(settings, "damping", slider=True)
        _end_section(body)

    # Geometry
    body = _section(layout, "Geometry", "geometry")
    col = body.column(align=True)
    depth = col.row(align=True)
    # With a trim slot the slot sets the thickness.
    depth.active = not (is_wire and settings.use_trim)
    depth.prop(settings, "depth")
    col.prop(settings, "resolution")
    col.prop(settings, "bevel_resolution", text="Radial Resolution")
    if mesh_settings is not None:
        body.prop(mesh_settings, "shading")
    body.prop(settings, "fill_caps")
    if settings.fill_caps and mesh_settings is not None:
        operators.draw_cap_options(body, mesh_settings)
    _end_section(body)


def _draw_trim(layout, scene_settings, wire):
    """Trim sheet / hotspot controls for one curve (inside UVs)."""
    layout.prop(wire.wible, "use_trim")
    if not wire.wible.use_trim:
        return
    col = layout.column()
    col.prop(scene_settings, "trim_object")
    sub = col.column(align=True)
    sub.prop(scene_settings, "texture_width")
    sub.prop(scene_settings, "texture_height")
    col.prop(scene_settings, "texel_density")
    col.prop(scene_settings, "trim_padding")
    row = col.row(align=True)
    row.operator("wible.capture_trim", icon="RESTRICT_SELECT_OFF")
    col.prop(wire.wible, "trim_slot")

    layout_, reason = build.trim_layout(wire)
    info = col.row()
    info.alignment = "RIGHT"
    if layout_ is None:
        info.label(text=reason, icon="INFO")
    else:
        # The slots of the scene the curve lives in, like trim_layout.
        owner = build.scene_of(wire).wible
        slots = owner.trim_slots
        w_px, h_px = trim.slot_size_px(slots[wire.wible.trim_slot - 1].rect,
                                       owner.texture_width, owner.texture_height)
        info.label(text=f"{round(w_px)} x {round(h_px)} px, {len(slots)} slots")
        info = col.row()
        info.alignment = "RIGHT"
        info.label(text=f"Thickness {layout_.radius * 2000:.1f} mm")
        if wire.data.use_fill_caps and not layout_.caps_fit():
            info = col.row()
            info.alignment = "RIGHT"
            info.label(text="Slot too short for the caps", icon="ERROR")


def _draw_mesh_section(layout, scene_settings, caps=False, shading=False, wire=None):
    body = _section(layout, "UVs", "mesh", reset="MESH")
    trim_on = wire is not None and wire.wible.use_trim
    operators.draw_mesh_options(body, scene_settings.mesh, caps=caps, shading=shading, trim_on=trim_on)
    if wire is not None:
        _draw_trim(body, scene_settings, wire)
    _end_section(body)


def _draw_accept(layout, curve):
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator("wible.accept_mesh", text="Save as Mesh", icon="MESH_DATA")
    if curve:
        row.operator("wible.accept_curve", text="Save as Curve", icon="CURVE_DATA")


def _trim_plane_active(context):
    trim = context.scene.wible.trim_object
    return trim is not None and context.object == trim


def panel_available(context):
    """Object Mode, any mode while drawing (so Finish stays reachable), and
    the trim plane's Edit Mode (to pick faces)."""
    if context.mode == "OBJECT" or draw.session_active(context):
        return True
    return context.mode == "EDIT_MESH" and _trim_plane_active(context)


def shown_wire(context):
    """The curve whose settings the panel shows: the active one, or the
    last active one while the trim plane is selected, so the panel keeps its
    size while you pick slot faces."""
    obj = operators.wire_of(context.object)
    if obj is not None and obj.wible.is_wire:
        return obj
    if _trim_plane_active(context):
        last = context.scene.wible.last_wire
        if last is not None and last.wible.is_wire and last.name in context.scene.objects:
            return last
    return None


def draw_wible(layout, context, header=True):
    """The whole Wible UI, every section open. The sidebar draws its title
    in the panel's own header instead (header=False)."""
    # Inset everything from the sidebar or popup edges, equally on both sides.
    outer = layout.row()
    outer.separator(factor=SIDE_GAP)
    layout = outer.column()
    outer.separator(factor=SIDE_GAP)
    layout.use_property_split = True
    layout.use_property_decorate = False
    scene_settings = context.scene.wible
    drawing = draw.session_active(context)

    if header:
        _draw_header(layout)
    _draw_draw_block(layout, scene_settings, drawing)

    if drawing:
        layout.separator()
        layout.label(text="New Curve Settings")
        _draw_wire_sections(layout, scene_settings.defaults, is_wire=False,
                            mesh_settings=scene_settings.mesh)
        return

    obj = shown_wire(context)
    if obj is None:
        return

    layout.separator()
    if obj.wible.accepted:
        row = layout.row()
        icon = "OUTLINER_OB_MESH" if obj.type == "MESH" else "OUTLINER_OB_CURVE"
        row.label(text=f"{obj.name} (saved)", icon=icon)
        layout.operator("wible.edit_wire", icon_value=icons.get("draw"))
        if obj.type == "CURVE":
            # No Geometry section here, so the cap controls stay with the mesh options.
            _draw_mesh_section(layout, scene_settings, caps=obj.data.use_fill_caps, shading=True, wire=obj)
            _draw_accept(layout, curve=False)
        return

    _draw_history_bar(layout, obj)
    _draw_wire_sections(layout, obj.wible, mesh_settings=scene_settings.mesh)
    _draw_mesh_section(layout, scene_settings, wire=obj)
    _draw_accept(layout, curve=True)


def _draw_history_bar(layout, obj):
    """Thin strip with one cell per history step, like ZBrush's undo history.
    The current step is highlighted, steps after it are dimmed; hovering a
    cell shows what changed and clicking it goes back (or forward) to it."""
    labels, current = history.steps(obj)
    if len(labels) < 2:
        return
    row = layout.row(align=True)
    row.scale_y = HISTORY_BAR_HEIGHT
    for i in range(len(labels)):
        cell = row.row(align=True)
        cell.active = i <= current
        op = cell.operator("wible.history_jump", text="", depress=(i == current))
        op.index = i
    layout.separator(factor=0.5)


class VIEW3D_PT_wible(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Wible"
    bl_label = "Wible"
    # A real panel header (not HIDE_HEADER) so Blender paints the panel
    # background; with region overlap a headerless panel is see-through.

    @classmethod
    def poll(cls, context):
        return panel_available(context)

    def draw_header(self, context):
        self.layout.label(text="", icon_value=icons.get("wible"))

    def draw(self, context):
        draw_wible(self.layout, context, header=False)


class VIEW3D_PT_wible_popup(bpy.types.Panel):
    """Same UI as the sidebar, opened at the mouse by the shortcut."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_label = "Wible"
    bl_ui_units_x = POPUP_WIDTH

    def draw(self, context):
        if not panel_available(context):
            self.layout.label(text="Wible works in Object Mode", icon="INFO")
            return
        draw_wible(self.layout, context)


classes = (VIEW3D_PT_wible, VIEW3D_PT_wible_popup)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
