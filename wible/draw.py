# SPDX-License-Identifier: GPL-3.0-or-later

"""Point placement tool: click to place a curve's points in the viewport.

Draw starts a modal tool. Each click adds a fixed point (anchor): on a
surface where the pointer hits, over empty space on a plane facing the view
through the previous point. A reticle shows where the next point will land,
and the curve hangs live through the points placed so far.

Enter or right-click finishes the current curve and starts the next;
right-click over a curve adds a point to it; Esc (or Finish in the panel)
creates the last one and leaves the tool; Backspace removes the last point.
Navigation passes through.
"""

import math

import bpy
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import build, solver

RETICLE_COLOR = (0.50, 0.83, 1.00, 1.0)  # sky, from the add-on's palette
INSERT_COLOR = (1.00, 0.76, 0.42, 1.0)  # amber: right-click adds a point here
CABLE_PICK_PX = 10.0
PREVIEW_COLOR = (0.92, 0.92, 0.92, 0.9)
RETICLE_PX = 14.0
PREVIEW_SAMPLES = 12
RAY_STEPS = 16

# Regions that sit over the viewport with Region Overlap on: clicks there
# belong to their buttons, not to the tool.
_OVERLAY_REGIONS = {"UI", "TOOLS", "HEADER", "TOOL_HEADER", "ASSET_SHELF", "ASSET_SHELF_HEADER"}
# Keys that never touch the scene, let through so panels and saving still work.
_SAFE_KEYS = {"N", "T", "F1", "F2", "F3", "F4", "F11", "F12"}
# Alt combinations used for navigation (emulate 3 button mouse, Blaya) and the
# Shift+Alt+X popup. Other Alt keys (Alt+D, Alt+G, Alt+P...) would edit objects.
_ALT_TYPES = {"LEFTMOUSE", "MIDDLEMOUSE", "RIGHTMOUSE", "MOUSEMOVE", "X",
              "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM"}

# Events the tool lets through so the view can still be navigated.
_NAVIGATION = {
    "MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE",
    "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE", "MOUSESMARTZOOM", "NDOF_MOTION",
    "HOME", "ACCENT_GRAVE",
}

_tool = None


def session_active(context):
    return _tool is not None and not _tool.done


def session_curves():
    """Live curves made in the current Draw session, newest first."""
    if _tool is None or _tool.done:
        return []
    out = []
    for name in reversed(_tool.created):
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.type == "CURVE" and obj.wible.is_wire and not obj.wible.accepted:
            out.append(obj)
    return out


class _Point:
    __slots__ = ("position", "normal", "kind", "strength", "surface")

    def __init__(self, position, normal, kind, strength=1.0, surface=False):
        self.position = position
        self.normal = normal
        self.kind = kind
        self.strength = strength
        self.surface = surface


