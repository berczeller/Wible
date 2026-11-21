# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep curves live: a timer runs rope physics for curves whose points moved,
and a depsgraph handler wakes them when a curve, its handles or its colliders
move. Nothing depends on the timeline."""

import math
import time

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector

from . import build, sim

FRAME = 1.0 / 60.0
MAX_DT = 1.0 / 20.0
# Seconds the ends must stay still before the wire starts easing to rest.
IDLE_BEFORE_SETTLE = 0.35
# Ease into the exact rest shape once motion drops below this (per meter of span).
SETTLE_SPEED = 0.02
# Give up on the swing after this long without the ends moving.
MAX_SWING = 6.0
BLEND_RATE = 10.0
# Never ease for longer than this; snap to the rest shape instead.
MAX_BLEND = 1.5
END_EPSILON = 1e-6
# Physics frames for the quick draped preview while a handle is dragged
# (Physics off); the full solve runs once the drag ends.
DRAG_PREVIEW_FRAMES = 30


class WireState:
    def __init__(self, points, uid=None):
        self.uid = uid  # the object's session_uid, to follow renames
        self.rope = sim.Rope(points)
        self.ends = None
        self.idle = 0.0
        self.phase = "SWING"  # SWING, BLEND or ASLEEP
        self.target = None  # exact rest shape, solved once the wire is idle
        self.blend_time = 0.0
        self.segments = sim.segment_lengths(points)


_states = {}
# Last world-space shape drawn for each wire, so a wire that starts moving
# swings from where it was on screen instead of jumping.
_last_drawn = {}
_last_tick = None
_ticking = False
_busy = False


def transform_running():
    """True while the user is dragging something with move, rotate or scale."""
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        for op in window.modal_operators:
            if op.bl_idname.startswith("TRANSFORM_OT"):
                return True
    return False


_transform_running = transform_running


def settle_at_rest(obj):
    """Record the curve as resting where it is now, so moves the
    depsgraph reports for this exact position don't start a swing."""
    if not build.uses_physics(obj):
        return
    count = build.point_total(obj)
    points = _last_drawn.get(obj.name)
    if points is None or len(points) != count:
        return
    state = WireState(points, obj.session_uid)
    state.ends = _positions(obj)
    state.phase = "ASLEEP"
    _states[obj.name] = state


def current_points(obj):
    state = _states.get(obj.name)
    if state is None or state.phase == "ASLEEP":
        return None
    return state.rope.points()


def remember(name, world_points):
    _last_drawn[name] = [tuple(p) for p in world_points]


def forget(obj):
    _states.pop(obj.name, None)


def clear():
    _states.clear()
    _last_drawn.clear()


def _positions(obj):
    """All point positions as a tuple of tuples (state.ends holds these)."""
    return tuple(tuple(p) for p in build.point_positions(obj))


def _moved(now, before):
    if before is None or len(now) != len(before):
        return True
    return any(abs(a - b) > END_EPSILON for p, q in zip(now, before) for a, b in zip(p, q))


def _ends_moved(obj, state):
    return _moved(_positions(obj), state.ends)


def _ensure_timer():
    global _ticking, _last_tick
    # Blender drops timers on file load and when they raise, so don't trust
    # the flag alone.
    if not _ticking or not bpy.app.timers.is_registered(_tick):
        _ticking = True
        _last_tick = time.perf_counter()
        bpy.app.timers.register(_tick, first_interval=0.0)


