# SPDX-License-Identifier: GPL-3.0-or-later

"""Per curve edit history, stored on the object (so it is saved with the
file and follows Blender's own undo).

A timer records a step once a live curve has stopped changing: one step
per slider drag or move, not one per update. Jumping to a step restores its
settings, shape, ends and transform.
"""

import bpy
from mathutils import Matrix, Vector

from . import build, history_stack, live, props

HISTORY_KEY = "wible_history"
INDEX_KEY = "wible_history_index"
SIG_KEY = "wible_history_sig"
CHECK_INTERVAL = 0.4

# Signature seen at the previous check, per object name: a change is recorded
# once it has held still for one check.
_pending = {}


def _setting_names():
    rna = props.WibleWireSettings.bl_rna.properties
    return {name: rna[name].name for name in props.WIRE_SETTINGS if name in rna}


def capture(obj):
    s = obj.wible
    settings = {}
    for name in props.WIRE_SETTINGS:
        value = getattr(s, name)
        if name == "drape_collection":
            value = value.name if value is not None else ""
        settings[name] = value
    positions = build.point_positions(obj)
    return {
        "settings": settings,
        "ends": [tuple(p) for p in positions],
        "kinds": list(build.point_kinds(obj)),
        "strengths": list(build.point_strengths(obj)),
        "runs": list(obj.get(build.RUNS_KEY, [])),
        "matrix": [c for row in obj.matrix_world for c in row],
        "stroke": list(obj.get(build.STROKE_KEY, [])),
    }


def load(obj):
    return history_stack.History.loads(obj.get(HISTORY_KEY, ""), obj.get(INDEX_KEY, -1))


def _save(obj, history):
    obj[HISTORY_KEY] = history.dumps()
    obj[INDEX_KEY] = history.index
    current = history.current
    obj[SIG_KEY] = current["sig"] if current else ""


def steps(obj):
    """(labels, current index) for the UI."""
    history = load(obj)
    return [step["label"] for step in history.steps], history.index


def record(obj):
    """Record the curve's current state as a step. Returns True if it changed."""
    history = load(obj)
    state = capture(obj)
    label = None
    if history.current is not None:
        label = history_stack.describe(history.current["state"], state, _setting_names())
    if history.push(state, label):
        _save(obj, history)
        return True
    return False


def restore(obj, index):
    """Make step `index` current and put the curve back in that state."""
    history = load(obj)
    state = history.jump(index)

    s = obj.wible
    for name, value in state["settings"].items():
        if name not in props.WIRE_SETTINGS:
            continue
        if name == "drape_collection":
            value = bpy.data.collections.get(value) if value else None
        try:
            props.set_silent(s, name, value)
        except (TypeError, ValueError):
            pass

    flat = state["matrix"]
    matrix = Matrix([flat[0:4], flat[4:8], flat[8:12], flat[12:16]])
    obj.matrix_world = matrix
    # Points come from where the handles were (world space) when recorded;
    # the stored stroke is the fallback for older steps.
    positions = state.get("ends") or []
    if len(positions) >= 2:
        inv = matrix.inverted_safe()
        obj[build.STROKE_KEY] = [c for p in positions for c in (inv @ Vector(p))]
    else:
        obj[build.STROKE_KEY] = list(state["stroke"])
    if state.get("kinds"):
        obj[build.KINDS_KEY] = list(state["kinds"])
    if state.get("strengths"):
        obj[build.STRENGTH_KEY] = list(state["strengths"])
    if state.get("runs"):
        obj[build.RUNS_KEY] = list(state["runs"])
    # The step may have more or fewer points than now: recreate the handles.
    build.remove_handles(obj)
    build.ensure_handles(obj)

    _save(obj, history)
    _pending.pop(obj.name, None)
    live.forget(obj)
    build.rebuild(obj)
    live.settle_at_rest(obj)
    # Rebuilding can round Length or Tension a little; that's the restored
    # step, not a new change, so don't record it (it would drop redo steps).
    obj[SIG_KEY] = history_stack.signature(capture(obj))


def _live_wires():
    seen = set()
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        scene = window.scene
        if scene is None or scene.name in seen:
            continue
        seen.add(scene.name)
        for obj in scene.objects:
            s = obj.wible
            if obj.type == "CURVE" and s.is_wire and not s.accepted:
                yield obj


def _remember_active_wire():
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        scene, view_layer = window.scene, window.view_layer
        if scene is None or view_layer is None:
            continue
        active = view_layer.objects.active
        if active is not None and active.get(build.HANDLE_KEY) is not None and active.parent is not None:
            active = active.parent
        if active is None or not active.wible.is_wire or active.wible.accepted:
            continue
        if scene.wible.last_wire != active:
            scene.wible.last_wire = active


def _tick():
    from . import draw
    if draw._tool is not None and not draw._tool.done:
        # Placing points: nothing to record yet, and the scene is changing.
        return CHECK_INTERVAL
    try:
        _remember_active_wire()
        if live.transform_running():
            return CHECK_INTERVAL
        names = set()
        for obj in _live_wires():
            names.add(obj.name)
            if live.current_points(obj) is not None:
                # Still swinging; record once it has settled.
                _pending.pop(obj.name, None)
                continue
            sig = history_stack.signature(capture(obj))
            if sig == obj.get(SIG_KEY):
                _pending.pop(obj.name, None)
                continue
            if _pending.get(obj.name) == sig:
                record(obj)
                _pending.pop(obj.name, None)
            else:
                _pending[obj.name] = sig
        for name in list(_pending):
            if name not in names:
                del _pending[name]
    except ReferenceError:
        _pending.clear()
    except Exception:
        # Keep the timer alive (Blender drops a timer that raises).
        import traceback
        traceback.print_exc()
        _pending.clear()
    return CHECK_INTERVAL


class WIBLE_OT_history_jump(bpy.types.Operator):
    bl_idname = "wible.history_jump"
    bl_label = "Go to Step"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    index: bpy.props.IntProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, context, properties):
        from .ui import shown_wire
        obj = shown_wire(context)
        if obj is None:
            return "Go to this step"
        labels, current = steps(obj)
        i = properties.index
        if not 0 <= i < len(labels):
            return "Go to this step"
        where = " (current)" if i == current else ""
        return f"Step {i + 1}: {labels[i]}{where}"

    @classmethod
    def poll(cls, context):
        from .ui import shown_wire
        obj = shown_wire(context)
        return obj is not None and obj.type == "CURVE" and obj.wible.is_wire and not obj.wible.accepted

    def execute(self, context):
        from .ui import shown_wire
        obj = shown_wire(context)
        labels, _current = steps(obj)
        if not 0 <= self.index < len(labels):
            return {"CANCELLED"}
        restore(obj, self.index)
        return {"FINISHED"}


def copy_history(src, dst):
    for key in (HISTORY_KEY, INDEX_KEY, SIG_KEY):
        if key in src:
            dst[key] = src[key]


def register():
    bpy.utils.register_class(WIBLE_OT_history_jump)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=CHECK_INTERVAL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _pending.clear()
    bpy.utils.unregister_class(WIBLE_OT_history_jump)