class _Tool:
    def __init__(self, context):
        self.area = context.area
        self.screen = context.window.screen
        # The viewport region the pointer is over; in Quad View it follows
        # the pointer between the four views.
        region = context.region
        if region is None or region.type != "WINDOW":
            region = next(r for r in context.area.regions if r.type == "WINDOW")
        self.region = region
        self.changed = False  # a point was added to an existing curve
        self.points = []
        self.hover = None  # _Point under the pointer, or None outside the view
        # Live curve under the pointer: (name, world position, samples,
        # float sample index), where right-click adds a point.
        self.cable = None
        self.done = False
        self.created = []  # names of curves made in this session
        settings = context.scene.wible
        self.targets = set()
        if settings.only_selected:
            self.targets = {
                o.name for o in context.selected_objects
                if not o.wible.is_wire and o.get(build.HANDLE_KEY) is None
            }
        self.handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")

    # Picking

    def mouse(self, event):
        """Pointer position in the viewport region under it, and whether it
        is over the viewport itself (not a panel or header drawn over it)."""
        mx, my = event.mouse_x, event.mouse_y

        def over(r):
            return r.width > 1 and r.height > 1 and r.x <= mx < r.x + r.width and r.y <= my < r.y + r.height

        regions = self.area.regions
        for r in regions:
            if r.type == "WINDOW" and over(r):
                self.region = r
                break
        else:
            r = self.region
            return (mx - r.x, my - r.y), False
        inside = not any(o.type in _OVERLAY_REGIONS and over(o) for o in regions)
        return (mx - self.region.x, my - self.region.y), inside

    def view(self):
        """The region_3d of the view the pointer is over."""
        data = self.region.data
        return data if data is not None else self.area.spaces.active.region_3d

    def pick(self, context, coord):
        """The point the pointer would place, as a _Point (kind left None)."""
        region = self.region
        rv3d = self.view()
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord).normalized()
        scene = context.scene

        if scene.wible.placement == "SNAP_ENDS":
            hit = self._ray(context, origin, direction)
            if hit is not None:
                location, normal = hit
                if normal.dot(direction) > 0.0:
                    normal = -normal
                return _Point(location, normal, None, surface=True)

        # Over empty space (or View Plane placement): a plane facing the view
        # through the previous point, or the 3D cursor for the first one.
        base = self.points[-1].position if self.points else scene.cursor.location.copy()
        view_dir = (rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))).normalized()
        denom = direction.dot(view_dir)
        if abs(denom) < 1e-9:
            return None
        t = (base - origin).dot(view_dir) / denom
        return _Point(origin + direction * t, -view_dir, None)

    def _ray(self, context, origin, direction):
        scene = context.scene
        depsgraph = context.evaluated_depsgraph_get()
        wible_coll = bpy.data.collections.get(build.COLLECTION_NAME)
        skip = {o.name for o in wible_coll.all_objects} if wible_coll else set()
        for _ in range(RAY_STEPS):
            hit, location, normal, _index, obj, _matrix = scene.ray_cast(depsgraph, origin, direction)
            if not hit:
                return None
            if obj is not None and obj.name not in skip and (not self.targets or obj.name in self.targets):
                return location, normal.normalized()
            origin = location + direction * 1e-4
        return None

    def kind_for(self, point):
        return solver.ANCHOR

    def find_cable(self, context, coord):
        """The live curve drawn closest to the pointer on screen, within
        CABLE_PICK_PX, as (name, world position, samples, sample index)."""
        from . import live
        region = self.region
        rv3d = self.view()
        mouse = Vector(coord)
        best = None
        for obj in context.scene.objects:
            s = obj.wible
            if obj.type != "CURVE" or not s.is_wire or s.accepted or not obj.visible_get():
                continue
            samples = live._last_drawn.get(obj.name)
            if not samples:
                continue
            screen = [view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(p)) for p in samples]
            for i in range(len(samples) - 1):
                a, b = screen[i], screen[i + 1]
                if a is None or b is None:
                    continue
                ab = b - a
                t = 0.0 if ab.length_squared < 1e-9 else min(max((mouse - a).dot(ab) / ab.length_squared, 0.0), 1.0)
                distance = (a + ab * t - mouse).length
                if distance <= CABLE_PICK_PX and (best is None or distance < best[0]):
                    world = Vector(samples[i]).lerp(Vector(samples[i + 1]), t)
                    best = (distance, obj.name, world, samples, i + t)
        return best[1:] if best else None

    def insert(self, context):
        """Right-click: add a point to the curve under the pointer."""
        if self.cable is None:
            return False
        name, position, samples, index = self.cable
        obj = bpy.data.objects.get(name)
        if obj is None:
            return False
        build.insert_point(obj, position, samples, index)
        self.changed = True
        self.cable = None
        return True

    # Editing

    def add(self, context, event):
        coord, inside = self.mouse(event)
        if not inside:
            return False
        point = self.pick(context, coord)
        if point is None:
            return False
        point.kind = self.kind_for(point)
        self.points.append(point)
        return True

    def commit(self, context):
        """Create a curve from the placed points, if there are two."""
        points, self.points = self.points, []
        if len(points) < 2:
            return None
        wire = build.create_wire(context, [p.position for p in points], context.scene.wible.defaults)
        self.created.append(wire.name)
        return wire

    def finish(self, context, keep=True):
        if self.done:
            return
        if keep:
            self.commit(context)
        self.done = True
        try:
            bpy.types.SpaceView3D.draw_handler_remove(self.handler, "WINDOW")
        except (ValueError, ReferenceError):
            pass
        try:
            self.area.header_text_set(None)
            self.area.tag_redraw()
        except ReferenceError:
            pass
        if keep and self.created:
            wires = [bpy.data.objects.get(name) for name in self.created]
            wires = [w for w in wires if w is not None and w.name in context.scene.objects]
            if wires:
                for obj in context.selected_objects:
                    obj.select_set(False)
                for wire in wires:
                    wire.select_set(True)
                context.view_layer.objects.active = wires[-1]

    def header(self):
        count = len(self.points)
        insert = (f"Right-click: add a point to {self.cable[0]}  ·  " if self.cable
                  else "Right-click a curve: add a point  ·  ")
        self.area.header_text_set(
            f"Wible: click to place points ({count} placed)  ·  {insert}"
            "Enter / Right-click elsewhere: next curve  ·  Backspace / Ctrl+Z: remove last  ·  Esc: finish"
        )