def wake(obj, force=True):
    """Start (or keep) simulating a wire from its current shape.

    With force=False (a move was reported) a sleeping wire whose ends did not
    actually move stays asleep. force=True is for setting changes: the wire
    swings into its new rest shape even though its ends stayed put.
    """
    if not build.uses_physics(obj):
        return
    count = build.point_total(obj)
    state = _states.get(obj.name)
    if state is not None and not force and state.rope.count == count:
        if state.phase != "ASLEEP":
            # Already simulating: the step itself notices real end movement.
            # Resetting here would let our own curve writes (which come back
            # as depsgraph updates) keep the wire awake forever.
            _ensure_timer()
            return
        if not _ends_moved(obj, state):
            return
    if state is None or state.rope.count != count:
        start = _last_drawn.get(obj.name)
        if start is None or len(start) != count:
            start = build.hanging_points(obj, count)
        if len(start) < 2:
            return
        state = WireState(start, obj.session_uid)
        _states[obj.name] = state
    if force:
        # Treat as moved so rest lengths are refreshed for the new settings.
        state.ends = None
    state.phase = "SWING"
    state.idle = 0.0
    state.target = None
    _ensure_timer()


def _refresh_rest_lengths(obj, state):
    """Segment lengths from the rest shape (no colliders) at the current points."""
    hang = build.hanging_points(obj, state.rope.count)
    if len(hang) == state.rope.count:
        state.segments = sim.segment_lengths(hang)
    return hang


def _pins_and_guides(obj, positions):
    """Rope indices pinned at anchors/supports, and guide pulls."""
    from . import solver
    count, where = build.sample_layout(obj)
    kinds = build.point_kinds(obj)
    strengths = build.point_strengths(obj)
    pins = {}
    guides = []
    for i, kind in enumerate(kinds):
        index = where.get(i)
        if index is None or i in (0, len(kinds) - 1):
            continue
        if kind == solver.GUIDE:
            guides.append((index, positions[i], strengths[i]))
        else:
            pins[index] = positions[i]
    return count, pins, guides


def _step_wire(obj, state, dt, transforming):
    s = obj.wible
    positions = _positions(obj)
    if len(positions) < 2:
        return False
    ends = (positions[0], positions[-1])

    moved = _moved(positions, state.ends)
    if moved:
        if state.ends is not None:
            build.keep_length(obj)
        build.ensure_handles(obj)
        state.ends = positions
        state.idle = 0.0
        state.target = None
        if state.phase != "SWING":
            state.phase = "SWING"
        _refresh_rest_lengths(obj, state)
    else:
        state.idle += dt

    rope = state.rope
    count, pins, guides = _pins_and_guides(obj, positions)
    if count != rope.count:
        # The sample count changed (colliders came near, or points were
        # added): carry the current shape over so the swing goes on.
        shape = [tuple(p) for p in build.resample([Vector(p) for p in rope.points()], count)]
        rope.reset(shape)
        hang = build.hanging_points(obj, count)
        state.segments = sim.segment_lengths(hang if len(hang) == count else shape)
    if state.phase == "SWING":
        nearest, cast = build.collision_funcs(obj)
        hang = None
        if state.idle > IDLE_BEFORE_SETTLE and nearest is None:
            hang = build.hanging_points(obj, rope.count)
            if len(hang) != rope.count:
                hang = None
        rope.step(
            dt, ends[0], ends[1], state.segments,
            damping=s.damping, stiffness=s.stiffness,
            nearest=nearest, cast=cast, gap=s.depth + s.drape_gap,
            target=hang, settle=2.0 if hang else 0.0,
            pins=pins, guides=guides,
        )
        span = max(sum(math.dist(a, b) for a, b in zip(positions, positions[1:])), 1.0)
        calm = rope.mean_speed(dt) < SETTLE_SPEED * span
        if not transforming and state.idle > IDLE_BEFORE_SETTLE and (calm or state.idle > MAX_SWING):
            target = build.rest_points(obj, start=rope.points())
            if len(target) != rope.count:
                # Point count changed under us; restart from the target.
                rope.reset(target)
            state.target = target
            state.phase = "BLEND"
            state.blend_time = 0.0

    if state.phase == "BLEND":
        nearest, _cast = build.collision_funcs(obj)
        # Target points sit exactly at the gap; a hair less keeps them from
        # being pushed off their own rest position.
        rope.blend(state.target, 1.0 - math.exp(-BLEND_RATE * dt), nearest, s.depth + s.drape_gap - 1e-4)
        state.blend_time += dt
        if rope.distance_to(state.target) < 1e-4 or state.blend_time > MAX_BLEND:
            rope.snap(state.target)
            state.phase = "ASLEEP"

    build.write_curve(obj, rope.points())
    if state.phase == "ASLEEP":
        if not transforming and build.bake_scale(obj):
            build.write_curve(obj, rope.points())
        build.keep_length(obj)
        # Store where the points are, so copies and recreated handles
        # start from here rather than from where they were placed.
        build.commit_points(obj)
        return False
    return True