# Drawing

def _view_scale(region, rv3d, point):
    """World size of RETICLE_PX pixels at `point`."""
    center = view3d_utils.location_3d_to_region_2d(region, rv3d, point)
    if center is None:
        return 0.05
    edge = view3d_utils.region_2d_to_location_3d(region, rv3d, center + Vector((RETICLE_PX, 0.0)), point)
    return max((edge - point).length, 1e-5)


def _basis(normal):
    u = normal.orthogonal().normalized()
    return u, normal.cross(u).normalized()


def _ring(center, normal, radius, segments=32):
    u, v = _basis(normal)
    coords = []
    for i in range(segments):
        a0 = 2.0 * math.pi * i / segments
        a1 = 2.0 * math.pi * (i + 1) / segments
        coords.append(center + (u * math.cos(a0) + v * math.sin(a0)) * radius)
        coords.append(center + (u * math.cos(a1) + v * math.sin(a1)) * radius)
    return coords


def _marker(center, normal, radius):
    return _ring(center, normal, radius, segments=24)


def _reticle(point, size):
    """Ring with four ticks and a center dot on the surface, plus a normal line."""
    p, n = point.position, point.normal
    u, v = _basis(n)
    coords = _marker(p, n, size)
    for axis in (u, v, -u, -v):
        coords += [p + axis * size * 0.7, p + axis * size * 1.35]
    coords += _ring(p, n, size * 0.12, segments=8)
    coords += [p, p + n * size * 1.6]
    return coords


def _lines(coords, color, width):
    if len(coords) < 2:
        return
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINES", {"pos": [tuple(c) for c in coords]})
    shader.bind()
    shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("color", color)
    batch.draw(shader)


def _preview_path(context, points):
    if len(points) < 2:
        return []
    positions = [tuple(p.position) for p in points]
    kinds = [solver.ANCHOR] * len(points)
    defaults = context.scene.wible.defaults
    if not defaults.gravity:
        return build._smooth_through(positions, PREVIEW_SAMPLES)
    a = defaults.tension / max(defaults.weight, 1e-6)
    lengths = [solver.run_length(positions, [i0, i1], a) for i0, i1 in solver.runs(kinds)]
    return solver.solve_path(positions, kinds, [1.0] * len(points), lengths, PREVIEW_SAMPLES)


def _draw():
    tool = _tool
    if tool is None or tool.done:
        return
    context = bpy.context
    if context.area != tool.area:
        return
    # Drawn in every view of a Quad View, each with its own matrices.
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    try:
        # Live preview of the curve through the placed points and the pointer.
        points = list(tool.points)
        if tool.hover is not None and tool.hover.kind is not None:
            ghost = _Point(tool.hover.position, tool.hover.normal, solver.ANCHOR)
            points = points + [ghost]
        path = _preview_path(context, points)
        if len(path) >= 2:
            coords = []
            for a, b in zip(path, path[1:]):
                coords += [Vector(a), Vector(b)]
            _lines(coords, PREVIEW_COLOR, 2.0)

        for point in tool.points:
            size = _view_scale(region, rv3d, point.position) * 0.6
            _lines(_marker(point.position, point.normal, size), RETICLE_COLOR, 2.0)

        if tool.cable is not None:
            # Over a curve: show where right-click would add a point.
            position = tool.cable[1]
            facing = -(rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))).normalized()
            size = _view_scale(region, rv3d, position)
            _lines(_reticle(_Point(position, facing, solver.ANCHOR), size * 0.8), INSERT_COLOR, 2.5)
        elif tool.hover is not None and tool.hover.kind is not None:
            size = _view_scale(region, rv3d, tool.hover.position)
            _lines(_reticle(tool.hover, size), RETICLE_COLOR, 2.0)
    finally:
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.blend_set("NONE")


# Operators

class WIBLE_OT_draw_start(bpy.types.Operator):
    bl_idname = "wible.draw_start"
    bl_label = "Draw"
    bl_description = (
        "Place a curve's points in the viewport: click on surfaces or in the air. "
        "The curve hangs between them. Right-click an existing curve to add a point to it. "
        "Tip: select the objects to attach to first and keep Only Selected on, which keeps "
        "placing fast in big scenes"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and not session_active(context)

    def invoke(self, context, event):
        global _tool
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        _tool = _Tool(context)
        self._update_hover(context, event)
        _tool.header()
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        return {"CANCELLED"}

    def _update_hover(self, context, event):
        coord, inside = _tool.mouse(event)
        if not inside:
            _tool.hover = None
            _tool.cable = None
            return
        had_cable = _tool.cable is not None
        _tool.cable = _tool.find_cable(context, coord)
        if had_cable != (_tool.cable is not None):
            _tool.header()
        point = _tool.pick(context, coord)
        if point is not None:
            point.kind = _tool.kind_for(point)
        _tool.hover = point

    def _end(self, context, keep):
        global _tool
        tool = _tool
        changed = False
        if tool is not None:
            try:
                tool.finish(context, keep=keep)
            except Exception:
                tool.done = True
            changed = bool(tool.created) or tool.changed
        _tool = None
        # FINISHED records one undo step for the whole session, only when
        # something was made or changed.
        return {"FINISHED"} if changed else {"CANCELLED"}

    def modal(self, context, event):
        tool = _tool
        if tool is None or tool.done:
            # Finished or cancelled from the panel.
            return self._end(context, keep=True)
        try:
            if tool.area.type != "VIEW_3D":
                return self._end(context, keep=True)
            tool.area.tag_redraw()
        except ReferenceError:
            return self._end(context, keep=False)
        if context.window.screen != tool.screen:
            # Another workspace is showing: leave its events alone.
            return {"PASS_THROUGH"}
        try:
            return self._modal(context, event, tool)
        except ReferenceError:
            return self._end(context, keep=False)
        except Exception:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, "Wible: the Draw tool stopped after an error (see the console)")
            return self._end(context, keep=False)

    def _modal(self, context, event, tool):
        _coord, inside = tool.mouse(event)
        undo_key = (
            event.type == "Z" and (event.ctrl or event.oskey)
            or event.type == "Y" and (event.ctrl or event.oskey)
        )
        if undo_key:
            # Blender's undo would roll back curves this tool just made
            # while it still works with them. Ctrl+Z removes the last point.
            if event.value == "PRESS" and event.type == "Z" and not event.shift and tool.points:
                tool.points.pop()
                self._update_hover(context, event)
                tool.header()
            return {"RUNNING_MODAL"}
        navigation = (
            (event.type == "Z" and not (event.ctrl or event.shift or event.oskey))
            or
            event.type in _NAVIGATION
            or event.type.startswith("NDOF")
            or (event.type.startswith("NUMPAD_") and event.type != "NUMPAD_ENTER")
            or (event.alt and event.type in _ALT_TYPES)  # Alt navigation, popup shortcut
        )
        if navigation:
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            self._update_hover(context, event)
            return {"PASS_THROUGH"}

        if event.value == "PRESS" and not (event.ctrl or event.alt or event.oskey):
            # Keyboard commands work wherever the pointer is.
            if event.type == "ESC":
                return self._end(context, keep=True)
            if event.type in {"RET", "NUMPAD_ENTER"}:
                tool.commit(context)
                tool.header()
                return {"RUNNING_MODAL"}
            if event.type in {"BACK_SPACE", "DEL"}:
                if tool.points:
                    tool.points.pop()
                    self._update_hover(context, event)
                    tool.header()
                return {"RUNNING_MODAL"}

        if event.type in _SAFE_KEYS or (event.type == "S" and (event.ctrl or event.oskey)):
            return {"PASS_THROUGH"}

        if not inside:
            # Panel buttons (Finish, Cancel, settings) and other regions.
            return {"PASS_THROUGH"}

        if event.value != "PRESS":
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if tool.add(context, event):
                self._update_hover(context, event)
                tool.header()
            return {"RUNNING_MODAL"}
        if event.type == "RIGHTMOUSE" and tool.cable is not None:
            # Right-click over a curve: add a point to it there.
            if tool.insert(context):
                self._update_hover(context, event)
                tool.header()
            return {"RUNNING_MODAL"}
        if event.type == "RIGHTMOUSE":
            tool.commit(context)
            tool.header()
        return {"RUNNING_MODAL"}