def _find_renamed(name, state):
    """The object a state belonged to after a rename, re-keyed, or None."""
    if state.uid is None:
        return None
    for obj in bpy.data.objects:
        if obj.session_uid == state.uid:
            _states.pop(name, None)
            _states[obj.name] = state
            if name in _last_drawn:
                _last_drawn[obj.name] = _last_drawn.pop(name)
            return obj
    return None


def _tick():
    global _last_tick, _ticking, _busy
    now = time.perf_counter()
    dt = min(max(now - (_last_tick or now), 0.0), MAX_DT)
    _last_tick = now
    if dt <= 0.0:
        dt = FRAME

    transforming = _transform_running()
    awake = False
    _busy = True
    try:
        for name, state in list(_states.items()):
            if state.phase == "ASLEEP":
                continue
            obj = bpy.data.objects.get(name)
            if obj is None:
                obj = _find_renamed(name, state)
            if obj is None or not build.uses_physics(obj):
                _states.pop(name, None)
                if obj is not None and obj.type == "CURVE" and obj.wible.is_wire:
                    build.write_curve(obj, build.rest_points(obj))
                continue
            try:
                if _step_wire(obj, state, dt, transforming):
                    awake = True
            except ReferenceError:
                _states.pop(obj.name, None)
            except Exception:
                # One curve's error must not stop the others (Blender drops
                # a timer that raises).
                import traceback
                traceback.print_exc()
                _states.pop(obj.name, None)
    finally:
        _busy = False

    if awake:
        return FRAME
    _ticking = False
    return None


def _wire_for(ob):
    """The live wire an updated object belongs to: itself or its handle's parent."""
    if ob is None:
        return None
    if ob.wible.is_wire:
        return ob
    if ob.get(build.HANDLE_KEY) is not None and ob.parent is not None and ob.parent.wible.is_wire:
        return ob.parent
    return None


@persistent
def on_depsgraph_update(scene, depsgraph):
    if _busy:
        return

    wires = {}
    changed_colliders = set()
    for update in depsgraph.updates:
        ob = update.id
        if not isinstance(ob, bpy.types.Object):
            continue
        original = ob.original if ob.original is not None else ob
        if update.is_updated_geometry:
            build.clear_collider_cache([original.name])
        if not (update.is_updated_transform or update.is_updated_geometry):
            continue
        wire = _wire_for(original)
        if wire is not None:
            if update.is_updated_transform:
                wires[wire.name] = wire
        else:
            changed_colliders.add(original.name)

    # Curves whose colliders moved or changed shape. Their own points
    # didn't move, so they must be re-solved even if asleep (force).
    pushed = set()
    if changed_colliders:
        changed = [bpy.data.objects.get(name) for name in changed_colliders]
        changed = [o for o in changed if o is not None and o.type in build.COLLIDER_TYPES
                   and o.get(build.HANDLE_KEY) is None]
        for wire in scene.objects if changed else ():
            s = wire.wible
            if wire.type != "CURVE" or not s.is_wire or s.accepted or not (s.gravity and s.drape):
                continue
            if wire.name in wires:
                continue
            if s.drape_collection is not None:
                hit = any(o.name in changed_colliders for o in s.drape_collection.all_objects)
            else:
                bounds = build.wire_bounds(wire)
                hit = bounds is not None and any(build._overlaps(bounds, build._world_box(o)) for o in changed)
            if hit:
                wires[wire.name] = wire
                pushed.add(wire.name)

    for wire in wires.values():
        if wire.type != "CURVE" or wire.wible.accepted:
            continue
        if build.uses_physics(wire):
            wake(wire, force=wire.name in pushed)
        elif wire.wible.gravity or wire.wible.is_wire:
            build.keep_length(wire)
            if transform_running() and wire.wible.gravity and wire.wible.drape:
                # A quick settle from the last shape while dragging; the
                # full one runs when the drag ends.
                start = _last_drawn.get(wire.name)
                build.write_curve(wire, build.rest_points(
                    wire, depsgraph, start=start, max_frames=DRAG_PREVIEW_FRAMES))
                _settle_later(wire)
            else:
                build.write_curve(wire, build.rest_points(wire, depsgraph))
                build.commit_points(wire)