class WIBLE_OT_draw_finish(bpy.types.Operator):
    bl_idname = "wible.draw_finish"
    bl_label = "Finish Drawing"
    bl_description = "Create the curve being placed and leave the tool"
    # No undo step of its own: the Draw operator records one when it ends.
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return session_active(context)

    def execute(self, context):
        if _tool is not None:
            _tool.finish(context, keep=True)
            if _tool.created:
                self.report({"INFO"}, f"Created {len(_tool.created)} curve(s)")
        return {"FINISHED"}


class WIBLE_OT_draw_cancel(bpy.types.Operator):
    bl_idname = "wible.draw_cancel"
    bl_label = "Cancel Drawing"
    bl_description = "Discard the points being placed and leave the tool"
    # No undo step of its own: the Draw operator records one when it ends.
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return session_active(context)

    def execute(self, context):
        if _tool is not None:
            _tool.finish(context, keep=False)
        return {"FINISHED"}


classes = (WIBLE_OT_draw_start, WIBLE_OT_draw_finish, WIBLE_OT_draw_cancel)


@bpy.app.handlers.persistent
def _on_undo(*_args):
    """An undo or file load while placing points: the objects the tool made
    may be gone, so stop without touching them (the modal then ends on its
    next event)."""
    tool = _tool
    if tool is not None and not tool.done:
        tool.created = []
        tool.points = []
        try:
            tool.finish(bpy.context, keep=False)
        except Exception:
            tool.done = True


_UNDO_HANDLERS = ("undo_pre", "redo_pre", "load_pre")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    for name in _UNDO_HANDLERS:
        getattr(bpy.app.handlers, name).append(_on_undo)


def unregister():
    global _tool
    for name in _UNDO_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if _on_undo in handlers:
            handlers.remove(_on_undo)
    if _tool is not None:
        try:
            _tool.finish(bpy.context, keep=False)
        except Exception:
            pass
        _tool = None
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