_unsettled = set()


def _settle_later(wire):
    _unsettled.add(wire.name)
    if not bpy.app.timers.is_registered(_settle_unsettled):
        bpy.app.timers.register(_settle_unsettled, first_interval=0.1)


def _settle_unsettled():
    """Full rest solve for curves dragged with Physics off, once the drag ends."""
    global _busy
    if transform_running():
        return 0.1
    names = list(_unsettled)
    _unsettled.clear()
    _busy = True
    try:
        for name in names:
            wire = bpy.data.objects.get(name)
            if wire is None or wire.type != "CURVE" or not wire.wible.is_wire or wire.wible.accepted:
                continue
            try:
                build.write_curve(wire, build.rest_points(wire))
                build.commit_points(wire)
            except Exception:
                import traceback
                traceback.print_exc()
    finally:
        _busy = False
    return None


def _cleanup_handles():
    """Remove end handles whose wire was deleted or accepted."""
    for ob in list(bpy.data.objects):
        if ob.get(build.HANDLE_KEY) is None:
            continue
        parent = ob.parent
        if parent is None or not parent.wible.is_wire or parent.wible.accepted:
            if ob.users_scene:
                bpy.data.objects.remove(ob)
    return 2.0


@persistent
def on_load(_dummy):
    global _ticking
    build.clear_collider_cache()
    clear()
    _unsettled.clear()
    _ticking = False  # Blender dropped the timer with the old file


@persistent
def on_undo(_scene, _dummy=None):
    # Undo restores data but not our in-memory ropes or collider trees.
    # Curves that were moving may have been saved mid-swing: settle them
    # into their rest shape.
    moving = [name for name, state in _states.items() if state.phase != "ASLEEP"]
    build.clear_collider_cache()
    clear()
    if moving:
        _after_undo.update(moving)
        if not bpy.app.timers.is_registered(_settle_after_undo):
            bpy.app.timers.register(_settle_after_undo, first_interval=0.0)


_after_undo = set()


def _settle_after_undo():
    names = list(_after_undo)
    _after_undo.clear()
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "CURVE" or not obj.wible.is_wire or obj.wible.accepted:
            continue
        try:
            build.rebuild(obj)
            settle_at_rest(obj)
        except Exception:
            import traceback
            traceback.print_exc()
    return None


def register():
    bpy.app.handlers.depsgraph_update_post.append(on_depsgraph_update)
    bpy.app.handlers.load_post.append(on_load)
    bpy.app.handlers.undo_post.append(on_undo)
    bpy.app.handlers.redo_post.append(on_undo)
    if not bpy.app.timers.is_registered(_cleanup_handles):
        bpy.app.timers.register(_cleanup_handles, first_interval=2.0, persistent=True)


def unregister():
    global _ticking
    for handlers, fn in (
        (bpy.app.handlers.depsgraph_update_post, on_depsgraph_update),
        (bpy.app.handlers.load_post, on_load),
        (bpy.app.handlers.undo_post, on_undo),
        (bpy.app.handlers.redo_post, on_undo),
    ):
        if fn in handlers:
            handlers.remove(fn)
    for fn in (_tick, _cleanup_handles, _settle_unsettled, _settle_after_undo):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    _ticking = False
    clear()
    build.clear_collider_cache()
